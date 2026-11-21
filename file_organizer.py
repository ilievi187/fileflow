#!/usr/bin/env python3
"""
file_organizer.py — sort a messy folder into clean, categorized folders.

Classifies files by extension and name hints, can pull project files out by
name or regex, shards huge categories by first letter, and moves (or copies)
everything into a tidy tree.

Design rules that the tests enforce:

* **nothing is ever overwritten** — destination collisions are resolved under a
  lock, so two threads with the same filename cannot race into one path;
* **runs are idempotent** — a file already sitting at its computed destination
  is counted as "already sorted" and left alone, so running twice never adds
  digest suffixes;
* **stop_event is honoured** — no new work is queued or started once it is set;
* **project files go to Projects/<name>/** and log-looking files go to Logs,
  even when their extension is .txt.

CLI:
  python file_organizer.py /path/to/messy --dry-run
  python file_organizer.py /path/to/messy --dest /path/clean --max-workers 8
  python file_organizer.py /path/to/messy --projects siteA,siteB --json-out stats.json
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------
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
        "m2ts", "ts", "ogv", "3gp", "vob",
    },
    "Audio": {
        "mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus", "midi", "aiff",
        "aif", "oga",
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
LOG_HINT_EXTS = {"txt", "csv", "tsv", "dat", "out"}
LOG_NAME_HINTS = (
    "log", "err", "error", "debug", "trace", "stacktrace", "crash", "boot",
    "access", "audit", "kern", "daemon",
)

PROJECTS_DIR = "Projects"


def classify(name: str) -> str:
    """Return the category for a filename based on extension + name heuristics."""
    ext = name.rpartition(".")[2].lower() if "." in name else ""
    low = name.lower()

    # logs win over the generic document/data tables
    if ext in LOG_EXTS:
        return "Logs"
    if ext in LOG_HINT_EXTS and any(hint in low for hint in LOG_NAME_HINTS):
        return "Logs"

    if ext:
        for category, extensions in EXT.items():
            if ext in extensions:
                return category
    return "Other"


def shard_key(name: str) -> str:
    """First alnum char (uppercased) so big categories spread across folders."""
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return "_"


def detect_project(rel_parts, project_names, project_regex):
    """Return a project label if any path component belongs to one, else None."""
    text = " ".join(part.lower() for part in rel_parts)
    for name in project_names or []:
        if name.lower() in text:
            return name
    if project_regex:
        match = re.search(project_regex, " ".join(rel_parts))
        if match:
            return match.group(0)
    return None


@dataclass
class Task:
    src: Path
    rel: Path
    category: str
    project: str | None
    target_base: Path


@dataclass
class Stats:
    total: int = 0
    moved: int = 0
    copied: int = 0
    failed: int = 0
    skipped_size: int = 0
    skipped_sorted: int = 0
    stopped: bool = False
    by_category: dict = field(default_factory=dict)
    by_project: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    def as_dict(self, **extra) -> dict:
        data = {
            "total": self.total,
            "moved": self.moved,
            "copied": self.copied,
            "failed": self.failed,
            "skipped_size": self.skipped_size,
            "skipped_sorted": self.skipped_sorted,
            "stopped": self.stopped,
            "by_category": dict(sorted(self.by_category.items())),
            "by_project": dict(sorted(self.by_project.items())),
            "errors": self.errors[:50],
        }
        data.update(extra)
        return data


class Allocator:
    """Hands out destination paths, never the same one twice, never over an
    existing file. The lock closes the check-then-move race between threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reserved: set[Path] = set()

    def allocate(self, base: Path, name: str, hint: str) -> Path:
        with self._lock:
            candidate = base / name
            if not candidate.exists() and candidate not in self._reserved:
                self._reserved.add(candidate)
                return candidate

            stem, ext = os.path.splitext(name)
            digest = hashlib.md5(hint.encode("utf-8", "replace")).hexdigest()[:8]
            counter = 1
            while True:
                suffix = f"_{digest}" if counter == 1 else f"_{digest}-{counter}"
                candidate = base / f"{stem}{suffix}{ext}"
                if not candidate.exists() and candidate not in self._reserved:
                    self._reserved.add(candidate)
                    return candidate
                counter += 1
                if counter > 1000:           # pathological; give up cleanly
                    raise RuntimeError(f"cannot allocate a free name for {name}")


def _target_base(task: Task) -> Path:
    return task.target_base


def plan_task(path: Path, rel: Path, dest: Path, *, shard: bool, keep_tree: bool,
              project_names, project_regex) -> Task:
    name = path.name
    category = classify(name)
    project = detect_project(rel.parts, project_names, project_regex)

    if project:
        base = dest / PROJECTS_DIR / project
    else:
        base = dest / category

    if keep_tree:
        parts = list(rel.parent.parts)
        if parts and (parts[0] == category or parts[0] == PROJECTS_DIR):
            parts = parts[1:]
        if project and parts and parts[0] == project:
            parts = parts[1:]
        if parts:
            base = base.joinpath(*parts)
    elif shard and not project:
        base = base / shard_key(name)
    return Task(src=path, rel=rel, category=category, project=project, target_base=base)


def organize(source, dest=None, *, dry_run=False, copy=False, max_workers=8,
             on_event=None, stop_event=None, project_names=None,
             project_regex=None, shard=True, keep_tree=False, min_size=0,
             per_file_log=None, on_progress=None):
    """Organize `source` into `dest` (defaults to in-place).

    Returns a stats dict. `on_event(line)` receives progress text; `on_progress`
    is called as (done, total) for progress bars; `stop_event` aborts the run;
    `per_file_log` is an open text handle for JSONL entries.
    """
    started = time.monotonic()
    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"source is not a folder: {source}")
    dest = Path(dest).expanduser().resolve() if dest else source

    if isinstance(project_names, str):
        project_names = [p.strip() for p in project_names.split(",") if p.strip()]
    stop_event = stop_event or threading.Event()

    def emit(line: str) -> None:
        if on_event:
            on_event(line)

    log_lock = threading.Lock()

    def log_entry(entry: dict) -> None:
        if per_file_log is None:
            return
        with log_lock:
            per_file_log.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---- scan ----------------------------------------------------------
    emit(f"Scanning {source} …")
    files: list[tuple[Path, Path]] = []
    skip_root = None
    if dest != source and dest.is_relative_to(source):
        skip_root = dest                       # never re-scan our own output

    for root, dirs, names in os.walk(source):
        root_path = Path(root)
        if skip_root is not None and root_path == skip_root:
            dirs[:] = []
            continue
        for name in names:
            path = root_path / name
            files.append((path, path.relative_to(source)))

    stats = Stats(total=len(files))
    emit(f"Found {len(files):,} files")

    allocator = Allocator()
    executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)))

    def do(task: Task) -> tuple[str, Task, str | None, Path | None]:
        if stop_event.is_set():
            return ("stopped", task, None, None)
        try:
            size = task.src.stat().st_size
        except OSError as exc:
            return ("fail", task, str(exc), None)
        if min_size and size < min_size:
            return ("skip_size", task, None, None)

        natural = task.target_base / task.src.name
        if natural == task.src:                # already in the right place
            return ("sorted", task, None, natural)

        target = allocator.allocate(task.target_base, task.src.name, str(task.src))
        if dry_run:
            return ("plan", task, None, target)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copy2(task.src, target, follow_symlinks=False)
                kind = "copy"
            else:
                shutil.move(str(task.src), str(target))
                kind = "move"
            return (kind, task, None, target)
        except Exception as exc:               # keep going on single failures
            return ("fail", task, str(exc), target)

    # ---- execute -------------------------------------------------------
    progress_step = max(1, len(files) // 100)
    done = 0

    def record(kind, task, error, target, *, quiet=False) -> None:
        nonlocal done
        done += 1
        if kind in ("plan", "move", "copy", "sorted"):
            stats.by_category[task.category] = stats.by_category.get(task.category, 0) + 1
            if task.project:
                stats.by_project[task.project] = stats.by_project.get(task.project, 0) + 1
        if kind == "move":
            stats.moved += 1
        elif kind == "copy":
            stats.copied += 1
        elif kind == "sorted":
            stats.skipped_sorted += 1
        elif kind == "skip_size":
            stats.skipped_size += 1
        elif kind == "fail":
            stats.failed += 1
            if len(stats.errors) < 20:
                stats.errors.append(f"{task.src}: {error}")
            if not quiet:
                emit(f"[fail] {task.src.name}: {error}")
        elif kind == "stopped":
            stats.stopped = True

        if kind != "stopped":
            log_entry({
                "src": str(task.src),
                "dst": str(target) if target else None,
                "category": task.category,
                "project": task.project,
                "action": kind,
                "error": error,
            })
        if quiet:
            return
        if on_progress is not None:
            on_progress(done, len(files))
        if done % progress_step == 0 or done == len(files):
            emit(f"Processed {done:,} / {len(files):,} ({100 * done // max(1, len(files))}%)")

    try:
        futures = []
        for path, rel in files:
            if stop_event.is_set():
                break
            task = plan_task(path, rel, dest, shard=shard, keep_tree=keep_tree,
                             project_names=project_names, project_regex=project_regex)
            futures.append(executor.submit(do, task))

        counted = set()
        for future in futures:
            kind, task, error, target = future.result()
            record(kind, task, error, target)
            counted.add(future)
            if stop_event.is_set():
                stats.stopped = True
                break

        # On stop, tasks that were already running still finish — count them so
        # the numbers match what is actually on disk. Queued tasks are cancelled.
        if stats.stopped:
            for future in futures:
                if future in counted or future.cancelled():
                    continue
                try:
                    kind, task, error, target = future.result(timeout=5)
                except Exception:
                    continue
                if kind != "stopped":
                    record(kind, task, error, target, quiet=True)
    finally:
        if stop_event.is_set():
            stats.stopped = True
        executor.shutdown(wait=True, cancel_futures=True)

    duration = round(time.monotonic() - started, 2)
    emit(f"Done in {duration}s — moved {stats.moved}, copied {stats.copied}, "
         f"already sorted {stats.skipped_sorted}, failed {stats.failed}")
    return stats.as_dict(source=str(source), dest=str(dest), dry_run=dry_run,
                         copy=copy, duration=duration)


def summarize(stats: dict) -> str:
    lines = [
        "=" * 52,
        "DRY-RUN — nothing was moved" if stats.get("dry_run") else "",
        f"Source : {stats['source']}",
        f"Dest   : {stats['dest']}",
        f"Total  : {stats['total']:,}",
        f"Moved  : {stats['moved']:,}   Copied: {stats['copied']:,}   "
        f"Failed: {stats['failed']:,}",
        f"Already sorted: {stats.get('skipped_sorted', 0):,}   "
        f"Skipped by size: {stats.get('skipped_size', 0):,}",
    ]
    if stats.get("stopped"):
        lines.append("Stopped by user")
    if stats.get("by_project"):
        joined = ", ".join(f"{k}({v})" for k, v in sorted(stats["by_project"].items()))
        lines.append(f"Projects: {joined}")
    lines.append("By category:")
    for category in CATEGORIES:
        count = stats["by_category"].get(category, 0)
        if count:
            lines.append(f"  {category:<14} {count:>10,}")
    lines.append("=" * 52)
    return "\n".join(line for line in lines if line != "")


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Sort a messy folder into clean, categorized folders.")
    parser.add_argument("source", help="folder full of mixed files to organize")
    parser.add_argument("--dest", default=None,
                        help="destination folder (default: in-place)")
    parser.add_argument("--copy", action="store_true", help="copy instead of move")
    parser.add_argument("--max-workers", type=int, default=8, help="worker threads")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the plan without moving anything")
    parser.add_argument("--no-shard", action="store_true",
                        help="one folder per category instead of A/B/C shards")
    parser.add_argument("--keep-tree", action="store_true",
                        help="keep the source subfolder structure inside each category")
    parser.add_argument("--projects", default=None,
                        help="comma-separated project names to pull out")
    parser.add_argument("--project-regex", default=None,
                        help="regex that marks a file as a project match")
    parser.add_argument("--min-size-bytes", type=int, default=0,
                        help="skip files smaller than N bytes")
    parser.add_argument("--log", default=None,
                        help="write a JSONL per-file log (src, dst, action) here")
    parser.add_argument("--json-out", default=None, help="write the stats JSON here")
    args = parser.parse_args()

    log_handle = open(args.log, "w", encoding="utf-8") if args.log else None
    try:
        stats = organize(
            args.source, dest=args.dest, dry_run=args.dry_run, copy=args.copy,
            max_workers=args.max_workers, project_names=args.projects,
            project_regex=args.project_regex, shard=not args.no_shard,
            keep_tree=args.keep_tree, min_size=args.min_size_bytes,
            on_event=lambda line: print(line, flush=True),
            per_file_log=log_handle,
        )
    finally:
        if log_handle:
            log_handle.close()

    print(summarize(stats))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(f"\nstats -> {args.json_out}")
    if args.log:
        print(f"per-file log -> {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
