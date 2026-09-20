from __future__ import annotations

import csv
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from mailcode.analyzer import age_bucket


REPORT_FILENAMES = (
    "ages.csv",
    "categories.csv",
    "category_messages.csv",
    "cleanup_candidates.csv",
    "cleanup_plan.md",
    "content_types.csv",
    "senders.csv",
)

CANDIDATE_RULES = (
    ("Promotions older than 30 days", "promotions", 30),
    ("Social notifications older than 30 days", "social notifications", 30),
    ("Newsletters older than 90 days", "newsletters", 90),
    ("Automated messages older than 90 days", "automated/operational", 90),
    ("Purchase updates older than one year", "purchases/receipts", 365),
)


def _write_csv(path: Path, headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)


def generate_reports(connection: sqlite3.Connection, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    senders = connection.execute(
        """
        SELECT provider, account, sender, sender_domain, COUNT(*) AS messages,
               SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread,
               SUM(size_bytes) AS total_bytes,
               MIN(received_at) AS oldest, MAX(received_at) AS newest
        FROM messages GROUP BY provider, account, sender, sender_domain ORDER BY messages DESC, sender
        """
    ).fetchall()
    _write_csv(
        output_dir / "senders.csv",
        ("provider", "account", "sender", "domain", "messages", "unread", "total_bytes", "oldest", "newest"),
        [tuple(row) for row in senders],
    )

    categories = connection.execute(
        "SELECT category, COUNT(*) AS messages, SUM(size_bytes) AS total_bytes FROM messages GROUP BY category ORDER BY messages DESC"
    ).fetchall()
    _write_csv(
        output_dir / "categories.csv",
        ("category", "messages", "total_bytes"),
        [tuple(row) for row in categories],
    )

    mime_types = connection.execute(
        "SELECT mime_type, COUNT(*) AS messages FROM messages GROUP BY mime_type ORDER BY messages DESC"
    ).fetchall()
    _write_csv(
        output_dir / "content_types.csv",
        ("mime_type", "messages"),
        [tuple(row) for row in mime_types],
    )

    now = datetime.now(timezone.utc)
    ages = Counter(
        age_bucket(datetime.fromisoformat(row[0]), now)
        for row in connection.execute("SELECT received_at FROM messages")
    )
    age_order = ("0-30 days", "31-90 days", "3-12 months", "1-3 years", "3+ years")
    _write_csv(output_dir / "ages.csv", ("age", "messages"), [(name, ages[name]) for name in age_order])

    category_rows: list[tuple[object, ...]] = []
    messages = connection.execute(
        """
        SELECT provider, account, folder, uid, sender, subject, received_at, category, is_read,
               size_bytes, mime_type, has_attachment
        FROM messages ORDER BY category, received_at DESC, sender
        """
    ).fetchall()
    rules_by_category = {category: (label, days) for label, category, days in CANDIDATE_RULES}
    for message in messages:
        received_at = datetime.fromisoformat(message[6])
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        age_days = max(0, (now - received_at).days)
        rule = rules_by_category.get(message[7])
        is_candidate = bool(rule and received_at < datetime.fromtimestamp(now.timestamp() - rule[1] * 86400, timezone.utc))
        category_rows.append(
            (
                message[0],
                message[1],
                message[2],
                message[3],
                message[4],
                message[5],
                message[6],
                age_days,
                age_bucket(received_at, now),
                message[7],
                "yes" if is_candidate else "no",
                rule[0] if is_candidate and rule else "",
                "yes" if not message[8] else "no",
                message[9],
                message[10],
                "yes" if message[11] else "no",
            )
        )
    _write_csv(
        output_dir / "category_messages.csv",
        (
            "provider",
            "account",
            "folder",
            "uid",
            "sender",
            "subject",
            "received_at",
            "age_days",
            "age_bucket",
            "category",
            "cleanup_candidate",
            "cleanup_rule",
            "unread",
            "size_bytes",
            "mime_type",
            "has_attachment",
        ),
        category_rows,
    )

    total = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    unread = connection.execute("SELECT COUNT(*) FROM messages WHERE is_read = 0").fetchone()[0]
    plan_rows: list[tuple[str, int, str]] = []
    for label, category, days in CANDIDATE_RULES:
        cutoff = datetime.fromtimestamp(now.timestamp() - days * 86400, timezone.utc).isoformat()
        count = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE category = ? AND received_at < ?",
            (category, cutoff),
        ).fetchone()[0]
        plan_rows.append((label, count, "Review a sample, then delete with the mail provider if correct"))
    _write_csv(output_dir / "cleanup_candidates.csv", ("rule", "messages", "action"), plan_rows)

    lines = [
        "# Inbox cleanup plan",
        "",
        f"Generated: {now.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"Analyzed messages: {total:,}",
        f"Unread messages: {unread:,}",
        "",
        "## Candidate batches",
        "",
    ]
    lines.extend(f"- {label}: {count:,}. {action}." for label, count, action in plan_rows)
    lines.extend(
        [
            "",
            "## Safeguards",
            "",
            "- Review at least 20 messages from each candidate group before deleting anything.",
            "- Exclude financial/legal, account/security, and unknown/review categories from bulk deletion.",
            "- Delete in small batches so the provider's Trash recovery window remains available.",
            "- Unsubscribe only from legitimate senders; mark suspicious messages as spam.",
        ]
    )
    (output_dir / "cleanup_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")