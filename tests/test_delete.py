import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mailcode.analyzer import ScannedMessage
from mailcode.cli import build_parser, parse_uid_list
from mailcode.imap_client import YahooMailbox
from mailcode.storage import connect, remove_messages, store_messages


def message(uid: int, sender: str) -> ScannedMessage:
    return ScannedMessage(
        uid=uid,
        folder="INBOX",
        sender=sender,
        subject=f"Message {uid}",
        received_at=datetime.now(timezone.utc),
        size_bytes=100,
        is_read=True,
        mime_type="text/plain",
        has_attachment=False,
    )


class FakeConnection:
    capabilities = (b"IMAP4REV1", b"MOVE")

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", folder, readonly))
        return "OK", [b"3"]

    def uid(self, command: str, *args: object) -> tuple[str, list[bytes]]:
        self.calls.append(("uid", command, *args))
        return "OK", []

    def list(self) -> tuple[str, list[bytes]]:
        self.calls.append(("list",))
        return "OK", [b'(\\HasNoChildren \\Trash) "/" "Trash"']


class DeleteTests(unittest.TestCase):
    def test_parses_and_deduplicates_comma_separated_uids(self) -> None:
        self.assertEqual(parse_uid_list(" 3,1,3, 2 "), [3, 1, 2])

    def test_rejects_invalid_uid_lists(self) -> None:
        for value in ("", "1,", "0,1", "-1,2", "1,two"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                parse_uid_list(value)

    def test_delete_requires_exactly_one_target_mode(self) -> None:
        parser = build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["delete", "--email", "owner@yahoo.com"])
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "delete",
                        "--email",
                        "owner@yahoo.com",
                        "--sender",
                        "sender@example.com",
                        "--uids",
                        "1,2",
                    ]
                )

    def test_finds_only_exact_normalized_sender(self) -> None:
        mailbox = YahooMailbox("owner@yahoo.com", "unused")
        messages = [
            message(1, "Offers <Sender@Example.com>"),
            message(2, "other@example.com"),
            message(3, "sender@example.com.invalid"),
        ]
        with patch.object(mailbox, "scan_inbox", return_value=iter(messages)):
            matches = mailbox.find_inbox_messages_from("sender@example.com")
        self.assertEqual([item.uid for item in matches], [1])

    def test_uid_lookup_returns_only_live_matches_in_requested_order(self) -> None:
        connection = FakeConnection()
        mailbox = YahooMailbox("owner@yahoo.com", "unused")
        mailbox.connection = connection  # type: ignore[assignment]
        fetched = [message(3, "three@example.com"), message(1, "one@example.com")]

        with patch.object(mailbox, "_fetch_inbox_messages", return_value=iter(fetched)):
            matches = mailbox.find_inbox_messages_by_uids([1, 2, 3])

        self.assertEqual([item.uid for item in matches], [1, 3])
        self.assertEqual(connection.calls, [("select", "INBOX", True)])

    def test_rejects_incomplete_sender_address(self) -> None:
        mailbox = YahooMailbox("owner@yahoo.com", "unused")
        with self.assertRaises(ValueError):
            mailbox.find_inbox_messages_from("sender")

    def test_moves_uids_to_trash_in_batches(self) -> None:
        connection = FakeConnection()
        mailbox = YahooMailbox("owner@yahoo.com", "unused")
        mailbox.connection = connection  # type: ignore[assignment]

        moved = mailbox.move_inbox_messages_to_trash([1, 2, 3], batch_size=2)

        self.assertEqual(moved, 3)
        self.assertEqual(
            connection.calls,
            [
                ("list",),
                ("select", "INBOX", False),
                ("uid", "MOVE", "1,2", "Trash"),
                ("uid", "MOVE", "3", "Trash"),
            ],
        )

    def test_refuses_to_modify_server_without_move_capability(self) -> None:
        connection = FakeConnection()
        connection.capabilities = (b"IMAP4REV1",)
        mailbox = YahooMailbox("owner@yahoo.com", "unused")
        mailbox.connection = connection  # type: ignore[assignment]

        with self.assertRaises(RuntimeError):
            mailbox.move_inbox_messages_to_trash([1])
        self.assertEqual(connection.calls, [])

    def test_removes_only_moved_local_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "mail.db")
            try:
                store_messages(connection, [message(1, "a@example.com"), message(2, "b@example.com")])
                removed = remove_messages(connection, "INBOX", [1])
                remaining = [row[0] for row in connection.execute("SELECT uid FROM messages")]
            finally:
                connection.close()

        self.assertEqual(removed, 1)
        self.assertEqual(remaining, [2])

    def test_same_uid_isolated_across_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "mail.db")
            try:
                store_messages(connection, [message(1, "a@example.com")], "yahoo", "a@yahoo.com")
                store_messages(connection, [message(1, "b@example.com")], "gmail", "b@gmail.com")
                remove_messages(connection, "INBOX", [1], "gmail", "b@gmail.com")
                remaining = connection.execute(
                    "SELECT provider, account FROM messages"
                ).fetchall()
            finally:
                connection.close()

        self.assertEqual([tuple(row) for row in remaining], [("yahoo", "a@yahoo.com")])


if __name__ == "__main__":
    unittest.main()