#!/usr/bin/env python3
"""
fileflow_qt.py — PySide6 front-end for file_organizer.

Drop a folder (or browse), choose the options, press Start. The sort runs in a
worker thread with live progress, real Stop, a per-run journal and a one-click
Undo. Colours come from the active Omarchy theme and reload live when you switch
themes; motion is limited to short, quiet animations.

Run:             python3 fileflow_qt.py
Headless smoke:  QT_QPA_PLATFORM=offscreen python3 fileflow_qt.py --smoke
Screenshot:      QT_QPA_PLATFORM=offscreen python3 fileflow_qt.py --shot /tmp/x.png
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QFileSystemWatcher, QPropertyAnimation, Qt, QThread, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QPalette, QShortcut, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget,
)

import ui_theme
from file_organizer import default_journal_path, organize, undo


# ----------------------------------------------------------------- worker ---

class Worker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(dict)

    def __init__(self, config: dict, mode: str = "organize", parent=None):
        super().__init__(parent)
        self.config = config
        self.mode = mode
        self.stop_event = threading.Event()
        self._log_handle = None

    def cancel(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        stats: dict = {}
        try:
            if self.mode == "undo":
                stats = undo(
                    self.config["journal"],
                    dry_run=self.config.get("dry_run", False),
                    on_event=self.log.emit,
                    stop_event=self.stop_event,
                )
            else:
                if self.config.get("log_path"):
                    self._log_handle = open(self.config["log_path"], "w", encoding="utf-8")
                kwargs = {k: v for k, v in self.config.items()
                          if k not in ("log_path", "journal")}
                stats = organize(
                    kwargs.pop("source"),
                    stop_event=self.stop_event,
                    on_event=self.log.emit,
                    on_progress=lambda done, total: self.progress.emit(done, total),
                    per_file_log=self._log_handle,
                    journal_path=self.config.get("journal"),
                    **kwargs,
                )
        except Exception as exc:
            self.log.emit(f"[error] {exc}")
            stats = {"error": str(exc), "total": 0, "moved": 0, "copied": 0,
                     "failed": 0, "skipped_sorted": 0, "skipped_size": 0,
                     "duplicates": 0, "pruned_dirs": 0, "stopped": False,
                     "by_category": {}, "by_project": {}}
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
        self.setMinimumHeight(94)
        self.source = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(3)
        self.title = QLabel("Drop a folder here")
        self.title.setStyleSheet("font-size:15px; font-weight:600;")
        self.path = QLabel("or click to choose — nothing moves until you press Start")
        self.path.setObjectName("Muted")
        self.path.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.path)

    def set_source(self, path: str) -> None:
        self.source = path
        self.path.setText(path or "—")
        self.path.setStyleSheet(f"color:{ui_theme.load().accent};" if path else "")

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
    def __init__(self, theme: ui_theme.Theme):
        super().__init__()
        self.theme = theme
        self.setWindowTitle("FileFlow — folder sorter")
        self.resize(1000, 780)
        self.setMinimumSize(780, 660)
        self.worker: Worker | None = None
        self._last_dest = ""
        self._journal = default_journal_path()
        self._progress_anim: QPropertyAnimation | None = None
        self._glow: QPropertyAnimation | None = None
        self._scroll: QPropertyAnimation | None = None
        self._fade: QPropertyAnimation | None = None
        self._counter_anims: list[QVariantAnimation] = []
        self._build()
        self._shortcuts()
        self._watch_theme()
        self._restore_settings()

    # -- construction ----------------------------------------------------
    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 13, 16, 15)
        box.setSpacing(10)
        return frame, box

    def _section(self, box: QVBoxLayout, text: str) -> None:
        label = QLabel(f"// {text.upper()}")
        label.setObjectName("CardTitle")
        box.addWidget(label)

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(20, 16, 20, 14)
        page.setSpacing(11)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.brand = QLabel("FILEFLOW")
        self.brand.setObjectName("Brand")
        self.sub = QLabel("sort a messy folder into clean categories")
        self.sub.setObjectName("Sub")
        titles.addWidget(self.brand)
        titles.addWidget(self.sub)
        head.addLayout(titles)
        head.addStretch(1)
        self.theme_label = QLabel(f"theme  {self.theme.name}")
        self.theme_label.setObjectName("Muted")
        head.addWidget(self.theme_label)
        page.addLayout(head)

        self.drop = DropCard()
        self.drop.dropped.connect(self._set_source)
        self.drop.clicked.connect(self._pick_source)
        page.addWidget(self.drop)

        # ---- options ---------------------------------------------------
        card, box = self._card()
        self._section(box, "options")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(11)
        grid.setColumnMinimumWidth(0, 130)
        grid.setColumnMinimumWidth(2, 126)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        grid.addWidget(self._muted("Destination"), 0, 0)
        self.dest = QLineEdit()
        self.dest.setPlaceholderText("empty = sort in place")
        grid.addWidget(self.dest, 0, 1)
        browse = QPushButton("…")
        browse.setObjectName("Ghost")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._pick_dest)
        grid.addWidget(browse, 0, 2)

        row = QHBoxLayout()
        row.setSpacing(18)
        self.copy = QCheckBox("Copy instead of move")
        self.shard = QCheckBox("Shard by letter")
        self.shard.setChecked(True)
        self.tree = QCheckBox("Keep subfolders")
        self.dry = QCheckBox("Dry-run")
        for widget in (self.copy, self.shard, self.tree, self.dry):
            row.addWidget(widget)
        row.addStretch(1)
        grid.addLayout(row, 1, 0, 1, 4)

        grid.addWidget(self._muted("Projects"), 2, 0)
        self.projects = QLineEdit()
        self.projects.setPlaceholderText("siteA, projectX")
        grid.addWidget(self.projects, 2, 1)
        grid.addWidget(self._muted("Project regex"), 2, 2)
        self.regex = QLineEdit()
        self.regex.setPlaceholderText(r"report_\d+")
        grid.addWidget(self.regex, 2, 3)

        grid.addWidget(self._muted("Exclude (globs)"), 3, 0)
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText("*.tmp, node_modules, .git")
        grid.addWidget(self.exclude, 3, 1)
        grid.addWidget(self._muted("Duplicates"), 3, 2)
        self.duplicates = QComboBox()
        self.duplicates.addItems(["keep", "move to Duplicates/", "report only"])
        grid.addWidget(self.duplicates, 3, 3)

        row2 = QHBoxLayout()
        row2.setSpacing(18)
        self.by_date = QCheckBox("Group by date (YYYY/MM)")
        self.prune = QCheckBox("Remove empty folders")
        row2.addWidget(self.by_date)
        row2.addWidget(self.prune)
        row2.addStretch(1)
        grid.addLayout(row2, 4, 0, 1, 2)

        grid.addWidget(self._muted("Workers"), 5, 0)
        self.workers = QSpinBox()
        self.workers.setRange(1, 64)
        self.workers.setValue(8)
        grid.addWidget(self.workers, 5, 1)
        grid.addWidget(self._muted("Skip files < bytes"), 5, 2)
        self.min_size = QSpinBox()
        self.min_size.setRange(0, 100_000_000)
        self.min_size.setSingleStep(1024)
        grid.addWidget(self.min_size, 5, 3)

        box.addLayout(grid)
        page.addWidget(card)

        # ---- progress --------------------------------------------------
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
        self.bar.setTextVisible(False)
        box.addWidget(self.bar)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(26)
        self.stat_widgets = {}
        for key, label in (("moved", "moved"), ("copied", "copied"),
                           ("sorted", "already sorted"), ("dupes", "duplicates"),
                           ("failed", "failed")):
            block = QVBoxLayout()
            block.setSpacing(0)
            value = QLabel("0")
            value.setObjectName("Big")
            block.addWidget(value)
            block.addWidget(self._muted(label))
            stats_row.addLayout(block)
            self.stat_widgets[key] = value
        stats_row.addStretch(1)
        box.addLayout(stats_row)
        page.addWidget(card)

        # ---- actions ---------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.start_btn = QPushButton("▶  START")
        self.start_btn.setObjectName("Go")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("■  STOP")
        self.stop_btn.setObjectName("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)
        self.undo_btn = QPushButton("↶  UNDO LAST RUN")
        self.undo_btn.setObjectName("Undo")
        self.undo_btn.clicked.connect(self.undo_last)
        self.open_btn = QPushButton("open destination")
        self.open_btn.setObjectName("Ghost")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_dest)
        actions.addWidget(self.start_btn)
        actions.addWidget(self.stop_btn)
        actions.addWidget(self.undo_btn)
        actions.addStretch(1)
        actions.addWidget(self.open_btn)
        page.addLayout(actions)

        self.console = QPlainTextEdit()
        self.console.setObjectName("Console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        self.console.setPlaceholderText("log appears here")
        self.console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        page.addWidget(self.console, 1)

        font = QFont("JetBrainsMono Nerd Font", 10)
        font.setStyleHint(QFont.Monospace)
        self.console.setFont(font)

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.start)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.start)
        QShortcut(QKeySequence("Esc"), self, activated=self.stop)

    # -- motion ----------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._fade is not None:
            return
        effect = QGraphicsOpacityEffect(self.centralWidget())
        self.centralWidget().setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(240)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: self.centralWidget().setGraphicsEffect(None))
        anim.start()
        self._fade = anim

    def _set_bar(self, value: int) -> None:
        if self._progress_anim is not None:
            self._progress_anim.stop()
        anim = QPropertyAnimation(self.bar, b"value", self)
        anim.setDuration(220)
        anim.setStartValue(self.bar.value())
        anim.setEndValue(value)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._progress_anim = anim

    def _set_stat(self, key: str, value: int) -> None:
        label = self.stat_widgets[key]
        try:
            current = int(label.text().replace(",", ""))
        except ValueError:
            current = 0
        if current == value:
            return
        anim = QVariantAnimation(self)
        anim.setDuration(380)
        anim.setStartValue(current)
        anim.setEndValue(value)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: label.setText(f"{int(v):,}"))
        anim.start()
        self._counter_anims.append(anim)

    def _start_glow(self) -> None:
        effect = QGraphicsDropShadowEffect(self.start_btn)
        effect.setOffset(0, 0)
        effect.setBlurRadius(0)
        effect.setColor(QColor(self.theme.accent))
        self.start_btn.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"blurRadius", self)
        anim.setDuration(1600)
        anim.setLoopCount(-1)
        anim.setKeyValueAt(0.0, 0)
        anim.setKeyValueAt(0.5, 18)
        anim.setKeyValueAt(1.0, 0)
        anim.setEasingCurve(QEasingCurve.InOutSine)
        anim.start()
        self._glow = anim

    def _stop_glow(self) -> None:
        if self._glow is not None:
            self._glow.stop()
            self._glow = None
        self.start_btn.setGraphicsEffect(None)

    # -- theme -----------------------------------------------------------
    def _apply_theme(self, theme: ui_theme.Theme) -> None:
        self.theme = theme
        app = QApplication.instance()
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(theme.bg))
        palette.setColor(QPalette.WindowText, QColor(theme.fg))
        palette.setColor(QPalette.Base, QColor(theme.bg))
        palette.setColor(QPalette.Text, QColor(theme.fg))
        palette.setColor(QPalette.Button, QColor(theme.surface))
        palette.setColor(QPalette.ButtonText, QColor(theme.fg))
        palette.setColor(QPalette.Highlight, QColor(theme.accent))
        palette.setColor(QPalette.HighlightedText, QColor(theme.bg))
        palette.setColor(QPalette.PlaceholderText, QColor(theme.muted))
        app.setPalette(palette)
        app.setStyleSheet(theme.qss())
        self.theme_label.setText(f"theme  {theme.name}")
        if self.drop.source:
            self.drop.set_source(self.drop.source)

    def _watch_theme(self) -> None:
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._theme_changed)
        self._watcher.directoryChanged.connect(self._theme_changed)
        self._add_watch_paths()

    def _add_watch_paths(self) -> None:
        base = Path.home() / ".local/state/omarchy/current"
        for path in (base, base / "theme.name", base / "theme" / "colors.toml"):
            text = str(path)
            if path.exists() and text not in self._watcher.files() \
                    and text not in self._watcher.directories():
                self._watcher.addPath(text)

    def _theme_changed(self, _path: str) -> None:
        QTimer.singleShot(300, self._reload_theme)
        QTimer.singleShot(600, self._add_watch_paths)

    def _reload_theme(self) -> None:
        fresh = ui_theme.load()
        if (fresh.bg, fresh.accent, fresh.name) == (self.theme.bg, self.theme.accent, self.theme.name):
            return
        self._apply_theme(fresh)
        self._log(f"theme switched to {fresh.name}", fresh.accent)

    # -- settings --------------------------------------------------------
    def _restore_settings(self) -> None:
        from PySide6.QtCore import QSettings
        s = QSettings("fileflow", "fileflow")
        self.dest.setText(s.value("dest", "", str))
        self.projects.setText(s.value("projects", "", str))
        self.regex.setText(s.value("regex", "", str))
        self.exclude.setText(s.value("exclude", "", str))
        self.copy.setChecked(s.value("copy", False, bool))
        self.shard.setChecked(s.value("shard", True, bool))
        self.tree.setChecked(s.value("tree", False, bool))
        self.dry.setChecked(s.value("dry", False, bool))
        self.by_date.setChecked(s.value("by_date", False, bool))
        self.prune.setChecked(s.value("prune", False, bool))
        self.workers.setValue(s.value("workers", 8, int))
        self.min_size.setValue(s.value("min_size", 0, int))
        self.duplicates.setCurrentIndex(s.value("duplicates", 0, int))
        last = s.value("source", "", str)
        if last and Path(last).is_dir():
            self._set_source(last)
        self._refresh_undo()

    def _save_settings(self) -> None:
        from PySide6.QtCore import QSettings
        s = QSettings("fileflow", "fileflow")
        s.setValue("dest", self.dest.text())
        s.setValue("projects", self.projects.text())
        s.setValue("regex", self.regex.text())
        s.setValue("exclude", self.exclude.text())
        s.setValue("copy", self.copy.isChecked())
        s.setValue("shard", self.shard.isChecked())
        s.setValue("tree", self.tree.isChecked())
        s.setValue("dry", self.dry.isChecked())
        s.setValue("by_date", self.by_date.isChecked())
        s.setValue("prune", self.prune.isChecked())
        s.setValue("workers", self.workers.value())
        s.setValue("min_size", self.min_size.value())
        s.setValue("duplicates", self.duplicates.currentIndex())
        s.setValue("source", self.drop.source)

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
        dupes = ["keep", "move", "report"][self.duplicates.currentIndex()]
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
            "exclude": self.exclude.text().strip() or None,
            "by_date": self.by_date.isChecked(),
            "prune_empty": self.prune.isChecked(),
            "duplicates": dupes,
            "journal": str(self._journal),
            "log_path": None,
        }

    def start(self) -> None:
        if self.worker:
            return
        source = self.drop.source.strip()
        if not source or not Path(source).is_dir():
            self._log("pick a folder first — drop it on the window or click the box",
                      self.theme.warn)
            return

        self.console.clear()
        self._set_bar(0)
        for key in self.stat_widgets:
            self._set_stat(key, 0)
        self.counter.setText("running…")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.undo_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self._last_dest = self.dest.text().strip() or source

        self.worker = Worker(self._config(), "organize", self)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._progress)
        self.worker.done.connect(self._finished)
        self.worker.start()
        self._start_glow()
        self._save_settings()

    def stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.counter.setText("stopping…")

    def undo_last(self) -> None:
        if self.worker:
            return
        if not self._journal.is_file():
            self._log("no journal yet — run a sort first", self.theme.warn)
            return
        answer = QMessageBox.question(
            self, "Undo last run",
            "Move every file from the last run back where it came from?\n\n"
            "Copies created by that run will be removed.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return

        self.console.clear()
        self.counter.setText("undoing…")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.undo_btn.setEnabled(False)

        self.worker = Worker({"journal": str(self._journal),
                              "dry_run": self.dry.isChecked()}, "undo", self)
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._undo_finished)
        self.worker.start()
        self._start_glow()

    def _progress(self, done: int, total: int) -> None:
        pct = int(100 * done / total) if total else 0
        self._set_bar(pct)
        self.counter.setText(f"{done:,} / {total:,}  ·  {pct}%")

    def _finished(self, stats: dict) -> None:
        self.worker = None
        self._stop_glow()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.open_btn.setEnabled(bool(stats.get("moved") or stats.get("copied")))
        self._refresh_undo()

        self._set_stat("moved", stats.get("moved", 0))
        self._set_stat("copied", stats.get("copied", 0))
        self._set_stat("sorted", stats.get("skipped_sorted", 0))
        self._set_stat("dupes", stats.get("duplicates", 0))
        self._set_stat("failed", stats.get("failed", 0))

        if stats.get("stopped"):
            self.counter.setText("stopped")
        elif stats.get("dry_run"):
            self.counter.setText("dry-run finished")
        else:
            self.counter.setText(f"done in {stats.get('duration', 0)}s")
            self._set_bar(100)

        categories = stats.get("by_category") or {}
        if categories:
            self._log("")
            for name, count in sorted(categories.items(), key=lambda kv: -kv[1]):
                self._log(f"  {name:<14} {count:>6,}")
        if stats.get("pruned_dirs"):
            self._log(f"  removed {stats['pruned_dirs']} empty folders")
        for error in (stats.get("errors") or [])[:5]:
            self._log(f"  ! {error}", self.theme.danger)
        if stats.get("error"):
            self._log(f"[error] {stats['error']}", self.theme.danger)

    def _undo_finished(self, stats: dict) -> None:
        self.worker = None
        self._stop_glow()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.counter.setText("undo finished")
        self._refresh_undo()
        for error in (stats.get("errors") or [])[:5]:
            self._log(f"  ! {error}", self.theme.danger)

    def _refresh_undo(self) -> None:
        self.undo_btn.setEnabled(self._journal.is_file())

    def _open_dest(self) -> None:
        if not self._last_dest:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_dest))

    # -- console ---------------------------------------------------------
    def _log(self, line: str, color: str | None = None) -> None:
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(QColor(color))
        cursor.insertText(line + "\n", fmt)
        self.console.setTextCursor(cursor)
        bar = self.console.verticalScrollBar()
        if self._scroll is not None:
            self._scroll.stop()
        anim = QPropertyAnimation(bar, b"value", self)
        anim.setDuration(110)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(bar.value())
        anim.setEndValue(bar.maximum())
        anim.finished.connect(lambda: bar.setValue(bar.maximum()))
        anim.start()
        self._scroll = anim

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_settings()
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

    theme = ui_theme.load()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = MainWindow(theme)
    window._apply_theme(theme)
    if args.source:
        window._set_source(args.source)
    window.show()

    if args.smoke or args.shot:
        app.processEvents()
        if args.shot:
            for _ in range(12):
                app.processEvents()
                time.sleep(0.02)
            window.grab().save(args.shot)
            print(f"screenshot: {args.shot}")
        print(f"smoke OK: theme={theme.name}")
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
