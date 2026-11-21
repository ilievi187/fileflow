#!/usr/bin/env python3
"""
file_organizer.py - sort a messy folder into clean, categorized folders.

Given a folder full of mixed files (even ~1,000,000), it classifies each file
by extension / name heuristics into categories (Documents, Images, Logs, Code,
...), separates project files out, analyzes the leftovers, then creates the
destination structure and moves (or copies) every file into the right place so
everything ends up ordered.

CLI:
  python file_organizer.py /path/to/messy --dest /path/org --max-workers 8
  python file_organizer.py /path/to/messy --dry-run
  python file_organizer.py /path/to/messy --projects siteA,siteB --project-regex 'report_.*'

Library:
  from file_organizer import organize
  stats = organize("/messy", dest="/org", on_event=print, stop_event=Event())
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------
# Categories are checked in order; the first match wins.
EXT = {
    "Documents": {
        "pdf", "doc", "docx", "odt", "rtf", "txt", "md", "markdown", "epub",
        "mobi", "djvu", "ppt", "pptx", "odp", "fods", "html", "htm", "mhtml",
        "pages", "wpf", "docm", "potx", "otp",
    },
    "Images": {
        "jpg", "jpeg", "png", "gif", "bmp", "svg", "webp", "tiff", "tif", "raw",
        "cr2", "nef", "arw", "orw", "heic", "heif", "psd", "ai", "ico", "wmf",
        "emf", "apng", "jxl",
    },
    "Videos": {
        "mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "mpg", "mpeg", "wmv",
        "m2ts", "ts", "ogv", "3gp", "vob", "mov",
    },
    "Audio": {
        "mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus", "midi", "aiff",
        "aif", "wma", "oga",
    },
    "Archives": {
        "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "tgz", "tbz", "lz",
        "arc", "cab", "iso", "img",
    },
    "Code": {
        "py", "js", "ts", "tsx", "jsx", "mjs", "c", "cc", "cpp", "h", "hpp", "hh",
        "cs", "go", "rb", "php", "sh", "bash", "zsh", "pl", "pm", "r", "swift",
        "kt", "scala", "m", "mm", "lua", "rs", "dart", "vb", "java", "clj",
        "ex", "exs", "sql", "html", "css",
    },
    "Data": {
        "csv", "tsv", "sql", "db", "sqlite", "sqlite3", "parquet", "hdf5", "h5",
        "pickle", "pkl", "npy", "npz", "json", "jsonl", "xml", "yaml", "yml",
        "toml", "ini", "cfg", "conf", "lock", "dbf", "mat", "bin",
    },
    "Fonts": {"ttf", "otf", "woff", "woff2", "eot"},
    "Executables": {
        "exe", "dll", "so", "dylib", "o", "a", "app", "apk", "msi", "run",
        "class", "jar", "com", "bat", "cmd", "ps1", "elf", "war", "bundle", "dmg",
    },
}

CATEGORIES = [
    "Documents", "Images", "Videos", "Audio", "Archives",
    "Code", "Data", "Fonts", "Executables", "Logs", "Other",
]

LOG_EXTS = {"log", "out", "trace", "syslog", "journal", "lgi", "esl"}
LOG_NAME_HINTS = (
    "log", "err", "error", "debug", "trace", "stacktrace", "crash", "boot",
    "access", "audit", "kern", "daemon",
)


def classify(name):
    """Return the category for a filename based on extension + name heuristics."""
    _, _, ext = name.rpartition(".")
    ext = ext.lower().lstrip(".")
    if ext:
        for cat, exts in EXT.items():
            if ext in exts:
                return cat
    low = name.lower()
    if ext in {"txt", "csv", "tsv", "dat"} and any(h in low for h in LOG_NAME_HINTS):
        return "Logs"
    if ext in LOG_EXTS:
        return "Logs"
    return "Other"


def shard_key(name):
    """First alnum char (uppercased) so big categories spread across folders."""
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return "_"


def detect_project(name, parent_name, project_names, project_regex):
    """Return a project label if the file belongs to one, else None."""
    candidates = [name]
    if parent_name:
        candidates.append(parent_name)
    text = " ".join(c.lower() for c in candidates)
    if project_names:
        for p in project_names:
            if p.lower() in text:
                return p
    if project_regex:
        m = re.search(project_regex, text)
        if m:
            return m.group(0)
    return None


def build_dest(dest, category, name, abspath, shard, keep_tree):
    """Compute the destination path, de-duplicating on collision."""
    base = dest if keep_tree else dest / category / shard_key(name)
    path = base / name
    if path.exists():
        stem, _sep, ext = name.rpartition(".")
        stem = stem[:60]
        safe = stem if stem else "file"
        digest = hashlib.md5(abspath.encode("utf-8", "replace")).hexdigest()[:8]
        ext = "." + ext if ext else ""
        path = base / f"{safe}_{digest}{ext}"
    return path


def organize(source, dest=None, *, dry_run=False, copy=False, max_workers=8,
             on_event=None, stop_event=None, project_names=None,
             project_regex=None, shard=True, keep_tree=False, min_size=0):
    """
    Organize `source` into `dest` (defaults to in-place).

    Returns a stats dict. Pass on_event(line) for progress logging and an
    optional stop_event to abort mid-run.
    """
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError(f"Source is not a folder: {source}")
    dest = Path(dest).resolve() if dest else source
    if stop_event is None:
        stop_event = threading.Event()

    def ev(line):
        if on_event:
            on_event(line)

    if not project_names:
        project_names = []
    elif isinstance(project_names, str):
        project_names = [p.strip() for p in project_names.split(",") if p.strip()]

    ev(f"Scanning {source} ...")
    files = []
    for root, _dirs, fnames in os.walk(source):
        for fn in fnames:
            files.append(os.path.join(root, fn))
    total = len(files)
    ev(f"Found {total:,} files")

    stats = {
        "total": total,
        "moved": 0, "copied": 0, "failed": 0, "skipped_size": 0,
        "by_category": {}, "by_project": {},
        "dry_run": dry_run, "copy": copy,
        "source": str(source), "dest": str(dest),
    }

    def process_one(path):
        try:
            st = os.stat(path)
        except OSError:
            return ("skip", None, None)
        if st.st_size < min_size:
            return ("skip", None, None)
        name = os.path.basename(path)
        parent = os.path.basename(os.path.dirname(path))
        cat = classify(name)
        project = detect_project(name, parent, project_names, project_regex)
        dest_path = build_dest(dest, cat, name, path, shard, keep_tree)
        if dry_run:
            return ("plan", cat, project)
        try:
            os.makedirs(dest_path.parent, exist_ok=True)
            if copy:
                shutil.copy2(path, dest_path, follow_symlinks=False)
                return ("copy", cat, project)
            shutil.move(path, dest_path)
            return ("move", cat, project)
        except Exception as e:  # keep going on individual failures
            return ("fail", cat, project, str(e))

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(process_one, p) for p in files]
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            kind = res[0]
            if kind == "plan":
                stats["by_category"][res[1]] = stats["by_category"].get(res[1], 0) + 1
                if res[2]:
                    stats["by_project"][res[2]] = stats["by_project"].get(res[2], 0) + 1
            elif kind in ("move", "copy"):
                stats["moved" if kind == "move" else "copied"] += 1
                cat = res[1]
                stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
                if res[2]:
                    stats["by_project"][res[2]] = stats["by_project"].get(res[2], 0) + 1
            elif kind == "fail":
                stats["failed"] += 1
                if done % max(1, total // 50) == 0 or done == total:
                    ev(f"Processed {done:,} / {total:,} ({100 * done // total}%)")
            # "skip" -> nothing to report
    ev(f"Processed {done:,} / {total:,} ({100 * done // total}%)")
    return stats


def summarize(stats):
    lines = [
        "=" * 48,
        "DRY-RUN — no files moved" if stats["dry_run"] else "",
        f"Source : {stats['source']}",
        f"Dest   : {stats['dest']}",
        f"Total  : {stats['total']:,}",
        f"Moved  : {stats['moved']:,}  (copied={stats['copied']:,})",
        f"Failed : {stats['failed']:,}",
    ]
    if stats["by_project"]:
        pj = ", ".join(f"{k}({v})" for k, v in sorted(stats["by_project"].items()))
        lines.append(f"Projects: {pj}")
    lines.append("By category:")
    for cat in CATEGORIES:
        n = stats["by_category"].get(cat, 0)
        if n:
            lines.append(f"  {cat:<14} {n:>10,}")
    lines.append("=" * 48)
    return "\n".join(lines)


def _main():
    p = argparse.ArgumentParser(
        description="Sort a messy folder into clean, categorized folders.")
    p.add_argument("source", help="Folder full of mixed files to organize")
    p.add_argument("--dest", default=None,
                   help="Destination folder (default: in-place)")
    p.add_argument("--copy", action="store_true", help="Copy instead of move")
    p.add_argument("--max-workers", type=int, default=8, help="Worker threads")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the plan without moving anything")
    p.add_argument("--no-shard", action="store_true",
                   help="Put everything in one folder per category")
    p.add_argument("--keep-tree", action="store_true",
                   help="Preserve relative subfolder structure")
    p.add_argument("--projects", default=None,
                   help="Comma-separated project names to sort out")
    p.add_argument("--project-regex", default=None,
                   help="Regex that marks a file as a project match")
    p.add_argument("--min-size-bytes", type=int, default=0,
                   help="Skip files smaller than N bytes")
    p.add_argument("--log", default=None, help="Write a per-file log to this path")
    p.add_argument("--json-out", default=None, help="Write stats JSON to this path")
    args = p.parse_args()

    stats = organize(
        args.source, dest=args.dest, dry_run=args.dry_run, copy=args.copy,
        max_workers=args.max_workers, project_names=args.projects,
        project_regex=args.project_regex, shard=not args.no_shard,
        keep_tree=args.keep_tree, min_size=args.min_size_bytes,
    )
    print(summarize(stats))

    if args.log:
        try:
            with open(args.log, "w") as f:
                json.dump(stats, f, indent=2)
        except Exception:
            pass
    if args.json_out:
        import json
        try:
            with open(args.json_out, "w") as f:
                json.dump(stats, f, indent=2)
        except Exception:
            pass

    print(f"\nWrote stats -> {args.json_out or args.log or '(stdout only)'}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
