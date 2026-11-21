#!/usr/bin/env python3
"""
file_organizer_gui.py - polished tkinter front-end for file_organize.organize().

Pick a messy folder -> configure categories/projects -> Sort. Runs in the
background (window can be minimized), live log pane shows progress, Stop halts.

Run with:  python3 file_organizer_gui.py
"""

import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

from file_organizer import organize


def _load_theme():
    """Detect dark/light and build a colour palette so the UI blends in."""
    scheme = "dark"
    try:
        import subprocess
        out = subprocess.check_output(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            text=True, timeout=3,
        ).strip().strip("'\"")
        if out in ("dark", "light"):
            scheme = out
    except Exception:
        pass

    if scheme == "light":
        return dict(
            bg="#f4f4f6", fg="#1a1a1a", frame="#e9e9ed", entry="#ffffff",
            accent="#2f6fed", border="#c9c9d0", text="#1a1a1a", muted="#6b6b76",
            log_bg="#ffffff", log_fg="#111111", ok="#1b7a2e", err="#b3261e",
        )
    return dict(
        bg="#1e1e22", fg="#e8e8ec", frame="#27272d", entry="#303037",
        accent="#4f8cff", border="#3d3d46", text="#e8e8ec", muted="#9a9aa6",
        log_bg="#16161a", log_fg="#d8d8de", ok="#6cc66a", err="#ff6b5c",
    )


class OrganizerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.th = _load_theme()
        self.title("File Organizer")
        self.geometry("560x520")
        self.minsize(480, 420)
        self.stop_event = threading.Event()
        self.thread = None
        self._style()
        self._build_ui()

    # ---- theming --------------------------------------------------------
    def _style(self):
        self.style = ttk.Style(self)
        self.theme = "clam" if "clam" in self.style.theme_names() else "default"
        self.style.theme_use(self.theme)

    # ---- layout ---------------------------------------------------------
    def _build_ui(self):
        self.style.configure("Title.TLabel", font=("Sans", 13, "bold"),
                             foreground=self.th["accent"])
        self.style.configure("TFrame", background=self.th["bg"])
        self.style.configure("TLabel", background=self.th["bg"],
                             foreground=self.th["text"])
        self.style.configure("TButton", background=self.th["accent"],
                             foreground="#ffffff")
        self.style.map("TButton",
                       background=[("active", self.th["accent"]),
                                   ("!active", self.th["accent"])])

        pad = dict(padx=6, pady=5)
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="File Organizer", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        def row_labels(r, src=True, dst=True):
            if src:
                ttk.Label(top, text="Source folder:").grid(row=r, column=0,
                                                             sticky="w", **pad)
            if dst:
                ttk.Label(top, text="Destination:").grid(row=r, column=1,
                                                          sticky="w", **pad)

        self.src = self._path_entry(top, 1, "Choose messy folder…",
                                    src=True, dst=False, attr="src_var")
        self.dst = self._path_entry(top, 1, "(same as source)", src=False,
                                    dst=True, attr="dst_var")

        opts = ttk.Frame(top)
        opts.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self._copy_var = tk.BooleanVar()
        ttk.Checkbutton(opts, text="Copy instead of move",
                        variable=self._copy_var).grid(
            row=0, column=0, sticky="w")
        self._shard_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Shard by first letter (big folders)",
                        variable=self._shard_var).grid(row=0, column=1,
                                                        padx=10, sticky="w")
        self._tree_var = tk.BooleanVar()
        ttk.Checkbutton(opts, text="Preserve subfolders",
                        variable=self._tree_var).grid(row=0, column=2,
                                                      sticky="w")
        self._dry_var = tk.BooleanVar()
        ttk.Checkbutton(opts, text="Dry-run (no moves)",
                        variable=self._dry_var).grid(row=1, column=0,
                                                     columnspan=3,
                                                     sticky="w",
                                                     pady=(4, 0))

        ttk.Label(opts, text="Projects (comma-sep):").grid(row=1, column=0,
                                                           sticky="w")
        self.projects = ttk.Entry(opts, width=26)
        self.projects.grid(row=1, column=1, sticky="ew")
        ttk.Label(opts, text="Project regex:").grid(row=2, column=0, sticky="w")
        self.regex = ttk.Entry(opts, width=26)
        self.regex.grid(row=2, column=1, sticky="ew")

        ttk.Label(opts, text="Max workers:").grid(row=1, column=2, sticky="e")
        self.workers = ttk.Spinbox(opts, from_=1, to=32, width=5,
                                   increment=1)
        self.workers.set("8")
        ttk.Label(opts, text="Min size (bytes):").grid(row=2, column=2,
                                                      sticky="e")
        self.minsize = ttk.Spinbox(opts, from_=0, to=10_000_000, width=5)
        self.minsize.set("0")

        btns = ttk.Frame(top)
        btns.grid(row=3, column=0, columnspan=3, pady=(10, 4))
        ttk.Button(btns, text="Browse source…",
                   command=self._pick_source).grid(row=0, column=0, padx=3)
        self.start_btn = ttk.Button(btns, text="Sort →", command=self.start)
        self.start_btn.grid(row=0, column=1, padx=3)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=2, padx=3)
        self.status = ttk.Label(btns, text="Ready")
        self.status.grid(row=0, column=3, sticky="w", padx=10)

        self.min_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Minimize on start",
                        variable=self.min_var).grid(
            row=4, column=0, columnspan=3, sticky="w")

        logbox = scrolledtext.ScrolledText(self, height=16, wrap="word",
                                            font=("Monospace", 9),
                                            background=self.th["log_bg"],
                                            foreground=self.th["log_fg"],
                                            relief="flat",
                                            highlightthickness=1,
                                            highlightcolor=self.th["border"])
        logbox.pack(fill="both", expand=True, padx=8, pady=8)
        self.logbox = logbox

        self.after(100, self._pulse)

    def _path_entry(self, parent, row, placeholder, src=False, dst=False, attr=None):
        var = tk.StringVar()
        if attr:
            setattr(self, attr, var)
        e = ttk.Entry(parent, textvariable=var, width=52)
        if src:
            e.grid(row=row, column=0, sticky="we", pady=2)
        elif dst:
            e.grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
        return e

    # ---- actions --------------------------------------------------------
    def _pick_source(self):
        d = filedialog.askdirectory()
        if d:
            self.src_var.set(d)
            if not self.dst_var.get():
                self.dst_var.set(d)

    def _collect(self):
        dest = self.dst_var.get().strip()
        dest = dest or None
        return dict(
            dest=dest,
            dry_run=self._dry_var.get(),
            copy=self._copy_var.get(),
            max_workers=max(1, int(self.workers.get())),
            project_names=self.projects.get().strip(),
            project_regex=self.regex.get().strip() or None,
            shard=self._shard_var.get(),
            keep_tree=self._tree_var.get(),
            min_size=int(self.minsize.get() or 0),
        )

    def start(self):
        src = self.src_var.get().strip()
        if not src:
            messagebox.showerror("Bad input", "Pick a source folder first.")
            return
        try:
            workers = max(1, int(self.workers.get()))
        except ValueError:
            messagebox.showerror("Bad input", "Workers must be a number.")
            return

        self.stop_event.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status.config(text="Running…")
        self.logbox.delete(1.0, tk.END)
        self.logbox.insert(tk.END, f"Sorting {src} …\n")

        self.thread = threading.Thread(
            target=self._run, args=(src, workers), daemon=True)
        self.thread.start()
        self.after(150, self._maybe_minimize)

    def _maybe_minimize(self):
        if self.min_var.get():
            try:
                self.iconify()
            except tk.TclError:
                pass

    def _run(self, src, workers):
        cfg = self._collect()

        def on_event(line):
            self.after(0, lambda: self._log(line))

        stats = organize(src, on_event=on_event,
                         stop_event=self.stop_event, **cfg)
        self.after(0, lambda: self._finish(stats))

    def _log(self, line):
        self.logbox.insert(tk.END, line + "\n")
        self.logbox.see(tk.END)

    def _finish(self, stats):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status.config(text="Done")
        moved = stats["moved"] + stats["copied"]
        self._log(f"--- {moved:,} moved, {stats['failed']} failed, "
                  f"{stats['total']:,} total ---")
        for cat, n in stats["by_category"].items():
            self._log(f"  {cat:<14} {n:,}")

    def stop(self):
        self.stop_event.set()
        self.status.config(text="Stopping…")

    def _pulse(self):
        if self.thread and self.thread.is_alive():
            self.status.config(text="Running…")
        self.after(1000, self._pulse)


def main():
    OrganizerGUI().mainloop()


if __name__ == "__main__":
    main()
