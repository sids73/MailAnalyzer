import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mailcode.analyzer import MessageMetadata, age_bucket, classify_message, normalize_sender


class AnalyzerTests(unittest.TestCase):
    def test_normalizes_named_sender(self) -> None:
        self.assertEqual(normalize_sender("Example Person <Person@Example.com>"), "person@example.com")

    def test_assigns_age_bucket(self) -> None:
        now = datetime(2026, 9, 11, tzinfo=timezone.utc)
        self.assertEqual(age_bucket(now - timedelta(days=45), now), "31-90 days")
        self.assertEqual(age_bucket(now - timedelta(days=500), now), "1-3 years")

    def test_protects_financial_mail_from_promotion_rule(self) -> None:
        message = MessageMetadata(
            sender="bank@example.com",
            subject="Your statement and special offer",
            received_at=datetime.now(timezone.utc),
        )
        self.assertEqual(classify_message(message), "financial/legal")

    def test_leaves_unrecognized_mail_for_review(self) -> None:
        message = MessageMetadata(
            sender="friend@example.com",
            subject="Dinner on Saturday",
            received_at=datetime.now(timezone.utc),
        )
        self.assertEqual(classify_message(message), "unknown/review")


if __name__ == "__main__":
    unittest.main()