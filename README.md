# FileFlow

Sort a messy folder into clean, categorized folders — a small **library + CLI + Qt GUI**
for tidying downloads, screenshots, scans and dumps.

![FileFlow](docs/screenshot.png)

Drop a folder on the window, press Start, watch it sort. Nothing leaves your
machine.

## What it does

- **Categorises** by extension and name hints: Documents, Images, Videos, Audio,
  Archives, Code, Data, Fonts, Executables, Logs, Other.
- **Projects** — pull out your own projects by name or regex into `Projects/<name>/`.
- **Sharding** — split big categories into `A/`, `B/`, `C/` so no folder ends up
  with a million files.
- **Keeps the tree** optionally, so `sub/deep/file.pdf` stays under
  `Documents/sub/deep/`.
- **Copy or move**, **dry-run**, minimum file size, configurable workers.

## Guarantees (all covered by tests)

| Guarantee | Why it matters |
|---|---|
| **Nothing is overwritten** | destinations are allocated under a lock; a collision becomes `name_ab12cd34.pdf`, never a silent replace |
| **Running twice is safe** | a file already at its computed destination counts as "already sorted" and is left alone — no digest suffixes piling up |
| **Stop actually stops** | `stop_event` is checked before queueing and inside every task; in-flight tasks are still counted so the numbers match the disk |
| **No shell** | nothing is ever passed through a shell |
| **Your output isn't re-scanned** | if `--dest` is inside the source, that subtree is skipped |

## Install

```bash
git clone https://github.com/ilievi187/fileflow.git
cd fileflow
```

- Python 3.9+
- GUI only: `pyside6` (`sudo pacman -S pyside6`)

## GUI

```bash
./run.sh            # or: python3 fileflow_qt.py
```

- drop a folder anywhere on the window (or click the box)
- destination is optional — empty means sort in place
- live progress bar, counters for moved / copied / already sorted / failed
- Stop button, `Ctrl+Enter` to start, `Esc` to stop
- "open destination" when it finishes
- `fileflow.desktop` + `icons/fileflow.svg` for your app launcher

## CLI

```bash
# preview only
python3 file_organizer.py ~/Downloads --dry-run

# sort into a separate tree, pull out two projects
python3 file_organizer.py ~/messy --dest ~/sorted \
    --projects siteA,projectX --project-regex 'report_|_backup' --max-workers 8

# copy instead of move, keep subfolders
python3 file_organizer.py ~/messy --copy --keep-tree

# per-file JSONL log + stats JSON
python3 file_organizer.py ~/messy --log run.jsonl --json-out stats.json
```

| Flag | Meaning |
|---|---|
| `--dest DIR` | destination (default: in place) |
| `--copy` | copy instead of move |
| `--dry-run` | plan only |
| `--no-shard` | one folder per category |
| `--keep-tree` | keep source subfolders inside each category |
| `--projects a,b` | project names to pull into `Projects/` |
| `--project-regex RE` | regex alternative |
| `--min-size-bytes N` | skip small files |
| `--max-workers N` | worker threads (default 8) |
| `--log FILE` | JSONL per-file log (`src`, `dst`, `action`) |
| `--json-out FILE` | stats JSON |

## Library

```python
from file_organizer import organize

stats = organize(
    "~/messy",
    dest="~/sorted",
    copy=False,
    projects=None,
    project_names="siteA",
    keep_tree=False,
    on_event=print,               # progress text
    on_progress=lambda done, total: ...,  # progress bar
)
print(stats["moved"], stats["failed"])
```

`on_event` gets human-readable lines, `on_progress` gets `(done, total)`, and
`stop_event=threading.Event()` cancels a run.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

15 tests covering collisions (including 25 files with the same name), idempotency,
project moves, keep-tree stability, stop behaviour, dry-run, min-size skips,
per-file logs and the logs classification fix.

Headless GUI check:

```bash
QT_QPA_PLATFORM=offscreen python3 fileflow_qt.py --smoke
```

## Layout

```
file_organizer.py    # library + CLI (classification, planner, allocator, runner)
fileflow_qt.py       # PySide6 GUI
tests/               # unittest suite
run.sh               # GUI launcher
fileflow.desktop     # app launcher entry
icons/fileflow.svg
```

## Notes

- Files are moved with `shutil.move`; cross-device moves fall back to copy+delete,
  which is the standard behaviour.
- Symlinks are moved/copied as links, not followed.
- `.gitignore` excludes `__pycache__`, logs and the local report folder.

## Licence

MIT — see [LICENSE](LICENSE).
