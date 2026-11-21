#!/usr/bin/env python3
"""
fileflow_qt.py — PySide6 front-end for file_organizer.

Drop a folder on the window (or browse to it), choose what to do, watch the
progress and the log, optionally open the result. Runs the sort in a worker
thread; Stop is honoured by the core through its stop_event.

Run:                python3 fileflow_qt.py
Headless smoke:     QT_QPA_PLATFORM=offscreen python3 fileflow_qt.py --smoke
Screenshot:         QT_QPA_PLATFORM=offscreen python3 fileflow_qt.py --shot /tmp/x.png
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPalette, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar,
    QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from file_organizer import organize

# ---------------------------------------------------------------- palette ---

ACCENT = "#00ff9c"
BG = "#070b09"
SURFACE = "#0d1310"
SURFACE_ALT = "#121a15"
BORDER = "#1d3a2a"
FG = "#d6ffe4"
MUTED = "#6f9c84"
DANGER = "#ff5b5b"
WARN = "#e0d26b"

def _check_svg() -> str:
    cache = Path.home() / ".cache" / "fileflow"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / "check.svg"
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            f'<path d="M3.4 8.6l3.1 3.1 6.1-7.2" fill="none" stroke="{BG}"'
            ' stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
            encoding="utf-8",
        )
        return path.as_uri()
    except OSError:
        return ""


CHECK_ICON = _check_svg()

QSS = f"""
QWidget {{
    background: {BG};
    color: {FG};
    font-family: "Noto Sans", sans-serif;
    font-size: 13px;
}}
QLabel#Brand {{
    font-family: "JetBrainsMono Nerd Font", monospace;
    font-size: 20px; font-weight: 700; letter-spacing: 4px; color: {ACCENT};
}}
QLabel#Sub {{ color: {MUTED}; font-size: 11px; }}
QLabel#CardTitle {{
    color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1.4px;
    font-family: "JetBrainsMono Nerd Font", monospace;
}}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Big {{ font-size: 25px; font-weight: 700; color: {ACCENT}; }}
QFrame#Card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {SURFACE}, stop:1 {BG});
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#Drop {{
    background: {SURFACE};
    border: 1px dashed {BORDER};
    border-radius: 10px;
}}
QFrame#Drop[drag="true"] {{
    border: 1px dashed {ACCENT};
    background: {SURFACE_ALT};
}}
QLineEdit, QSpinBox, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 7px 10px; selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_ALT}; border: 1px solid {BORDER};
    selection-background-color: {SURFACE_ALT}; outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; height: 0; border: none; }}
QCheckBox {{ spacing: 8px; color: {FG}; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {BORDER};
    border-radius: 3px; background: {BG};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT}; border: 1px solid {ACCENT};
    image: url({CHECK_ICON});
}}
QPushButton {{
    background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 8px 16px; color: {FG};
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE}; }}
QPushButton#Go {{
    background: {ACCENT}; border: 1px solid {ACCENT}; color: {BG};
    font-weight: 700; padding: 10px 26px;
    font-family: "JetBrainsMono Nerd Font", monospace; letter-spacing: 1px;
}}
QPushButton#Go:hover {{ background: #4dffbe; }}
QPushButton#Go:disabled {{ background: {SURFACE_ALT}; color: {MUTED}; border-color: {BORDER}; }}
QPushButton#Stop {{
    background: rgba(255,91,91,0.12); border: 1px solid {DANGER}; color: {DANGER};
    font-weight: 700; padding: 10px 20px;
}}
QPushButton#Stop:disabled {{ background: {SURFACE}; color: {MUTED}; border-color: {BORDER}; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton#Ghost:hover {{ color: {FG}; border-color: {BORDER}; }}
QProgressBar {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 5px;
    height: 10px; text-align: center; color: {BG};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QPlainTextEdit#Console {{
    background: #050806; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px; color: #a9f5c6; font-size: 12px;
    font-family: "JetBrainsMono Nerd Font", monospace;
    selection-background-color: {SURFACE_ALT};
}}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QToolTip {{
    background: {SURFACE_ALT}; color: {FG}; border: 1px solid {BORDER}; padding: 4px;
}}
"""


# ----------------------------------------------------------------- worker ---

class Worker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config
        self.stop_event = threading.Event()
        self._log_handle = None

    def cancel(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        stats = {}
        try:
            if self.config.get("log_path"):
                self._log_handle = open(self.config["log_path"], "w", encoding="utf-8")
            kwargs = {k: v for k, v in self.config.items() if k != "log_path"}
            stats = organize(
                kwargs.pop("source"),
                stop_event=self.stop_event,
                on_event=self.log.emit,
                on_progress=lambda done, total: self.progress.emit(done, total),
                per_file_log=self._log_handle,
                **kwargs,
            )
        except Exception as exc:                      # surface, don't crash
            self.log.emit(f"[error] {exc}")
            stats = {"error": str(exc), "total": 0, "moved": 0, "copied": 0,
                     "failed": 0, "skipped_sorted": 0, "skipped_size": 0,
                     "by_category": {}, "by_project": {}, "stopped": False}
        finally:
            if self._log_handle:
                self._log_handle.close()
        self.done.emit(stats)


# ------------------------------------------------------------------- drop ---

class DropCard(QFrame):
    """Click or drop a folder here."""

    dropped = Signal(str)
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Drop")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self.source = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.title = QLabel("Drop a folder here")
        self.title.setStyleSheet("font-size:15px; font-weight:600;")
        self.path = QLabel("or click to choose — nothing is moved until you press Start")
        self.path.setObjectName("Muted")
        self.path.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.path)

    def set_source(self, path: str) -> None:
        self.source = path
        self.path.setText(path or "—")
        self.path.setObjectName("Muted")
        self.path.setStyleSheet("" if not path else f"color:{ACCENT};")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)

    def _set_drag(self, active: bool) -> None:
        self.setProperty("drag", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_drag(False)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_drag(False)
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_dir():
                self.dropped.emit(str(path))
                return
            if path.is_file():
                self.dropped.emit(str(path.parent))
                return


# ----------------------------------------------------------------- window ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FileFlow — folder sorter")
        self.resize(980, 720)
        self.setMinimumSize(760, 620)
        self.worker: Worker | None = None
        self._last_dest = ""
        self._build()
        self._shortcuts()

    # -- construction ----------------------------------------------------
    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 14, 16, 16)
        box.setSpacing(10)
        return frame, box

    def _section(self, box: QVBoxLayout, text: str) -> None:
        label = QLabel(f"// {text.upper()}")
        label.setObjectName("CardTitle")
        box.addWidget(label)

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(20, 16, 20, 14)
        page.setSpacing(12)

        # header
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        brand = QLabel("FILEFLOW")
        brand.setObjectName("Brand")
        sub = QLabel("sort a messy folder into clean categories")
        sub.setObjectName("Sub")
        titles.addWidget(brand)
        titles.addWidget(sub)
        head.addLayout(titles)
        head.addStretch(1)
        self.mode_hint = QLabel("")
        self.mode_hint.setObjectName("Muted")
        head.addWidget(self.mode_hint)
        page.addLayout(head)

        # drop / source
        self.drop = DropCard()
        self.drop.dropped.connect(self._set_source)
        self.drop.clicked.connect(self._pick_source)
        page.addWidget(self.drop)

        # options
        card, box = self._card()
        self._section(box, "options")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        grid.setColumnMinimumWidth(0, 132)
        grid.setColumnMinimumWidth(2, 128)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        grid.addWidget(self._muted("Destination"), 0, 0)
        self.dest = QLineEdit()
        self.dest.setPlaceholderText("empty = sort in place")
        grid.addWidget(self.dest, 0, 1)
        browse_dest = QPushButton("…")
        browse_dest.setObjectName("Ghost")
        browse_dest.setFixedWidth(34)
        browse_dest.clicked.connect(self._pick_dest)
        grid.addWidget(browse_dest, 0, 2)

        self.copy = QCheckBox("Copy instead of move")
        self.shard = QCheckBox("Shard by first letter")
        self.shard.setChecked(True)
        self.tree = QCheckBox("Keep subfolders")
        self.dry = QCheckBox("Dry-run (just plan)")
        opts = QHBoxLayout()
        opts.setSpacing(20)
        for widget in (self.copy, self.shard, self.tree, self.dry):
            opts.addWidget(widget)
        opts.addStretch(1)
        grid.addLayout(opts, 1, 0, 1, 4)

        grid.addWidget(self._muted("Projects (comma-sep)"), 2, 0)
        self.projects = QLineEdit()
        self.projects.setPlaceholderText("siteA, projectX")
        grid.addWidget(self.projects, 2, 1)
        grid.addWidget(self._muted("Project regex"), 2, 2)
        self.regex = QLineEdit()
        self.regex.setPlaceholderText(r"report_\d+")
        grid.addWidget(self.regex, 2, 3)

        grid.addWidget(self._muted("Workers"), 3, 0)
        self.workers = QSpinBox()
        self.workers.setRange(1, 64)
        self.workers.setValue(8)
        grid.addWidget(self.workers, 3, 1)
        grid.addWidget(self._muted("Skip files < bytes"), 3, 2)
        self.min_size = QSpinBox()
        self.min_size.setRange(0, 100_000_000)
        self.min_size.setSingleStep(1024)
        grid.addWidget(self.min_size, 3, 3)

        box.addLayout(grid)
        page.addWidget(card)

        # progress
        card, box = self._card()
        top = QHBoxLayout()
        title = QLabel("// PROGRESS")
        title.setObjectName("CardTitle")
        top.addWidget(title)
        top.addStretch(1)
        self.counter = QLabel("idle")
        self.counter.setObjectName("Muted")
        top.addWidget(self.counter)
        box.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        box.addWidget(self.bar)

        stats_row = QHBoxLayout()
        self.stat_moved = self._stat("moved")
        self.stat_copy = self._stat("copied")
        self.stat_sorted = self._stat("already sorted")
        self.stat_fail = self._stat("failed")
        for block in (self.stat_moved, self.stat_copy, self.stat_sorted, self.stat_fail):
            stats_row.addLayout(block)
        stats_row.addStretch(1)
        box.addLayout(stats_row)
        page.addWidget(card)

        # actions
        actions = QHBoxLayout()
        self.start_btn = QPushButton("▶  START")
        self.start_btn.setObjectName("Go")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("■  STOP")
        self.stop_btn.setObjectName("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)
        self.open_btn = QPushButton("open destination")
        self.open_btn.setObjectName("Ghost")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_dest)
        actions.addWidget(self.start_btn)
        actions.addWidget(self.stop_btn)
        actions.addStretch(1)
        actions.addWidget(self.open_btn)
        page.addLayout(actions)

        # console
        self.console = QPlainTextEdit()
        self.console.setObjectName("Console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        self.console.setPlaceholderText("log appears here")
        self.console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        page.addWidget(self.console, 1)

        self._font = QFont("JetBrainsMono Nerd Font", 10)
        self._font.setStyleHint(QFont.Monospace)
        self.console.setFont(self._font)

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    def _stat(self, title: str) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(0)
        value = QLabel("0")
        value.setObjectName("Big")
        box.addWidget(value)
        box.addWidget(self._muted(title))
        setattr(self, "_value_" + title.replace(" ", "_"), value)
        return box

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.start)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.start)
        QShortcut(QKeySequence("Esc"), self, activated=self.stop)

    # -- actions ---------------------------------------------------------
    def _set_source(self, path: str) -> None:
        self.drop.set_source(path)

    def _pick_source(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose the messy folder")
        if chosen:
            self._set_source(chosen)

    def _pick_dest(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose the destination")
        if chosen:
            self.dest.setText(chosen)

    def _config(self) -> dict:
        return {
            "source": self.drop.source,
            "dest": self.dest.text().strip() or None,
            "copy": self.copy.isChecked(),
            "shard": self.shard.isChecked(),
            "keep_tree": self.tree.isChecked(),
            "dry_run": self.dry.isChecked(),
            "project_names": self.projects.text().strip() or None,
            "project_regex": self.regex.text().strip() or None,
            "max_workers": self.workers.value(),
            "min_size": self.min_size.value(),
            "log_path": None,
        }

    def start(self) -> None:
        if self.worker:
            return
        source = self.drop.source.strip()
        if not source or not Path(source).is_dir():
            self._log("pick a folder first (drop it on the window or click the box)", WARN)
            return

        self.console.clear()
        self.bar.setValue(0)
        for name in ("moved", "copied", "already_sorted", "failed"):
            getattr(self, f"_value_{name}").setText("0")
        self.counter.setText("running…")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.open_btn.setEnabled(False)
        self._last_dest = self.dest.text().strip() or source

        self.worker = Worker(self._config(), self)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._progress)
        self.worker.done.connect(self._finished)
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.counter.setText("stopping…")

    def _progress(self, done: int, total: int) -> None:
        pct = int(100 * done / total) if total else 0
        self.bar.setValue(pct)
        self.counter.setText(f"{done:,} / {total:,}  ·  {pct}%")

    def _finished(self, stats: dict) -> None:
        self.worker = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.open_btn.setEnabled(bool(stats.get("moved") or stats.get("copied")))

        self._value_moved.setText(f"{stats.get('moved', 0):,}")
        self._value_copied.setText(f"{stats.get('copied', 0):,}")
        self._value_already_sorted.setText(f"{stats.get('skipped_sorted', 0):,}")
        self._value_failed.setText(f"{stats.get('failed', 0):,}")

        if stats.get("stopped"):
            self.counter.setText("stopped")
        elif stats.get("dry_run"):
            self.counter.setText("dry-run finished")
        else:
            self.counter.setText(f"done in {stats.get('duration', 0)}s")
            self.bar.setValue(100)

        categories = stats.get("by_category") or {}
        if categories:
            self._log("")
            for name, count in sorted(categories.items(), key=lambda kv: -kv[1]):
                self._log(f"  {name:<14} {count:>6,}")
        if stats.get("errors"):
            self._log("")
            for error in stats["errors"][:5]:
                self._log(f"  ! {error}", DANGER)
        if stats.get("error"):
            self._log(f"[error] {stats['error']}", DANGER)

    def _open_dest(self) -> None:
        if not self._last_dest:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_dest))

    def _log(self, line: str, color: str | None = None) -> None:
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(QColor(color))
        cursor.insertText(line + "\n", fmt)
        self.console.setTextCursor(cursor)
        bar = self.console.verticalScrollBar()
        bar.setValue(bar.maximum())

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker:
            self.worker.cancel()
            self.worker.wait(5000)
        super().closeEvent(event)


# ------------------------------------------------------------------ entry ---

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--shot", metavar="PATH")
    parser.add_argument("--source", help="pre-fill the source folder")
    args = parser.parse_args()

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(FG))
    palette.setColor(QPalette.Base, QColor(BG))
    palette.setColor(QPalette.Text, QColor(FG))
    palette.setColor(QPalette.Button, QColor(SURFACE))
    palette.setColor(QPalette.ButtonText, QColor(FG))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor(BG))
    palette.setColor(QPalette.PlaceholderText, QColor(MUTED))
    app.setPalette(palette)
    app.setStyleSheet(QSS)

    window = MainWindow()
    if args.source:
        window._set_source(args.source)
    window.show()

    if args.smoke or args.shot:
        app.processEvents()
        if args.shot:
            window.grab().save(args.shot)
            print(f"screenshot: {args.shot}")
        print("smoke OK: window built")
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
