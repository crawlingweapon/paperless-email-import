"""Base parser interface and order data model."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OrderItem:
    name: str
    qty: int = 1
    price: Optional[float] = None
    url: Optional[str] = None  # Per-item URL (product page or tracking)


@dataclass
class OrderData:
    merchant: str = ""
    title: str = ""
    subject: str = ""
    order_number: Optional[str] = None
    order_date: Optional[str] = None  # YYYY-MM-DD
    total: Optional[float] = None
    shipping: Optional[float] = None
    tax: Optional[float] = None
    item_count: Optional[int] = None
    order_url: Optional[str] = None      # Link to order on merchant site
    message_id: Optional[str] = None     # Email Message-ID for dedup
    items: list[OrderItem] = field(default_factory=list)
    suggested_tags: list[str] = field(default_factory=list)  # Heuristic category names

    @property
    def pdf_title(self) -> str:
        """Human-readable title for the PDF and Paperless document."""
        return self.title or f"{self.merchant} Order" if self.merchant else "Order"


class BaseParser:
    """Override parse() to extract OrderData from raw email content."""

    def matches(self, subject: str, from_addr: str) -> bool:
        """Return True if this parser can handle the given email."""
        raise NotImplementedError

    def parse(self, raw_content: str, subject: str, from_addr: str, date_str: str) -> Optional[OrderData]:
        """Extract order data from raw email MIME content."""
        raise NotImplementedError
