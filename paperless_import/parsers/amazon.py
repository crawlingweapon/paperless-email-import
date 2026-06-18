"""Amazon order confirmation email parser.

Handles all known Amazon order email formats:

1. Classic (pre-2023): Subject "Your Order with Amazon.com"
   - Rich multi-section layout with per-order totals
   - Uses "Grand Total: $X.XX" (single order) or "Order Total: $X.XX" (multi-order)

2. Classic multi-order (pre-2023): Same subject, but body contains MULTIPLE orders
   - Each has "Order #..." section with its own items and totals
   - We aggregate across all sections into one document

3. Modern (2023+): Subject "Ordered: \"Item Name\""
   - Minimal layout, single item focus
   - Uses "Grand Total: $X.XX"

Extracts: order number, date, total, shipping, tax, items, order URL, Message-ID.
"""

import re
from datetime import datetime
from email import message_from_string
from typing import Optional

import html2text

from . import BaseParser, OrderData, OrderItem


# Regex patterns shared across formats
# Amounts: X.XX, X.X, or X.XUSD (no $). Handle 1-2 decimal places.
RE_ORDER_NUM = re.compile(r"Order\s*(?:#|number)[:\s]*\n?\s*(\d{3}-\d{7,10}-\d{7})")
RE_GRAND_TOTAL = re.compile(r"(?:Order\s+)?(?:Grand\s+)?Total[\s:]+\$?([0-9,]+\.[0-9]{1,2})")
RE_SHIPPING = re.compile(r"Shipping\s*(?:&|&amp;)?\s*Handling?[\s:]+\$?([0-9,]+\.[0-9]{1,2})")
RE_TAX = re.compile(r"Estimated\s+Tax[\s:]+\$?([0-9,]+\.[0-9]{1,2})")
RE_ITEM_SUBTOTAL = re.compile(r"Item\s+Subtotal[\s:]+\$?([0-9,]+\.[0-9]{1,2})")

# Item patterns
RE_ITEM_CLASSIC = re.compile(
    r'(\d+)\s+"([^"]+)"\s*\n\s*[^;]*;?\s*\n?\s*\n?\s*\$?([0-9,]+\.[0-9]{1,2})'
)
# Modern format: * Item Name ... \n Quantity: N \n [optional: $Price]
RE_ITEM_MODERN = re.compile(
    r"\*\s*([^\n]+)\s*\n\s*Quantity:\s*(\d+)(?:\s*\n\s*\$?([0-9,]+\.[0-9]{1,2}))?"
)
RE_ITEM_PLAIN = re.compile(
    r"^\s{16}([A-Z][\w\s&.,#'()/-]{10,80})\s*$",
    re.MULTILINE,
)


def _parse_date(date_str: str) -> Optional[str]:
    """Parse various date formats to YYYY-MM-DD."""
    if not date_str:
        return None
    try:
        # IMAP envelope format: "2010-09-06 18:03+00:00"
        dt = datetime.strptime(date_str.strip()[:16], "%Y-%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %z",
    ]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Try "Placed on Monday, October 13, 2014" inside email body
    m = re.search(r"Placed on \w+, (\w+ \d+, \d{4})", date_str)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


class AmazonParser(BaseParser):
    """Parse Amazon auto-confirm@amazon.com order emails."""

    FROM_PATTERNS = [r"auto-confirm@amazon\.com", r"Amazon\.com"]

    def matches(self, subject: str, from_addr: str) -> bool:
        return any(re.search(p, from_addr, re.I) for p in self.FROM_PATTERNS)

    def _get_body_text(self, raw_content: str) -> str:
        """Extract plain text body from raw MIME content."""
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

    def _get_message_id(self, raw_content: str) -> Optional[str]:
        msg = message_from_string(raw_content)
        return msg.get("Message-ID", "").strip().strip("<>")

    @staticmethod
    def is_summary_only(body: str) -> bool:
        """Check if body is a summary-only email (no item details in plain text).

        These emails have totals but no individual item names/lines.
        Detected by presence of 'Total Before Tax' which only appears
        in this stripped-down Amazon template.
        """
        return "Total Before Tax" in body

    @staticmethod
    def _parse_amounts(lines: list[str]) -> tuple:
        """Parse financial lines within a single order section.

        Returns (total, shipping, tax) — each can be None.
        """
        total = shipping = tax = None
        for line in lines:
            m = RE_GRAND_TOTAL.search(line)
            if m:
                total = float(m.group(1).replace(",", ""))
            m = RE_SHIPPING.search(line)
            if m:
                shipping = float(m.group(1).replace(",", ""))
            m = RE_TAX.search(line)
            if m:
                tax = float(m.group(1).replace(",", ""))
        return total, shipping, tax

    @staticmethod
    def _parse_items(body: str) -> list[OrderItem]:
        """Extract items from a body text (single order section)."""
        items = []

        # Classic format: 1 "Item Name" ... $Price
        for match in RE_ITEM_CLASSIC.findall(body):
            items.append(OrderItem(
                name=match[1].strip(),
                qty=int(match[0]),
                price=float(match[2].replace(",", "")),
            ))

        # Modern format: * Item Name ... Quantity: N [optional: $Price]
        for match in RE_ITEM_MODERN.findall(body):
            items.append(OrderItem(
                name=match[0].strip(),
                qty=int(match[1]),
                price=float(match[2].replace(",", "")) if match[2] else None,
            ))

        # Plain format (multi-order): Item Name with leading spaces, then $Price
        # Match items followed by a $price line within 3 lines
        item_candidates = list(RE_ITEM_PLAIN.finditer(body))
        for i, m in enumerate(item_candidates):
            name = m.group(1).strip()
            # Skip if this looks like a header or label
            if any(skip in name.lower() for skip in [
                "shipping", "total", "subtotal", "estimated", "item", "order",
                "tax", "price", "submitted", "your", "standard", "expedited",
                "group my items", "shipping preference",
            ]):
                continue
            # Look ahead for a price line
            tail = body[m.end():m.end() + 200]
            price_match = re.search(r"\$?([0-9,]+\.[0-9]{1,2})", tail)
            price = float(price_match.group(1).replace(",", "")) if price_match else None
            items.append(OrderItem(name=name, qty=1, price=price))

        return items

    def parse(self, raw_content: str, subject: str, from_addr: str, date_str: str) -> Optional[OrderData]:
        body = self._get_body_text(raw_content)
        if not body:
            return None

        order = OrderData(merchant="Amazon.com", subject=subject)
        order.message_id = self._get_message_id(raw_content)

        # --- Order Number (first one found) ---
        m = RE_ORDER_NUM.search(body)
        if m:
            order.order_number = m.group(1)

        # --- Order URL ---
        order.order_url = self._get_order_url(body, order.order_number)

        # --- Order Date ---
        order.order_date = _parse_date(date_str)
        # Fallback: try to extract "Placed on ..." from body if envelope date didn't work
        if not order.order_date:
            body_date = re.search(r"Placed on \w+, (\w+ \d+, \d{4})", body)
            if body_date:
                try:
                    dt = datetime.strptime(body_date.group(1), "%B %d, %Y")
                    order.order_date = dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass

        # --- Detect multi-order: multiple "Order #..." sections ---
        # Split on "Order #..." to get individual order sections
        order_sections = re.split(r"^Order\s+#\d{3}", body, flags=re.MULTILINE)
        # First element is everything before the first order, so skip it if non-empty
        if len(order_sections) <= 1:
            # Single-order format — parse directly
            all_items = self._parse_items(body)
            order.total, order.shipping, order.tax = self._parse_amounts([body])
            order.items = all_items
        else:
            # Multi-order format — aggregate across sections
            sections = order_sections[1:] if order_sections[0].strip() else order_sections[1:]
            total_t, total_s, total_tax = 0.0, 0.0, 0.0
            has_total = has_shipping = has_tax = False
            all_items = []

            for sec in sections:
                items = self._parse_items(sec)
                all_items.extend(items)
                ot, os_, otax = self._parse_amounts([sec])
                if ot is not None:
                    total_t += ot; has_total = True
                if os_ is not None:
                    total_s += os_; has_shipping = True
                if otax is not None:
                    total_tax += otax; has_tax = True

            if has_total:
                order.total = total_t
            if has_shipping:
                order.shipping = total_s
            if has_tax:
                order.tax = total_tax
            order.items = all_items

        # --- Item Count ---
        if order.items:
            order.item_count = sum(i.qty for i in order.items)
        elif order.total is not None:
            order.item_count = 1

        # --- Title ---
        if subject.startswith("Ordered:"):
            clean = subject.replace("Ordered:", "").strip().strip("\"").strip("'")
            clean = re.sub(r"[\u2074-\u207f\u2066-\u2069]", "", clean)
            clean = re.sub(r"\s+", " ", clean).strip()
            if len(clean) > 70:
                clean = clean[:67] + "..."
            order.title = f"Amazon.com - {clean}"
        elif len(order_sections) > 1:
            # Multi-order — indicate aggregation
            order.title = f"Amazon.com Orders {order.order_date or ''}".strip()
        elif order.order_date:
            order.title = f"Amazon.com Order {order.order_date}"
        else:
            order.title = "Amazon.com Order"

        return order

    def _get_order_url(self, body: str, order_number: str) -> Optional[str]:
        """Extract Amazon order URL from email body, or construct one."""
        m = re.search(
            r"(https://www\.amazon\.com/(?:gp/your-account/order-details|"
            r"your-orders/order-details)\?[^\"'\s]+orderID=\d{3}-\d{7,10}-\d{7})",
            body,
        )
        if m:
            return m.group(1)
        if order_number:
            return f"https://www.amazon.com/your-orders/order-details?orderID={order_number}"
        return None
