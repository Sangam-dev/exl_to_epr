"""Create a sample workbook for Segment 1 demos and tests.

Run from anywhere:

    python scripts/make_sample_excel.py

The file is written to ``data/input/invoices.xlsx`` and is safe to regenerate.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "data" / "input" / "invoices.xlsx"

HEADERS = [
    "Invoice Number",
    "Customer Name",
    "Invoice Date",
    "Amount",
    "Payment Method",
    "Notes",
    "Internal Code",
    "Currency",
    "Total With Tax",
    "Agent Status",
]

ROWS = [
    ["INV-0001", "Acme Corp", date(2026, 1, 5), 1250.00, "Card", "First invoice", "A-1", "USD", None, ""],
    ["INV-0002", "Blue Ridge Ltd", date(2026, 1, 8), 480.50, "Transfer", "Paid in full", "A-2", "USD", None, "DONE"],
    ["INV-0003", "Cedar Works", date(2026, 1, 12), 99.99, "Cash", "", "A-3", "USD", None, ""],
    ["INV-0004", "Delta Studio", date(2026, 2, 1), 3200.00, "Card", "Net 30", "A-4", "EUR", None, ""],
    ["INV-0005", "Echo Supply", date(2026, 2, 3), 75.25, "Card", "Urgent", "A-5", "USD", None, ""],
]

WIDTHS = {
    "A": 16,
    "B": 22,
    "C": 14,
    "D": 12,
    "E": 16,
    "F": 24,
    "G": 14,
    "H": 10,
    "I": 16,
    "J": 14,
}


def main() -> int:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoices"

    sheet.append(HEADERS)
    for row_number, row in enumerate(ROWS, start=2):
        sheet.append(row)
        sheet.cell(row_number, 9, f"=D{row_number}*1.2")

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for column, width in WIDTHS.items():
        sheet.column_dimensions[column].width = width

    for row_number in range(2, len(ROWS) + 2):
        sheet.cell(row_number, 3).number_format = "yyyy-mm-dd"
        sheet.cell(row_number, 4).number_format = "#,##0.00"
        sheet.cell(row_number, 9).number_format = "#,##0.00"

    sheet.freeze_panes = "A2"

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(OUTPUT)

    done_count = sum(1 for row in ROWS if row[-1] == "DONE")
    print(f"Created {OUTPUT}")
    print(f"{len(ROWS)} data rows | {done_count} marked DONE | formula in column I")
    print(f"Headers: {', '.join(HEADERS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
