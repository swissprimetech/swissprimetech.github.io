#!/usr/bin/env python3
"""Creates a sample Swiss invoice PDF for testing purposes."""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from pathlib import Path

def create_invoice(path: str):
    c = canvas.Canvas(path, pagesize=A4)
    w, h = A4

    # Header
    c.setFont("Helvetica-Bold", 20)
    c.drawString(20*mm, h - 30*mm, "Muster AG")
    c.setFont("Helvetica", 10)
    c.drawString(20*mm, h - 38*mm, "Bahnhofstrasse 1  |  8001 Zürich  |  muster@example.ch")

    c.setFont("Helvetica-Bold", 16)
    c.drawString(20*mm, h - 60*mm, "RECHNUNG")

    c.setFont("Helvetica", 10)
    c.drawString(20*mm, h - 70*mm, "Rechnungsnummer: 2024-0042")
    c.drawString(20*mm, h - 76*mm, "Datum: 28.03.2026")
    c.drawString(20*mm, h - 82*mm, "Fälligkeit: 28.04.2026")

    # Recipient
    c.setFont("Helvetica-Bold", 10)
    c.drawString(120*mm, h - 70*mm, "Kundenmaier GmbH")
    c.setFont("Helvetica", 10)
    c.drawString(120*mm, h - 76*mm, "Seestrasse 42")
    c.drawString(120*mm, h - 82*mm, "3001 Bern")

    # Table header
    y = h - 105*mm
    c.setFillColorRGB(0.15, 0.25, 0.45)
    c.rect(20*mm, y, w - 40*mm, 8*mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(22*mm, y + 2.5*mm, "Beschreibung")
    c.drawRightString(w - 22*mm, y + 2.5*mm, "Betrag CHF")

    # Items
    items = [
        ("Softwareentwicklung (40h à CHF 150.00)", 6000.00),
        ("Projektmanagement (8h à CHF 120.00)",    960.00),
        ("Infrastruktur & Hosting (März 2026)",    240.00),
        ("Reisekosten",                             85.50),
    ]
    c.setFillColorRGB(0, 0, 0)
    y -= 8*mm
    subtotal = 0.0
    for i, (desc, amount) in enumerate(items):
        if i % 2 == 0:
            c.setFillColorRGB(0.95, 0.97, 1.0)
            c.rect(20*mm, y - 6.5*mm, w - 40*mm, 8*mm, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 10)
        c.drawString(22*mm, y - 4*mm, desc)
        c.drawRightString(w - 22*mm, y - 4*mm, f"{amount:,.2f}".replace(",", "'"))
        subtotal += amount
        y -= 8*mm

    # Subtotal line
    y -= 4*mm
    c.setStrokeColorRGB(0.7, 0.7, 0.7)
    c.line(20*mm, y, w - 20*mm, y)
    y -= 6*mm

    vat_rate = 8.1
    vat = round(subtotal * vat_rate / 100, 2)
    gross = round(subtotal + vat, 2)

    def amount_row(label, value, bold=False):
        nonlocal y
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, 10)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(22*mm, y, label)
        c.drawRightString(w - 22*mm, y, f"{value:,.2f}".replace(",", "'"))
        y -= 7*mm

    amount_row("Zwischensumme (exkl. MWST):", subtotal)
    amount_row(f"MWST {vat_rate}%:", vat)

    c.setStrokeColorRGB(0.15, 0.25, 0.45)
    c.setLineWidth(1.5)
    c.line(20*mm, y + 4*mm, w - 20*mm, y + 4*mm)

    amount_row(f"Bruttobetrag CHF:", gross, bold=True)

    # Footer
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(20*mm, 20*mm, "Bankverbindung: IBAN CH56 0483 5012 3456 7800 9  |  ZKB Zürich  |  Zahlbar innert 30 Tagen")

    c.save()
    print(f"Test-Rechnung erstellt: {path}")
    print(f"  Zwischensumme : CHF {subtotal:,.2f}".replace(",", "'"))
    print(f"  MWST {vat_rate}%    : CHF {vat:,.2f}".replace(",", "'"))
    print(f"  Bruttobetrag  : CHF {gross:,.2f}".replace(",", "'"))
    print(f"  Nettobetrag   : CHF {subtotal:,.2f}  ← erwartet nach Verarbeitung".replace(",", "'"))

if __name__ == "__main__":
    out = Path(__file__).parent / "test_rechnung.pdf"
    create_invoice(str(out))
