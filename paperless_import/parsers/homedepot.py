"""Home Depot order confirmation email parser.

Handles emails from HomeDepot@orders.homedepot.com with subjects:
- "We received your order!" — order confirmation (has item details)
- "Thank you for your pickup order!" — pickup order confirmation
- "Shipping Confirmation for Order..." — shipping notification (no items)

HTML-only emails become a flat text stream via html2text with format:

    ORDER_HEADER_INFO ) ITEM_NAME (URL) Store SKU ... Unit Price $X.XX Qty N Item Total $X.XX

Extracts: order number, order date, items (name, qty, price), total.
"""

import re
from datetime import datetime
from email import message_from_string
from typing import Optional

import html2text

from . import BaseParser, OrderData, OrderItem


class HomeDepotParser(BaseParser):
    """Parse Home Depot order confirmation emails."""

    FROM_PATTERNS = [
        r"orders\.homedepot\.com",
        r"HomeDepot@orders\.homedepot",
    ]

    def matches(self, subject: str, from_addr: str) -> bool:
        return any(re.search(p, from_addr, re.I) for p in self.FROM_PATTERNS)

    @staticmethod
    def _extract_body(raw_content: str) -> str:
        """Extract plain text from the HTML-only email."""
        msg = message_from_string(raw_content)
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                if ct == "text/plain":
                    return payload.decode("utf-8", errors="replace")
                elif ct == "text/html":
                    return html2text.html2text(payload.decode("utf-8", errors="replace"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                ct = msg.get_content_type()
                if ct == "text/html":
                    return html2text.html2text(payload.decode("utf-8", errors="replace"))
                return payload.decode("utf-8", errors="replace")
        return ""

    def parse(
        self, raw_content: str, subject: str, from_addr: str, date_str: str
    ) -> Optional[OrderData]:
        body = self._extract_body(raw_content)
        if not body:
            return None

        order = OrderData(merchant="Home Depot", subject=subject)

        # --- Message-ID ---
        msg = message_from_string(raw_content)
        order.message_id = msg.get("Message-ID", "").strip().strip("<>")

        # --- Order Number ---
        m = re.search(r"Order\s*(?:#|Number)[:\s]*([A-Z]+\d+)", body, re.I)
        if m:
            order.order_number = m.group(1)

        # --- Order Date ---
        m = re.search(r"Order\s+Date[:\s]*([A-Za-z]+ \d+,? \d{4})", body, re.I)
        if m:
            try:
                dt = datetime.strptime(
                    m.group(1).replace(",", "").strip(), "%B %d %Y"
                )
                order.order_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # Fallback: use envelope date
        if not order.order_date:
            from paperless_import.parsers.amazon import _parse_date as fallback_date
            order.order_date = fallback_date(date_str)

        # --- Items ---
        # Flattened html2text structure:
        #   [URL] ITEM_NAME [URL] Store SKU#... Unit Price $X.XX Qty N Item Total $X.XX [Est Arrival...]
        # Pattern repeats for each item. First item's leading URL may be absent.
        item_blocks = re.finditer(
            r"(?:\(https?://[^\)]+\)\s+)?"
            r"([A-Z][A-Za-z0-9\s.,/#()&'\-%:;!?@+]{10,200}?)"
            r"\s*\(https?://[^\)]+\)\s+"
            r"Store SKU[^$]*"
            r"Unit Price\s*\$?([0-9,]+\.[0-9]{1,2})\s+"
            r"Qty\s+(\d+)\s+"
            r"Item Total\s*\$?([0-9,]+\.[0-9]{1,2})",
            body,
            re.I,
        )

        for block in item_blocks:
            name = block.group(1).strip()
            # Clean html2text junk — remove any trailing artifacts
            name = re.sub(r"\s+", " ", name).strip()
            if len(name) < 5:
                continue
            order.items.append(OrderItem(
                name=name,
                qty=int(block.group(3)),
                price=float(block.group(2).replace(",", "")),
            ))

        # --- Total from item sums ---
        if order.items:
            order.item_count = sum(i.qty for i in order.items)
            # Also try to get the overall Order Total from the body
            ot = re.search(r"Order Total\s*\$?([0-9,]+\.[0-9]{1,2})", body, re.I)
            if ot:
                order.total = float(ot.group(1).replace(",", ""))

        # --- Title ---
        if order.order_date:
            order.title = f"Home Depot Order {order.order_date}"
        elif order.order_number:
            order.title = f"Home Depot Order {order.order_number}"
        else:
            order.title = "Home Depot Order"

        return order
