import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mailcode.imap_client import IMAPMailbox
from mailcode.providers import PROVIDERS, get_provider


class ListConnection:
    def __init__(self, rows: list[bytes]) -> None:
        self.rows = rows

    def list(self) -> tuple[str, list[bytes]]:
        return "OK", self.rows


class ProviderTests(unittest.TestCase):
    def test_provider_presets(self) -> None:
        self.assertEqual(PROVIDERS["yahoo"].imap_host, "imap.mail.yahoo.com")
        self.assertEqual(PROVIDERS["gmail"].imap_host, "imap.gmail.com")
        self.assertEqual(PROVIDERS["outlook"].imap_host, "outlook.office365.com")
        self.assertEqual(PROVIDERS["gmail"].default_auth, "oauth")
        self.assertEqual(PROVIDERS["outlook"].default_auth, "oauth")

    def test_infers_consumer_email_providers(self) -> None:
        self.assertEqual(get_provider("auto", "person@gmail.com").key, "gmail")
        self.assertEqual(get_provider("auto", "person@hotmail.com").key, "outlook")
        self.assertEqual(get_provider("auto", "person@yahoo.co.uk").key, "yahoo")
        with self.assertRaises(ValueError):
            get_provider("auto", "person@example.com")

    def test_builds_xoauth2_response(self) -> None:
        mailbox = IMAPMailbox(PROVIDERS["gmail"], "person@gmail.com", "token", "oauth")
        self.assertEqual(
            mailbox._xoauth2_response(),
            b"user=person@gmail.com\x01auth=Bearer token\x01\x01",
        )

    def test_discovers_special_use_trash_folder(self) -> None:
        mailbox = IMAPMailbox(PROVIDERS["gmail"], "person@gmail.com", "token", "oauth")
        mailbox.connection = ListConnection(
            [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Bin"']
        )  # type: ignore[assignment]
        self.assertEqual(mailbox._find_trash_folder(), "[Gmail]/Bin")

    def test_uses_provider_fallback_when_special_use_is_missing(self) -> None:
        mailbox = IMAPMailbox(PROVIDERS["outlook"], "person@outlook.com", "token", "oauth")
        mailbox.connection = ListConnection(
            [b'(\\HasNoChildren) "/" "Deleted Items"']
        )  # type: ignore[assignment]
        self.assertEqual(mailbox._find_trash_folder(), "Deleted Items")


if __name__ == "__main__":
    unittest.main()