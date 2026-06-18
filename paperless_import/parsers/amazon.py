"""Amazon order confirmation email parser.

Handles two formats:
- Modern (2023+): Subject "Ordered: \"Item Name\"", minimal layout
- Classic (pre-2023): Subject "Your Order with Amazon.com", rich multi-order layout
"""
import re
from datetime import datetime
from email import message_from_string
from typing import Optional

import html2text

from . import BaseParser, OrderData, OrderItem


class AmazonParser(BaseParser):
    """Parse Amazon auto-confirm@amazon.com order emails."""

    # Amazon sender patterns
    FROM_PATTERNS = [r"auto-confirm@amazon\.com", r"Amazon\.com"]

    def matches(self, subject: str, from_addr: str) -> bool:
        return any(re.search(p, from_addr, re.I) for p in self.FROM_PATTERNS)

    def _get_body_text(self, raw_content: str) -> str:
        """Extract plain text body from MIME message."""
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

    def _parse_date(self, date_str: str) -> Optional[str]:
        """Try multiple date formats, return YYYY-MM-DD."""
        if not date_str:
            return None

        # Envelope format: "2010-09-06 18:03+00:00"
        try:
            dt = datetime.strptime(date_str.strip()[:16], "%Y-%m-%d %H:%M")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

        # RFC 2822: "Mon, 06 Sep 2010 18:03:47 +0000"
        for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z"]:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        return None

    def parse(self, raw_content: str, subject: str, from_addr: str, date_str: str) -> Optional[OrderData]:
        body = self._get_body_text(raw_content)
        if not body:
            return None

        order = OrderData(
            merchant="Amazon.com",
            subject=subject,
        )

        # --- Grand Total ---
        # "Grand Total: $19.54" or "Order Grand Total: $63.10"
        m = re.search(r"(?:Order\s+)?Grand\s*Total[:\s]*\$?([0-9,]+\.\d{2})", body)
        if m:
            order.total = float(m.group(1).replace(",", ""))

        # --- Order Number ---
        # "Order #\n113-7280203-7033815" or "Order number:\t\t\t103-5083470-4835402"
        m = re.search(r"Order\s*(?:#|number)[:\s]*\n?\s*(\d{3}-\d{7,10}-\d{7})", body)
        if m:
            order.order_number = m.group(1)

        # --- Order Date ---
        order.order_date = self._parse_date(date_str)

        # --- Shipping ---
        m = re.search(r"Shipping\s*(?:&\s*Handling)?[:\s]*\$?([0-9,]+\.\d{2})", body)
        if m:
            order.shipping = float(m.group(1).replace(",", ""))

        # --- Tax ---
        m = re.search(r"Tax[:\s]*\$?([0-9,]+\.\d{2})", body)
        if m:
            order.tax = float(m.group(1).replace(",", ""))

        # --- Items: Classic format ---
        # '1 "Item Name"\nCategory; \n\n$Price'
        for match in re.findall(
            r'(\d+)\s+"([^"]+)"\s*\n\s*[^;]*;?\s*\n?\s*\n?\s*\$?([0-9,]+\.\d{2})',
            body,
        ):
            order.items.append(OrderItem(
                name=match[1].strip(),
                qty=int(match[0]),
                price=float(match[2].replace(",", "")),
            ))

        # --- Items: Modern format ---
        # "* Item Name\n  Quantity: 1\n  17.99 USD"
        for match in re.findall(r"\*\s*([^\n]+)\s*\n\s*Quantity:\s*(\d+)", body):
            order.items.append(OrderItem(
                name=match[0].strip(),
                qty=int(match[1]),
                price=None,
            ))

        # --- Item Count ---
        if order.items:
            order.item_count = sum(i.qty for i in order.items)
        elif order.total is not None:
            order.item_count = 1

        # --- Title ---
        if subject.startswith("Ordered:"):
            # Modern: "Ordered: \"Item Name\" and 3 more items"
            clean = subject.replace("Ordered:", "").strip().strip("\"").strip("'")
            clean = re.sub(r"[\u2074-\u207f\u2066-\u2069]", "", clean)
            clean = re.sub(r"\s+", " ", clean).strip()
            if len(clean) > 70:
                clean = clean[:67] + "..."
            order.title = f"Amazon.com - {clean}"
        elif order.order_date:
            # Classic: "Amazon.com Order YYYY-MM-DD"
            order.title = f"Amazon.com Order {order.order_date}"
        else:
            order.title = "Amazon.com Order"

        return order
