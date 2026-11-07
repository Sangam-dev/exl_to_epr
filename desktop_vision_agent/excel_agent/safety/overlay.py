"""Always-on-top status overlay and shortcut reminder (Segment 3)."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class NullOverlay:
    """Overlay stand-in used when the overlay is disabled or unavailable."""

    def start(self) -> None:
        pass

    def update(self, state: str, detail: str = "") -> None:
        pass

    def stop(self) -> None:
        pass


class StatusOverlay:
    """A small always-on-top window showing the current safety state."""

    def __init__(self, labels: dict[str, str]) -> None:
        self._labels = dict(labels)
        self._state = "idle"
        self._detail = ""
        self._data_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._root = None
        self._label = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="safety-overlay", daemon=True
        )
        self._thread.start()
        self._ready.wait(3.0)

    def update(self, state: str, detail: str = "") -> None:
        with self._data_lock:
            self._state = state
            self._detail = detail

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and threading.current_thread() is not thread:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            logger.exception("overlay unavailable: tkinter could not be imported")
            self._ready.set()
            return

        try:
            root = tk.Tk()
            self._root = root
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.92)
            root.configure(bg="#333333")

            label = tk.Label(
                root,
                text="",
                anchor="w",
                justify="left",
                font=("Segoe UI", 9, "bold"),
                fg="white",
                bg="#333333",
                padx=10,
                pady=6,
            )
            label.pack(fill="both", expand=True)
            self._label = label

            screen_width = root.winfo_screenwidth()
            root.geometry(f"400x40+{max(0, screen_width - 420)}+12")
            self._ready.set()
            root.after(100, self._poll)
            root.mainloop()
        except Exception:
            logger.exception("overlay failed")
            self._ready.set()
        finally:
            self._root = None
            self._label = None

    def _poll(self) -> None:
        with self._data_lock:
            state = self._state
            detail = self._detail

        if self._stop.is_set():
            if self._root is not None:
                self._root.destroy()
            return

        stop = self._labels.get("stop", "Alt+T")
        pause = self._labels.get("pause", "Alt+P")
        resume = self._labels.get("resume", "Alt+R")

        if state == "running":
            color = "#1f6f3f"
            text = f"AGENT RUNNING | {stop} stop | {pause} pause"
        elif state == "paused":
            color = "#8a6d1a"
            text = f"AGENT PAUSED | {resume} resume | {stop} stop"
        elif state == "stopped":
            color = "#7a2a2a"
            text = f"AGENT STOPPED | input released | {detail}".rstrip(" |")
        else:
            color = "#333333"
            text = "AGENT IDLE"

        if self._label is not None:
            self._label.configure(text=text, bg=color)
        if self._root is not None:
            self._root.configure(bg=color)
            self._root.after(100, self._poll)
