"""
ui_theme.py — reads the live Omarchy theme and turns it into a palette + QSS.

The app follows whatever theme the desktop is using; if the Omarchy state files
are missing it falls back to a dark palette of its own. A file watcher in the GUI
reloads this when the theme changes, so switching themes repaints the window.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

SEARCH = [
    Path.home() / ".local/state/omarchy/current/theme/colors.toml",
    Path.home() / ".config/omarchy/current/theme/colors.toml",
]

FALLBACK = {
    "mode": "dark",
    "background": "#070b09",
    "foreground": "#d6ffe4",
    "accent": "#00ff9c",
    "muted": "#6f9c84",
    "lighter_background": "#0d1310",
    "red": "#ff5b5b",
    "yellow": "#e0d26b",
    "green": "#00ff9c",
    "cyan": "#6bffd8",
}


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return _hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


@dataclass
class Theme:
    name: str
    bg: str
    surface: str
    surface_alt: str
    fg: str
    muted: str
    accent: str
    border: str
    danger: str
    warn: str
    console_bg: str
    console_fg: str
    dark: bool

    def check_icon(self) -> str:
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fileflow"
        try:
            cache.mkdir(parents=True, exist_ok=True)
            path = cache / "check.svg"
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                f'<path d="M3.4 8.6l3.1 3.1 6.1-7.2" fill="none" stroke="{self.bg}"'
                ' stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                encoding="utf-8",
            )
            return path.as_uri()
        except OSError:
            return ""

    def qss(self) -> str:
        check = self.check_icon()
        return f"""
        QWidget {{
            background: {self.bg};
            color: {self.fg};
            font-family: "Noto Sans", sans-serif;
            font-size: 13px;
        }}
        QLabel, QCheckBox {{ background: transparent; }}
        QLabel#Brand {{
            font-family: "JetBrainsMono Nerd Font", monospace;
            font-size: 20px; font-weight: 700; letter-spacing: 4px;
            color: {self.accent};
        }}
        QLabel#Sub {{ color: {self.muted}; font-size: 11px; }}
        QLabel#CardTitle {{
            color: {self.muted}; font-size: 10px; font-weight: 700;
            letter-spacing: 1.4px;
            font-family: "JetBrainsMono Nerd Font", monospace;
        }}
        QLabel#Muted {{ color: {self.muted}; }}
        QLabel#Big {{
            font-size: 25px; font-weight: 700; color: {self.accent};
            font-family: "JetBrainsMono Nerd Font", monospace;
        }}
        QFrame#Card {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {mix(self.surface, self.fg, 0.035)},
                        stop:1 {self.surface});
            border: 1px solid {self.border};
            border-radius: 9px;
        }}
        QFrame#Drop {{
            background: {self.surface};
            border: 1px dashed {self.border};
            border-radius: 10px;
        }}
        QFrame#Drop[drag="true"] {{
            border: 1px dashed {self.accent};
            background: {mix(self.surface, self.accent, 0.08)};
        }}
        QLineEdit, QSpinBox, QComboBox {{
            background: {self.bg}; border: 1px solid {self.border};
            border-radius: 5px; padding: 7px 10px;
            selection-background-color: {self.accent}; selection-color: {self.bg};
        }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
            border: 1px solid {self.accent};
        }}
        QComboBox QAbstractItemView {{
            background: {self.surface_alt}; border: 1px solid {self.border};
            selection-background-color: {mix(self.surface, self.accent, 0.25)};
            outline: none;
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 0; height: 0; border: none;
        }}
        QCheckBox {{ spacing: 8px; color: {self.fg}; }}
        QCheckBox::indicator {{
            width: 15px; height: 15px; border: 1px solid {self.border};
            border-radius: 3px; background: {self.bg};
        }}
        QCheckBox::indicator:checked {{
            background: {self.accent}; border: 1px solid {self.accent};
            image: url({check});
        }}
        QPushButton {{
            background: {self.surface_alt}; border: 1px solid {self.border};
            border-radius: 5px; padding: 8px 16px; color: {self.fg};
        }}
        QPushButton:hover {{ border-color: {self.accent}; }}
        QPushButton:disabled {{ color: {self.muted}; background: {self.surface}; }}
        QPushButton#Go {{
            background: {self.accent}; border: 1px solid {self.accent};
            color: {self.bg}; font-weight: 700; padding: 10px 26px;
            font-family: "JetBrainsMono Nerd Font", monospace; letter-spacing: 1px;
        }}
        QPushButton#Go:hover {{ background: {mix(self.accent, self.fg, 0.22)}; }}
        QPushButton#Go:disabled {{
            background: {self.surface_alt}; color: {self.muted};
            border-color: {self.border};
        }}
        QPushButton#Stop {{
            background: {mix(self.bg, self.danger, 0.14)};
            border: 1px solid {mix(self.bg, self.danger, 0.6)};
            color: {self.danger}; font-weight: 700; padding: 10px 20px;
        }}
        QPushButton#Stop:disabled {{
            background: {self.surface}; color: {self.muted};
            border-color: {self.border};
        }}
        QPushButton#Ghost {{
            background: transparent; border: 1px solid transparent;
            color: {self.muted};
        }}
        QPushButton#Ghost:hover {{ color: {self.fg}; border-color: {self.border}; }}
        QPushButton#Undo {{
            background: transparent; border: 1px solid {self.border};
            color: {self.warn}; padding: 10px 18px;
        }}
        QPushButton#Undo:hover {{ border-color: {self.warn}; }}
        QPushButton#Undo:disabled {{ color: {self.muted}; border-color: {self.border}; }}
        QProgressBar {{
            background: {self.bg}; border: 1px solid {self.border};
            border-radius: 5px; height: 10px; text-align: center;
        }}
        QProgressBar::chunk {{ background: {self.accent}; border-radius: 4px; }}
        QPlainTextEdit#Console {{
            background: {self.console_bg}; border: 1px solid {self.border};
            border-radius: 8px; padding: 8px; color: {self.console_fg};
            font-size: 12px;
            font-family: "JetBrainsMono Nerd Font", monospace;
            selection-background-color: {mix(self.bg, self.accent, 0.35)};
        }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{
            background: {self.border}; border-radius: 5px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {self.muted}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QToolTip {{
            background: {self.surface_alt}; color: {self.fg};
            border: 1px solid {self.border}; padding: 4px;
        }}
        """


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def theme_name() -> str:
    for path in (
        Path.home() / ".local/state/omarchy/current/theme.name",
        Path.home() / ".config/omarchy/current/theme.name",
    ):
        try:
            name = path.read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            continue
    return "fileflow"


def load() -> Theme:
    data = FALLBACK.copy()
    found = False
    for path in SEARCH:
        if path.is_file():
            parsed = _read(path)
            if parsed:
                data.update(parsed)
                found = True
                break

    bg = data["background"]
    fg = data["foreground"]
    accent = data["accent"]
    dark = data.get("mode", "dark") != "light"
    surface = data.get("lighter_background") or mix(bg, fg, 0.05)

    return Theme(
        name=theme_name() if found else "builtin",
        bg=bg,
        surface=surface,
        surface_alt=mix(bg, fg, 0.09 if dark else 0.05),
        fg=fg,
        muted=data.get("muted") or mix(fg, bg, 0.45),
        accent=accent,
        border=mix(bg, fg, 0.18 if dark else 0.12),
        danger=data.get("red", "#ff5b5b"),
        warn=data.get("yellow", "#e0d26b"),
        console_bg=mix(bg, fg, 0.02),
        console_fg=mix(fg, bg, 0.05),
        dark=dark,
    )
