#!/usr/bin/env python3
"""
PDF Invoice Net Amount Tool
============================
Scans PDF invoices, extracts gross amount (Bruttobetrag) and VAT (MWST),
calculates the net amount (Nettobetrag = Bruttobetrag - MWST) and appends
it to the PDF.

Usage:
    python pdf_invoice_net_amount.py <path>         # single file or directory
    python pdf_invoice_net_amount.py <path> --dry-run
    python pdf_invoice_net_amount.py <path> --output-dir /out
"""

import argparse
import io
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


# ---------------------------------------------------------------------------
# Amount patterns for Swiss/German invoices (handles 1'234.56 and 1.234,56)
# ---------------------------------------------------------------------------

# Matches numbers like: 1'234.56 | 1.234,56 | 1234.56 | 1234,56
_NUM = r"[\d]{1,3}(?:['\.\s]?\d{3})*(?:[.,]\d{1,2})?"

# Labels that indicate the gross/total amount
_GROSS_LABELS = (
    r"Brutto(?:betrag|preis|total)?",
    r"Gesamt(?:betrag|summe|total)?",
    r"Rechnungs(?:betrag|total|summe)",
    r"Total\s+(?:inkl\.?\s*(?:MWST|MwSt|Steuer))?",
    r"Gesamttotal",
    r"Zahlbar",
    r"Zu\s+zahlen(?:der\s+Betrag)?",
)

# Labels that indicate the VAT amount
_VAT_LABELS = (
    r"(?:MWST|MwSt\.?|Mehrwertsteuer)",
    r"(?:VAT|USt\.?|Umsatzsteuer)",
)

_CURRENCY = r"(?:CHF|EUR|USD|GBP)?"
_GROSS_RE = re.compile(
    r"(?:" + "|".join(_GROSS_LABELS) + r")"
    r"[^\d\-]*"
    r"(" + _CURRENCY + r"\s*" + _NUM + r")",
    re.IGNORECASE,
)

# Strict: MWST WITH percentage rate (e.g. "MWST 8.1%: 590.13")
_VAT_STRICT_RE = re.compile(
    r"(?:" + "|".join(_VAT_LABELS) + r")"
    r"\s*\d{1,2}[.,]\d{1,2}\s*%"        # REQUIRED rate
    r"[^\d\-]*"
    r"(" + _CURRENCY + r"\s*" + _NUM + r")",
    re.IGNORECASE,
)

# Loose: MWST anywhere, used for findall fallback
_VAT_RE = re.compile(
    r"(?:" + "|".join(_VAT_LABELS) + r")"
    r"(?:\s*\d{1,2}[.,]\d{1,2}\s*%)?"   # optional rate
    r"[^\d\-]*"
    r"(" + _CURRENCY + r"\s*" + _NUM + r")",
    re.IGNORECASE,
)


def _parse_amount(raw: str) -> Optional[float]:
    """Convert a raw amount string to float, handling Swiss/German formatting."""
    if not raw:
        return None
    # Strip currency symbols and whitespace
    s = re.sub(r"[A-Z$€£\s]", "", raw.upper())
    # Determine decimal separator: if last separator has exactly 2 digits after → decimal
    # Swiss format: 1'234.56  →  replace ' then . is decimal
    # German format: 1.234,56 →  replace . then , is decimal
    if "'" in s:
        # Swiss: apostrophe as thousands separator
        s = s.replace("'", "")
        # Now either 1234.56 or 1234
        s = s.replace(",", ".")
    elif "," in s and "." in s:
        # Determine which is decimal by position
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_comma > last_dot:
            # German: 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:
            # English: 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        # Only comma → treat as decimal separator
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class InvoiceAmounts:
    gross: Optional[float] = None   # Bruttobetrag
    vat: Optional[float] = None     # MWST-Betrag
    net: Optional[float] = None     # Nettobetrag (calculated)
    currency: str = "CHF"


def extract_amounts(text: str) -> InvoiceAmounts:
    """
    Extract gross and VAT amounts from invoice text.
    Returns an InvoiceAmounts dataclass with net calculated when possible.
    """
    result = InvoiceAmounts()

    # Detect currency
    cur_match = re.search(r"\b(CHF|EUR|USD|GBP)\b", text)
    if cur_match:
        result.currency = cur_match.group(1)

    # Extract gross
    gross_match = _GROSS_RE.search(text)
    if gross_match:
        result.gross = _parse_amount(gross_match.group(1))

    # Extract VAT — prefer strict match (MWST with percentage rate)
    vat_match = _VAT_STRICT_RE.search(text)
    if vat_match:
        result.vat = _parse_amount(vat_match.group(1))
    else:
        # Fallback: collect all MWST matches, exclude implausibly large values
        # (e.g. "exkl. MWST: 7'285.50" is subtotal, not VAT)
        candidates = [_parse_amount(m) for m in _VAT_RE.findall(text)]
        candidates = [v for v in candidates if v is not None and v > 0]
        if candidates:
            # VAT is always smaller than gross; pick the smallest plausible value
            candidates.sort()
            result.vat = candidates[0]

    # Calculate net
    if result.gross is not None and result.vat is not None:
        result.net = round(result.gross - result.vat, 2)

    return result


def _format_amount(value: float, currency: str) -> str:
    """Format a float as a currency string (Swiss style)."""
    # Format with thousands separator
    parts = f"{value:,.2f}".split(".")
    integer_part = parts[0].replace(",", "'")
    return f"{currency} {integer_part}.{parts[1]}"


def create_net_amount_overlay(
    gross: float,
    vat: float,
    net: float,
    currency: str,
    page_width: float,
    page_height: float,
) -> bytes:
    """
    Create a PDF page (overlay) that contains the net amount annotation.
    Returns the PDF bytes.
    """
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(page_width, page_height))

    # Box dimensions
    box_x = 20 * mm
    box_y = 15 * mm
    box_w = page_width - 40 * mm
    box_h = 38 * mm

    # Background
    c.setFillColor(colors.HexColor("#F0F7FF"))
    c.setStrokeColor(colors.HexColor("#2563EB"))
    c.setLineWidth(1.5)
    c.roundRect(box_x, box_y, box_w, box_h, 4 * mm, fill=1, stroke=1)

    # Title
    c.setFillColor(colors.HexColor("#1E3A5F"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(box_x + 6 * mm, box_y + box_h - 9 * mm, "Nettobetrag (berechnet)")

    # Divider line
    c.setStrokeColor(colors.HexColor("#2563EB"))
    c.setLineWidth(0.5)
    c.line(box_x + 4 * mm, box_y + box_h - 12 * mm,
           box_x + box_w - 4 * mm, box_y + box_h - 12 * mm)

    # Row helper
    def draw_row(label: str, value: str, y_offset: float, bold: bool = False):
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, 10)
        c.setFillColor(colors.HexColor("#374151"))
        c.drawString(box_x + 6 * mm, box_y + y_offset, label)
        c.setFillColor(colors.HexColor("#111827") if bold else colors.HexColor("#374151"))
        c.drawRightString(box_x + box_w - 6 * mm, box_y + y_offset, value)

    draw_row("Bruttobetrag:", _format_amount(gross, currency), 22 * mm)
    draw_row(f"abzüglich MWST:", f"- {_format_amount(vat, currency)}", 15 * mm)

    # Separator before net
    c.setStrokeColor(colors.HexColor("#9CA3AF"))
    c.setLineWidth(0.4)
    c.line(box_x + 4 * mm, box_y + 12.5 * mm,
           box_x + box_w - 4 * mm, box_y + 12.5 * mm)

    draw_row("Nettobetrag:", _format_amount(net, currency), 8 * mm, bold=True)

    c.save()
    packet.seek(0)
    return packet.read()


def process_pdf(
    input_path: Path,
    output_path: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> Optional[InvoiceAmounts]:
    """
    Process a single PDF file: extract amounts, calculate net, write result.
    Returns InvoiceAmounts on success, None on failure.
    """
    try:
        full_text = ""
        with pdfplumber.open(input_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"

        amounts = extract_amounts(full_text)

        if verbose:
            print(f"  Brutto : {amounts.gross}")
            print(f"  MWST   : {amounts.vat}")
            print(f"  Netto  : {amounts.net}")

        if amounts.gross is None:
            print(f"  [WARN] Bruttobetrag nicht gefunden in: {input_path.name}")
            return amounts
        if amounts.vat is None:
            print(f"  [WARN] MWST nicht gefunden in: {input_path.name}")
            return amounts
        if amounts.net is None:
            return amounts

        if dry_run:
            print(
                f"  [DRY-RUN] Nettobetrag würde ergänzt: "
                f"{_format_amount(amounts.net, amounts.currency)}"
            )
            return amounts

        # Read original PDF
        reader = PdfReader(str(input_path))
        writer = PdfWriter()

        # Copy all pages
        for page in reader.pages:
            writer.add_page(page)

        # Get last page dimensions for the overlay
        last_page = reader.pages[-1]
        pw = float(last_page.mediabox.width)
        ph = float(last_page.mediabox.height)

        # Create overlay with net amount box
        overlay_bytes = create_net_amount_overlay(
            gross=amounts.gross,
            vat=amounts.vat,
            net=amounts.net,
            currency=amounts.currency,
            page_width=pw,
            page_height=ph,
        )

        overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
        overlay_page = overlay_reader.pages[0]

        # Merge overlay onto last page
        last_writer_page = writer.pages[-1]
        last_writer_page.merge_page(overlay_page)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            writer.write(f)

        print(
            f"  [OK] Nettobetrag ergänzt: "
            f"{_format_amount(amounts.net, amounts.currency)} → {output_path}"
        )
        return amounts

    except Exception as exc:
        print(f"  [ERROR] {input_path.name}: {exc}", file=sys.stderr)
        return None


def process_path(
    input_path: Path,
    output_dir: Optional[Path],
    dry_run: bool,
    verbose: bool,
    overwrite: bool,
) -> dict:
    """Process a file or all PDFs in a directory. Returns summary stats."""
    stats = {"processed": 0, "skipped": 0, "errors": 0, "no_amounts": 0}

    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(input_path.rglob("*.pdf"))
    else:
        print(f"[ERROR] Pfad nicht gefunden: {input_path}", file=sys.stderr)
        sys.exit(1)

    for pdf_file in files:
        print(f"\nVerarbeite: {pdf_file}")

        # Determine output path
        if output_dir:
            rel = pdf_file.relative_to(input_path) if input_path.is_dir() else pdf_file.name
            out_path = output_dir / rel
        else:
            stem = pdf_file.stem
            suffix = "_netto" if not stem.endswith("_netto") else ""
            out_path = pdf_file.parent / f"{stem}{suffix}.pdf"

        if not overwrite and out_path.exists() and not dry_run:
            print(f"  [SKIP] Ausgabedatei existiert bereits: {out_path}")
            stats["skipped"] += 1
            continue

        result = process_pdf(pdf_file, out_path, dry_run=dry_run, verbose=verbose)
        if result is None:
            stats["errors"] += 1
        elif result.net is None:
            stats["no_amounts"] += 1
        else:
            stats["processed"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="PDF-Rechnungen: Nettobetrag (Brutto - MWST) berechnen und ergänzen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python pdf_invoice_net_amount.py rechnung.pdf
  python pdf_invoice_net_amount.py ./rechnungen/
  python pdf_invoice_net_amount.py ./rechnungen/ --output-dir ./ausgabe/
  python pdf_invoice_net_amount.py rechnung.pdf --dry-run --verbose
        """,
    )
    parser.add_argument("path", type=Path, help="PDF-Datei oder Verzeichnis mit PDFs")
    parser.add_argument(
        "--output-dir", "-o", type=Path, default=None,
        help="Ausgabeverzeichnis (Standard: gleiches Verzeichnis, Suffix _netto)",
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true",
        help="Nur Beträge ausgeben, keine Dateien schreiben",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Extrahierte Rohdaten ausgeben",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Bestehende Ausgabedateien überschreiben",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("  PDF Invoice Net Amount Tool")
    print("  Nettobetrag = Bruttobetrag - MWST")
    print("=" * 60)

    stats = process_path(
        input_path=args.path,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
        overwrite=args.overwrite,
    )

    print("\n" + "=" * 60)
    print(f"  Verarbeitet : {stats['processed']}")
    print(f"  Übersprungen: {stats['skipped']}")
    print(f"  Fehler      : {stats['errors']}")
    print(f"  Ohne Beträge: {stats['no_amounts']}")
    print("=" * 60)

    if stats["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
