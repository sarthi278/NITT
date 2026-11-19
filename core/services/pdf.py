from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.models import CompanySettings

ONES = (
    "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
    "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
    "Sixteen", "Seventeen", "Eighteen", "Nineteen",
)
TENS = (
    "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
    "Eighty", "Ninety",
)


def _number_to_words(value):
    if value < 20:
        return ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return TENS[tens] + (f" {ONES[ones]}" if ones else "")
    if value < 1000:
        hundreds, remainder = divmod(value, 100)
        result = f"{ONES[hundreds]} Hundred"
        return result + (f" {_number_to_words(remainder)}" if remainder else "")
    for scale, name in (
        (10_000_000, "Crore"),
        (100_000, "Lakh"),
        (1_000, "Thousand"),
    ):
        if value >= scale:
            quotient, remainder = divmod(value, scale)
            result = f"{_number_to_words(quotient)} {name}"
            return result + (f" {_number_to_words(remainder)}" if remainder else "")
    return ""


def amount_in_words(amount):
    rounded = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not rounded.is_finite() or rounded < 0:
        raise ValueError("Invoice amount must be a finite, non-negative value.")
    rupees = int(rounded)
    paise = int((rounded - Decimal(rupees)) * 100)
    words = f"Rupees {_number_to_words(rupees)}"
    if paise:
        words += f" and {_number_to_words(paise)} Paise"
    return f"{words} Only"


def _display(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _paragraph(value, style):
    return Paragraph(escape(_display(value)), style)


def build_report_pdf(*, title, columns, rows, start_date=None, end_date=None):
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "ReportCell",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=6.5,
        leading=8,
        spaceAfter=0,
    )
    header_style = ParagraphStyle(
        "ReportHeader",
        parent=body_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    story = [
        Paragraph(escape(title), styles["Title"]),
        Spacer(1, 3 * mm),
    ]
    date_range = " · ".join(
        part
        for part in (
            f"From {start_date}" if start_date else "",
            f"To {end_date}" if end_date else "",
        )
        if part
    )
    if date_range:
        story.extend([Paragraph(escape(date_range), styles["Normal"]), Spacer(1, 3 * mm)])

    table_data = [[_paragraph(value, header_style) for value in columns]]
    table_data.extend([
        [_paragraph(value, body_style) for value in row]
        for row in rows
    ])
    page_width = landscape(A4)[0] - 24 * mm
    column_width = page_width / max(len(columns), 1)
    table = Table(
        table_data,
        colWidths=[column_width] * len(columns),
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#153d35")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dce5df")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f4")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    document.build(story)
    return output.getvalue()


def build_invoice_pdf(invoice):
    company = CompanySettings.objects.first()
    company_name = invoice.company_name or (company.name if company else "NIT Demo Snacks")
    company_address = invoice.company_address or (company.address if company else "")
    company_state = invoice.company_state or (company.state if company else "")
    company_gstin = invoice.company_gstin or (company.gstin if company else "")
    company_contact = " · ".join(
        value for value in (
            invoice.company_phone or (company.phone if company else ""),
            invoice.company_email or (company.email if company else ""),
        ) if value
    )
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "InvoiceCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        spaceAfter=0,
    )
    header_style = ParagraphStyle(
        "InvoiceHeader",
        parent=body_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    story = [
        Paragraph("Tax Invoice", styles["Title"]),
        Paragraph(escape(company_name), styles["Heading2"]),
        Paragraph(
            escape(" · ".join(
                value for value in (company_address, company_state) if value
            )),
            body_style,
        ),
        Paragraph(
            escape(
                " · ".join(
                    value for value in (
                        f"GSTIN {company_gstin}" if company_gstin else "",
                        company_contact,
                    )
                    if value
                )
            ),
            body_style,
        ),
        Paragraph(
            escape(f"Invoice {invoice.number} · {invoice.invoice_date}"),
            styles["Normal"],
        ),
        Spacer(1, 5 * mm),
        Paragraph(
            escape(
                f"Bill to: {invoice.customer.name} · {invoice.customer.state} · "
                f"GSTIN {invoice.customer_gstin or '—'}"
            ),
            body_style,
        ),
        Paragraph(
            escape(
                f"Place of supply: {invoice.place_of_supply} · "
                f"Supplier GSTIN {invoice.company_gstin or company_gstin or '—'}"
            ),
            body_style,
        ),
        Spacer(1, 5 * mm),
    ]
    columns = ("Product", "HSN", "Qty", "Rate", "Taxable", "GST", "Total")
    table_data = [[_paragraph(value, header_style) for value in columns]]
    for line in invoice.lines.all():
        tax = (
            f"IGST {line.igst}"
            if line.igst
            else f"CGST {line.cgst} / SGST {line.sgst}"
        )
        table_data.append([
            _paragraph(line.product_name, body_style),
            _paragraph(line.hsn_code, body_style),
            _paragraph(line.quantity, body_style),
            _paragraph(f"INR {line.unit_price}", body_style),
            _paragraph(f"INR {line.taxable_value}", body_style),
            _paragraph(tax, body_style),
            _paragraph(f"INR {line.total}", body_style),
        ])
    table = Table(
        table_data,
        colWidths=[42 * mm, 14 * mm, 15 * mm, 22 * mm, 22 * mm, 28 * mm, 25 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#153d35")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dce5df")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f4")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 5 * mm))

    totals = [
        ["Taxable value", f"INR {invoice.taxable_total}"],
    ]
    if invoice.igst_total:
        totals.append(["IGST", f"INR {invoice.igst_total}"])
    else:
        totals.extend([
            ["CGST", f"INR {invoice.cgst_total}"],
            ["SGST", f"INR {invoice.sgst_total}"],
        ])
    totals.append(["Invoice total", f"INR {invoice.total}"])
    total_table = Table(
        [[_paragraph(label, body_style), _paragraph(value, body_style)] for label, value in totals],
        colWidths=[40 * mm, 35 * mm],
        hAlign="RIGHT",
    )
    total_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#153d35")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(total_table)
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            escape(f"Amount in words: {amount_in_words(invoice.total)}"),
            body_style,
        )
    )
    document.build(story)
    return output.getvalue()
