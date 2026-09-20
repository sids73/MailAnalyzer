from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr


@dataclass(frozen=True)
class MessageMetadata:
    sender: str
    subject: str
    received_at: datetime
    has_attachment: bool = False


@dataclass(frozen=True)
class ScannedMessage:
    uid: int
    folder: str
    sender: str
    subject: str
    received_at: datetime
    size_bytes: int
    is_read: bool
    mime_type: str
    has_attachment: bool


def normalize_sender(raw_sender: str) -> str:
    """Return a lowercase email address, or the original sender if none is present."""
    _, address = parseaddr(raw_sender)
    return (address or raw_sender).strip().lower()


def age_bucket(received_at: datetime, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)

    days = max(0, (current - received_at).days)
    if days <= 30:
        return "0-30 days"
    if days <= 90:
        return "31-90 days"
    if days <= 365:
        return "3-12 months"
    if days <= 365 * 3:
        return "1-3 years"
    return "3+ years"


def classify_message(message: MessageMetadata) -> str:
    sender = normalize_sender(message.sender)
    searchable = f"{sender} {message.subject}".lower()

    rules = (
        ("financial/legal", ("bank", "statement", "invoice", "tax", "insurance")),
        ("account/security", ("security alert", "password reset", "verification code", "sign-in")),
        ("purchases/receipts", ("receipt", "order", "shipped", "delivered", "tracking")),
        ("travel/events", ("reservation", "booking", "boarding pass", "ticket", "event reminder")),
        ("social notifications", ("facebook", "linkedin", "instagram", "social notification")),
        ("promotions", ("sale", "coupon", "discount", "limited time", "special offer")),
        ("newsletters", ("newsletter", "digest", "weekly update", "unsubscribe")),
        ("automated/operational", ("no-reply", "noreply", "status report", "automated alert")),
    )
    for category, indicators in rules:
        if any(indicator in searchable for indicator in indicators):
            return category
    return "unknown/review"