import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mailcode.analyzer import ScannedMessage
from mailcode.reporting import generate_reports
from mailcode.storage import connect, store_messages, sync_messages


class ReportingTests(unittest.TestCase):
    def test_generates_inventory_and_conservative_cleanup_candidates(self) -> None:
        now = datetime.now(timezone.utc)
        messages = [
            ScannedMessage(
                uid=1,
                folder="INBOX",
                sender="Offers <sale@shop.example>",
                subject="Limited time sale",
                received_at=now - timedelta(days=60),
                size_bytes=1000,
                is_read=True,
                mime_type="text/html",
                has_attachment=False,
            ),
            ScannedMessage(
                uid=2,
                folder="INBOX",
                sender="bank@example.com",
                subject="Your annual statement",
                received_at=now - timedelta(days=500),
                size_bytes=2000,
                is_read=False,
                mime_type="application/pdf",
                has_attachment=True,
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = connect(root / "mail.db")
            try:
                self.assertEqual(store_messages(connection, messages), 2)
                generate_reports(connection, root / "reports")
            finally:
                connection.close()

            expected = {
                "ages.csv",
                "categories.csv",
                "category_messages.csv",
                "cleanup_candidates.csv",
                "cleanup_plan.md",
                "content_types.csv",
                "senders.csv",
            }
            self.assertEqual({path.name for path in (root / "reports").iterdir()}, expected)

            with (root / "reports" / "cleanup_candidates.csv").open(encoding="utf-8") as source:
                candidates = {row["rule"]: int(row["messages"]) for row in csv.DictReader(source)}
            self.assertEqual(candidates["Promotions older than 30 days"], 1)
            self.assertNotIn("Financial", candidates)

            with (root / "reports" / "category_messages.csv").open(encoding="utf-8") as source:
                messages_by_sender = {row["sender"]: row for row in csv.DictReader(source)}
            promotion = messages_by_sender["sale@shop.example"]
            self.assertEqual(promotion["provider"], "yahoo")
            self.assertEqual(promotion["category"], "promotions")
            self.assertEqual(promotion["cleanup_candidate"], "yes")
            self.assertEqual(promotion["cleanup_rule"], "Promotions older than 30 days")
            financial = messages_by_sender["bank@example.com"]
            self.assertEqual(financial["cleanup_candidate"], "no")
            self.assertEqual(financial["cleanup_rule"], "")
            plan = (root / "reports" / "cleanup_plan.md").read_text(encoding="utf-8")
            self.assertIn("Generated:", plan)
            self.assertNotIn("Yahoo", plan)

    def test_complete_sync_removes_messages_missing_from_inbox(self) -> None:
        now = datetime.now(timezone.utc)

        def message(uid: int) -> ScannedMessage:
            return ScannedMessage(
                uid=uid,
                folder="INBOX",
                sender="sender@example.com",
                subject=f"Message {uid}",
                received_at=now,
                size_bytes=100,
                is_read=True,
                mime_type="text/plain",
                has_attachment=False,
            )

        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "mail.db")
            try:
                store_messages(connection, [message(1), message(2)])
                scanned, removed = sync_messages(connection, [message(2), message(3)], "INBOX")
                remaining = [row[0] for row in connection.execute("SELECT uid FROM messages ORDER BY uid")]
            finally:
                connection.close()

        self.assertEqual((scanned, removed), (2, 1))
        self.assertEqual(remaining, [2, 3])


if __name__ == "__main__":
    unittest.main()