from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from mailcode.analyzer import MessageMetadata, ScannedMessage, classify_message, normalize_sender


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    provider TEXT NOT NULL,
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    sender TEXT NOT NULL,
    sender_domain TEXT NOT NULL,
    subject TEXT NOT NULL,
    received_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    is_read INTEGER NOT NULL,
    mime_type TEXT NOT NULL,
    has_attachment INTEGER NOT NULL,
    category TEXT NOT NULL,
    PRIMARY KEY (provider, account, folder, uid)
)
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    columns = [row[1] for row in connection.execute("PRAGMA table_info(messages)")]
    if columns and "provider" not in columns:
        connection.execute("ALTER TABLE messages RENAME TO messages_legacy")
        connection.execute(SCHEMA)
        connection.execute(
            """
            INSERT INTO messages
                (provider, account, folder, uid, sender, sender_domain, subject, received_at,
                 size_bytes, is_read, mime_type, has_attachment, category)
            SELECT 'yahoo', '', folder, uid, sender, sender_domain, subject, received_at,
                   size_bytes, is_read, mime_type, has_attachment, category
            FROM messages_legacy
            """
        )
        connection.execute("DROP TABLE messages_legacy")
    connection.execute(SCHEMA)
    connection.commit()
    return connection


def store_messages(
    connection: sqlite3.Connection,
    messages: Iterable[ScannedMessage],
    provider: str = "yahoo",
    account: str = "",
) -> int:
    return _store_messages(connection, messages, provider=provider, account=account)


def remove_messages(
    connection: sqlite3.Connection,
    folder: str,
    uids: Iterable[int],
    provider: str = "yahoo",
    account: str = "",
) -> int:
    uid_list = list(uids)
    if not uid_list:
        return 0
    placeholders = ",".join("?" for _ in uid_list)
    cursor = connection.execute(
        f"DELETE FROM messages WHERE provider = ? AND account = ? AND folder = ? AND uid IN ({placeholders})",
        (provider, account.strip().lower(), folder, *uid_list),
    )
    connection.commit()
    return cursor.rowcount


def sync_messages(
    connection: sqlite3.Connection,
    messages: Iterable[ScannedMessage],
    folder: str,
    provider: str = "yahoo",
    account: str = "",
) -> tuple[int, int]:
    connection.execute("CREATE TEMP TABLE scanned_uids (uid INTEGER PRIMARY KEY)")
    try:
        count = _store_messages(
            connection, messages, provider=provider, account=account, track_uids=True, commit=False
        )
        cursor = connection.execute(
            """
            DELETE FROM messages
            WHERE provider = ? AND account = ? AND folder = ?
              AND uid NOT IN (SELECT uid FROM scanned_uids)
            """,
            (provider, account.strip().lower(), folder),
        )
        connection.commit()
        return count, cursor.rowcount
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("DROP TABLE scanned_uids")


def _store_messages(
    connection: sqlite3.Connection,
    messages: Iterable[ScannedMessage],
    *,
    provider: str,
    account: str,
    track_uids: bool = False,
    commit: bool = True,
) -> int:
    count = 0
    for message in messages:
        sender = normalize_sender(message.sender)
        domain = sender.rpartition("@")[2] if "@" in sender else ""
        category = classify_message(
            MessageMetadata(message.sender, message.subject, message.received_at, message.has_attachment)
        )
        connection.execute(
            """
            INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, account, folder, uid) DO UPDATE SET
                sender=excluded.sender,
                sender_domain=excluded.sender_domain,
                subject=excluded.subject,
                received_at=excluded.received_at,
                size_bytes=excluded.size_bytes,
                is_read=excluded.is_read,
                mime_type=excluded.mime_type,
                has_attachment=excluded.has_attachment,
                category=excluded.category
            """,
            (
                provider,
                account.strip().lower(),
                message.folder,
                message.uid,
                sender,
                domain,
                message.subject,
                message.received_at.isoformat(),
                message.size_bytes,
                int(message.is_read),
                message.mime_type,
                int(message.has_attachment),
                category,
            ),
        )
        if track_uids:
            connection.execute("INSERT INTO scanned_uids VALUES (?)", (message.uid,))
        count += 1
    if commit:
        connection.commit()
    return count


def claim_legacy_yahoo_messages(connection: sqlite3.Connection, account: str) -> int:
    cursor = connection.execute(
        "UPDATE messages SET account = ? WHERE provider = 'yahoo' AND account = ''",
        (account.strip().lower(),),
    )
    connection.commit()
    return cursor.rowcount