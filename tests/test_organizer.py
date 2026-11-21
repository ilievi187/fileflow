"""Tests for file_organizer — every case below used to fail or was untested."""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from file_organizer import classify, organize


def make(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


class ClassifyTests(unittest.TestCase):
    def test_log_beats_document(self):
        self.assertEqual(classify("error_log.txt"), "Logs")
        self.assertEqual(classify("access.log"), "Logs")
        self.assertEqual(classify("boot.log"), "Logs")

    def test_normal_documents_stay_documents(self):
        self.assertEqual(classify("report.txt"), "Documents")
        self.assertEqual(classify("notes.md"), "Documents")
        self.assertEqual(classify("data.csv"), "Data")

    def test_unknown_extension(self):
        self.assertEqual(classify("firmware.xyz"), "Other")


class CollisionTests(unittest.TestCase):
    def test_same_name_in_two_folders_keeps_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"a/report.pdf": "first", "b/report.pdf": "second"})
            stats = organize(src, dest=Path(tmp) / "out")

            self.assertEqual(stats["failed"], 0)
            self.assertEqual(stats["total"], 2)
            files = tree(Path(tmp) / "out")
            self.assertEqual(len(files), 2, f"lost a file: {files}")

            contents = sorted(p.read_text() for p in (Path(tmp) / "out").rglob("*") if p.is_file())
            self.assertEqual(contents, ["first", "second"])

    def test_many_identical_names_no_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {f"d{i}/same.txt": f"content-{i}" for i in range(25)})
            stats = organize(src, dest=Path(tmp) / "out", max_workers=16)

            self.assertEqual(stats["failed"], 0)
            out = Path(tmp) / "out"
            got = sorted(p.read_text() for p in out.rglob("*") if p.is_file())
            self.assertEqual(got, sorted(f"content-{i}" for i in range(25)))


class IdempotencyTests(unittest.TestCase):
    def test_second_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"a.pdf": "x", "photo.jpg": "x", "code.py": "x", "error_log.txt": "x"})
            first = organize(src)
            after_one = tree(src)
            second = organize(src)
            after_two = tree(src)

            self.assertEqual(after_one, after_two)
            self.assertEqual(second["moved"], 0)
            self.assertEqual(second["skipped_sorted"], second["total"])
            self.assertEqual(first["moved"], 4)

    def test_keep_tree_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"sub/a.pdf": "x", "sub/deep/b.pdf": "x"})
            organize(src, keep_tree=True)
            after_one = tree(src)
            organize(src, keep_tree=True)
            self.assertEqual(after_one, tree(src))


class ProjectTests(unittest.TestCase):
    def test_project_files_go_to_projects_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"siteA/index.html": "x", "siteA/app.js": "x", "other.pdf": "x"})
            stats = organize(src, dest=Path(tmp) / "out", project_names="siteA")

            self.assertEqual(stats["by_project"], {"siteA": 2})
            out = Path(tmp) / "out"
            self.assertTrue((out / "Projects" / "siteA" / "index.html").exists())
            self.assertTrue((out / "Projects" / "siteA" / "app.js").exists())
            self.assertTrue((out / "Documents" / "O" / "other.pdf").exists())

    def test_project_regex(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"report_2026_q1.pdf": "x"})
            stats = organize(src, dest=Path(tmp) / "out", project_regex=r"report_\d+")
            self.assertEqual(list(stats["by_project"]), ["report_2026"])


class KeepTreeTests(unittest.TestCase):
    def test_subfolders_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"a.pdf": "x", "sub/b.pdf": "x", "sub/deep/c.pdf": "x"})
            organize(src, dest=Path(tmp) / "out", keep_tree=True)
            out = tree(Path(tmp) / "out")
            self.assertIn("Documents/sub/b.pdf", out)
            self.assertIn("Documents/sub/deep/c.pdf", out)


class StopTests(unittest.TestCase):
    def test_stop_before_start_moves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {f"f{i:03d}.pdf": "x" for i in range(300)})
            event = threading.Event()
            event.set()
            stats = organize(src, dest=Path(tmp) / "out", stop_event=event)

            self.assertTrue(stats["stopped"])
            self.assertEqual(stats["moved"], 0)
            self.assertEqual(len(tree(src)), 300)


class MiscTests(unittest.TestCase):
    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"a.pdf": "x", "b.jpg": "x"})
            stats = organize(src, dest=Path(tmp) / "out", dry_run=True)
            self.assertTrue(stats["dry_run"])
            self.assertEqual(stats["moved"], 0)
            self.assertFalse((Path(tmp) / "out").exists())
            self.assertEqual(len(tree(src)), 2)

    def test_min_size_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"small.txt": "x", "big.txt": "x" * 2000})
            stats = organize(src, dest=Path(tmp) / "out", min_size=1000)
            self.assertEqual(stats["skipped_size"], 1)
            self.assertEqual(stats["moved"], 1)

    def test_dest_inside_source_is_not_rescanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"a.pdf": "x"})
            out = src / "sorted"
            stats = organize(src, dest=out)
            again = organize(out)          # pass over our own output
            self.assertEqual(again["failed"], 0)
            self.assertEqual(len(tree(out)), 1)

    def test_per_file_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            make(src, {"a.pdf": "x"})
            log_path = Path(tmp) / "run.jsonl"
            with log_path.open("w") as handle:
                organize(src, dest=Path(tmp) / "out", per_file_log=handle)
            entries = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["action"], "move")
            self.assertTrue(entries[0]["dst"].endswith("a.pdf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
