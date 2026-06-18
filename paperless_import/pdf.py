"""PDF generation from order data."""
import tempfile
from fpdf import FPDF


def latin_safe(text: str) -> str:
    """Encode text to be Latin-1 compatible for fpdf2."""
    if not text:
        return ""
    return text.encode("latin-1", "replace").decode("latin-1")


def generate_order_pdf(
    merchant: str,
    title: str = "Order Confirmation",
    order_date: str = "",
    order_number: str = "",
    total: float = None,
    shipping: float = None,
    tax: float = None,
    items: list[dict] = None,
    source_subject: str = "",
    order_url: str = "",
) -> str:
    """Generate a PDF file and return the path. Items are list of {'name': str, 'qty': int}."""
    pdf = FPDF()
    pdf.add_page()

    # Header
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"{merchant} - {title}", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    if order_date:
        pdf.cell(0, 6, f"Order Date: {order_date}", new_x="LMARGIN", new_y="NEXT")
    if order_number:
        pdf.cell(0, 6, f"Order #: {order_number}", new_x="LMARGIN", new_y="NEXT")
    if total is not None:
        pdf.cell(0, 6, f"Total: ${total:.2f}", new_x="LMARGIN", new_y="NEXT")
    if shipping is not None:
        pdf.cell(0, 6, f"Shipping: ${shipping:.2f}", new_x="LMARGIN", new_y="NEXT")
    if tax is not None:
        pdf.cell(0, 6, f"Tax: ${tax:.2f}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)

    # Items
    if items:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Items:", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        for item in items:
            name = latin_safe(item.get("name", ""))
            qty = item.get("qty", 1)
            price = item.get("price")
            line = f"  x{qty} {name}"
            if price is not None:
                line += f" - ${price:.2f}"
            pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)

    # Order link
    if order_url:
        pdf.set_font("Helvetica", "", 9)
        safe_url = latin_safe(order_url)
        pdf.cell(0, 5, f"Order Link: {safe_url}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Source line
    if source_subject:
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 5, f"Source: {latin_safe(source_subject)}", new_x="LMARGIN", new_y="NEXT")

    path = tempfile.mktemp(suffix=".pdf")
    pdf.output(path)
    return path
