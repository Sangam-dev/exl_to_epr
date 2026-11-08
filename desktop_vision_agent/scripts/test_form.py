"""Tkinter test fixture for Excel Desktop Agent (Segment 9).

Mirrors scripts/test_form.ps1 using Tkinter, providing a native Windows fixture
without requiring PowerShell STA execution permissions.

Window Title: "Excel Agent Test Form"
Controls:
    - Invoice Number (Entry)
    - Customer Name  (Entry)
    - Amount         (Entry)
    - Notes          (Entry)
    - Save           (Button)
    - Clear          (Button)
    - Status         (Label)
    - Saved records  (Listbox)

Run directly:
    python scripts/test_form.py
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import tkinter as tk
from datetime import datetime

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class TestFormApp:
    """Tkinter fixture window matching the WinForms test form."""

    def __init__(self, root: tk.Tk, auto_close_seconds: float = 0.0) -> None:
        self.root = root
        self.root.title("Excel Agent Test Form")
        self.root.geometry("520x450")
        self.root.resizable(False, False)

        # Build form layout
        self.fields: dict[str, tk.Entry] = {}
        field_specs = [
            ("Invoice Number", "Invoice Number"),
            ("Customer Name", "Customer Name"),
            ("Amount", "Amount"),
            ("Notes", "Notes"),
        ]

        row = 0
        for label_text, key in field_specs:
            lbl = tk.Label(root, text=label_text, width=15, anchor="w", font=("Segoe UI", 9))
            lbl.place(x=20, y=20 + row * 40, width=120, height=24)

            entry = tk.Entry(root, font=("Segoe UI", 9), name=key.lower().replace(" ", "_"))
            entry.place(x=150, y=20 + row * 40, width=340, height=24)
            self.fields[key] = entry
            row += 1

        # Action Buttons
        self.btn_save = tk.Button(
            root, text="Save", command=self.on_save, font=("Segoe UI", 9), width=10
        )
        self.btn_save.place(x=150, y=180, width=100, height=28)

        self.btn_clear = tk.Button(
            root, text="Clear", command=self.on_clear, font=("Segoe UI", 9), width=10
        )
        self.btn_clear.place(x=260, y=180, width=100, height=28)

        # Status Label
        self.lbl_status = tk.Label(
            root, text="Status: ready", anchor="w", font=("Segoe UI", 9, "italic"), fg="#333"
        )
        self.lbl_status.place(x=20, y=225, width=470, height=24)

        # Saved records listbox
        lbl_list = tk.Label(root, text="Saved Records:", anchor="w", font=("Segoe UI", 8, "bold"))
        lbl_list.place(x=20, y=255, width=120, height=18)

        self.lst_saved = tk.Listbox(root, font=("Consolas", 9))
        self.lst_saved.place(x=20, y=275, width=470, height=150)

        # Focus initial field
        self.fields["Invoice Number"].focus_set()

        if auto_close_seconds > 0:
            self.root.after(int(auto_close_seconds * 1000), self.root.destroy)

    def on_save(self) -> None:
        invoice = self.fields["Invoice Number"].get().strip()
        customer = self.fields["Customer Name"].get().strip()
        amount = self.fields["Amount"].get().strip()

        if not invoice:
            self.lbl_status.config(text="Status: invoice number is required", fg="#b00")
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        record = f"{invoice} | {customer} | {amount}"
        self.lst_saved.insert(tk.END, record)
        self.lbl_status.config(text=f"Status: saved {now_str}", fg="#060")

        self.on_clear(reset_status=False)

    def on_clear(self, reset_status: bool = True) -> None:
        for entry in self.fields.values():
            entry.delete(0, tk.END)
        self.fields["Invoice Number"].focus_set()
        if reset_status:
            self.lbl_status.config(text="Status: cleared", fg="#333")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Form Fixture")
    parser.add_argument("--timeout", type=float, default=0.0, help="Auto-close after N seconds")
    args = parser.parse_args()

    root = tk.Tk()
    _app = TestFormApp(root, auto_close_seconds=args.timeout)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
