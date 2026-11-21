# FileFlow

Sort a messy folder into clean, categorized folders — a small **library + CLI + tkinter GUI** for tidying up downloads, scans, and dumps.

FileFlow classifies files by extension, name hints, and project rules, shuffles them into category folders (optionally split by first letter for huge folders), handles filename collisions by adding a short digest suffix, and reports a JSON stats summary.

> Drop it in a folder of 100,000+ files and it sorts them into sensible subfolders — no manual dragging.

## Features

- **Auto-categorization** by extension + name hints (Documents, Images, Videos, Audio, Code, Data, Logs, Fonts, Archives, Other…).
- **Projects** — pull out your own projects by name or a regex.
- **Sharding** — split a category into folders by first letter so no single folder gets 1,000,000 files.
- **Collision-safe** — duplicate filenames get a `name_digest.ext` suffix instead of being overwritten.
- **Copy or move**, **dry-run**, preserve subfolders, configurable worker threads.
- **Library + CLI + GUI**: import as a module, use from the command line, or run the packaged tkinter GUI with live log.

## Install

```bash
git clone https://github.com/ilievi187/fileflow.git
cd fileflow
```

No third-party dependencies — just Python 3.8+ and (for the GUI) `python3-tk`.

## CLI usage

```bash
# Just preview the plan (nothing moves)
python3 file_organizer.py /path/to/messy --dry-run

# Actually sort into a clean folder
python3 file_organizer.py /path/to/messy --dest /path/clean \
  --projects mysite,projectX --project-regex 'report_|_backup' \
  --max-workers 8
```

Common flags:

| flag | what it does |
|------|--------------|
| `--dest DEST` | destination folder (default: in-place) |
| `--copy` | copy instead of move |
| `--max-workers N` | worker threads |
| `--dry-run` | show the plan without moving anything |
| `--no-shard` | put everything in one folder per category |
| `--keep-tree` | preserve relative subfolder structure |
| `--projects NAME` | comma-separated project names to sort out |
| `--project-regex RX` | regex that marks a file as a project match |
| `--min-size-bytes N` | skip files smaller than N bytes |
| `--log LOG` | write a per-file log to this path |
| `--json-out JSON` | write stats JSON to this path |

## Library usage

```python
from file_organizer import organize
from threading import Event

stats = organize(
    "/path/to/messy",
    dest="/path/clean",
    on_event=print,
    stop_event=Event(),
)
print(stats)
```

## GUI usage

```bash
python3 file_organizer_gui.py
```

A themed tkinter window with fields for source/dest, options (Copy, Shard, Preserve subfolders, Dry-run, Projects), a live log pane, and a Stop button.

## Example

```
$ python3 file_organizer.py ~/Downloads --dest ~/Library --projects mysite,projectX --project-regex 'report_|_backup'

Source : /home/user/Downloads
Dest   : /home/user/Library
Total  : 108
Moved  : 108  (copied=0)
Failed : 0
Projects: _backup(1), mysite(1), projectX(1), report_(2)
By category:
  Documents              25
  Images                 13
  ...
```

## License

Released under the [MIT License](LICENSE) — free to use, copy, and modify.

---

Enjoy! If it saved you time, [buy me a coffee]([RENDER_BMO_LINK]) ☕

[buy me a coffee]: https://buymeacoffee.com/aurorauap
