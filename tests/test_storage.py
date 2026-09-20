from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mailcode.analyzer import ScannedMessage
from mailcode.storage import claim_legacy_yahoo_messages, connect, store_messages, sync_messages


OLD_SCHEMA = """
CREATE TABLE messages (
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
    PRIMARY KEY (folder, uid)
)
"""


def message(uid: int, subject: str = "Subject") -> ScannedMessage:
    return ScannedMessage(
        uid=uid,
        folder="INBOX",
        sender="sender@example.com",
        subject=subject,
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        size_bytes=100,
        is_read=True,
        mime_type="text/plain",
        has_attachment=False,
    )


class StorageTests(unittest.TestCase):
    def test_connect_migrates_old_schema_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mail.db"
            old_connection = sqlite3.connect(database)
            old_connection.execute(OLD_SCHEMA)
            old_connection.execute(
                """
                INSERT INTO messages VALUES
                ('INBOX', 42, 'sender@example.com', 'example.com', 'Subject',
                 '2026-01-01T00:00:00+00:00', 100, 1, 'text/plain', 0, 'unknown/review')
                """
            )
            old_connection.commit()
            old_connection.close()

            connection = connect(database)
            try:
                row = connection.execute(
                    "SELECT provider, account, folder, uid, subject FROM messages"
                ).fetchone()
                primary_key = [
                    item[1]
                    for item in sorted(
                        connection.execute("PRAGMA table_info(messages)"), key=lambda item: item[5]
                    )
                    if item[5]
                ]
            finally:
                connection.close()

        self.assertEqual(tuple(row), ("yahoo", "", "INBOX", 42, "Subject"))
        self.assertEqual(primary_key, ["provider", "account", "folder", "uid"])

    def test_claim_legacy_yahoo_messages_normalizes_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "mail.db")
            try:
                store_messages(connection, [message(1)])
                claimed = claim_legacy_yahoo_messages(connection, "Owner@Yahoo.com")
                account = connection.execute("SELECT account FROM messages").fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(claimed, 1)
        self.assertEqual(account, "owner@yahoo.com")

    def test_sync_removes_stale_rows_only_from_selected_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "mail.db")
            try:
                store_messages(connection, [message(1), message(2)], "gmail", "a@gmail.com")
                store_messages(connection, [message(1)], "gmail", "b@gmail.com")

                stored, removed = sync_messages(
                    connection, [message(2, "Updated")], "INBOX", "gmail", "A@GMAIL.COM"
                )
                rows = connection.execute(
                    "SELECT account, uid, subject FROM messages ORDER BY account, uid"
                ).fetchall()
            finally:
                connection.close()

        self.assertEqual((stored, removed), (1, 1))
        self.assertEqual(
            [tuple(row) for row in rows],
            [("a@gmail.com", 2, "Updated"), ("b@gmail.com", 1, "Subject")],
        )


if __name__ == "__main__":
    unittest.main()