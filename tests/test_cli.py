import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mailcode.cli import _mailbox_from_args, build_parser, main
from mailcode.paths import MigrationResult, RuntimePaths


class CliTests(unittest.TestCase):
    def test_scan_refreshes_default_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mail.db"
            reports = root / "reports"
            mailbox = MagicMock()
            mailbox.__enter__.return_value = mailbox
            mailbox.scan_inbox.return_value = iter(())

            arguments = [
                "mailcode",
                "--database",
                str(database),
                "scan",
                "--email",
                "owner@yahoo.com",
                "--limit",
                "1",
            ]
            with (
                patch.object(sys, "argv", arguments),
                patch("mailcode.cli.getpass.getpass", return_value="unused"),
                patch("mailcode.cli.IMAPMailbox", return_value=mailbox),
                patch("mailcode.cli.generate_reports") as generate_reports,
                patch(
                    "mailcode.cli.get_runtime_paths",
                    return_value=RuntimePaths(root, database, reports),
                ),
                patch(
                    "mailcode.cli.migrate_legacy_state",
                    return_value=MigrationResult(False, False, root / "legacy", root),
                ),
            ):
                self.assertEqual(main(), 0)

            generate_reports.assert_called_once()
            self.assertEqual(generate_reports.call_args.args[1], reports)

    def test_scan_can_skip_report_refresh(self) -> None:
        args = build_parser().parse_args(["scan", "--email", "owner@yahoo.com", "--no-report"])
        self.assertTrue(args.no_report)

    def test_custom_report_paths_bypass_legacy_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_paths = RuntimePaths(root / "home", root / "home" / "mailcode.db", root / "home" / "reports")
            custom_database = root / "custom" / "mail.db"
            custom_reports = root / "custom" / "reports"
            arguments = [
                "mailcode",
                "--database",
                str(custom_database),
                "report",
                "--output",
                str(custom_reports),
            ]
            with (
                patch.object(sys, "argv", arguments),
                patch("mailcode.cli.get_runtime_paths", return_value=runtime_paths),
                patch("mailcode.cli.migrate_legacy_state") as migrate,
            ):
                self.assertEqual(main(), 0)

            migrate.assert_not_called()
            self.assertTrue((custom_reports / "cleanup_plan.md").exists())

    def test_gmail_app_password_override_builds_gmail_mailbox(self) -> None:
        args = build_parser().parse_args(
            ["scan", "--email", "owner@gmail.com", "--auth", "app-password"]
        )
        with (
            patch("mailcode.cli.getpass.getpass", return_value="secret"),
            patch("mailcode.cli.IMAPMailbox") as mailbox_class,
        ):
            email_address, provider, mailbox = _mailbox_from_args(args)

        self.assertEqual(email_address, "owner@gmail.com")
        self.assertEqual(provider.key, "gmail")
        self.assertIs(mailbox, mailbox_class.return_value)
        mailbox_class.assert_called_once_with(provider, email_address, "secret", "app-password")

    def test_outlook_defaults_to_microsoft_oauth(self) -> None:
        args = build_parser().parse_args(
            ["scan", "--email", "owner@outlook.com", "--client-id", "client-id"]
        )
        with (
            patch("mailcode.cli.get_microsoft_access_token", return_value="token") as get_token,
            patch("mailcode.cli.IMAPMailbox") as mailbox_class,
        ):
            email_address, provider, mailbox = _mailbox_from_args(args)

        self.assertEqual(provider.key, "outlook")
        get_token.assert_called_once_with(email_address, "client-id", "consumers")
        self.assertIs(mailbox, mailbox_class.return_value)
        mailbox_class.assert_called_once_with(provider, email_address, "token", "oauth")

    def test_mailcode_home_override_bypasses_legacy_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_paths = RuntimePaths(root, root / "mailcode.db", root / "reports")
            arguments = ["mailcode", "report"]
            with (
                patch.object(sys, "argv", arguments),
                patch.dict("mailcode.cli.os.environ", {"MAILCODE_HOME": str(root)}, clear=False),
                patch("mailcode.cli.get_runtime_paths", return_value=runtime_paths),
                patch("mailcode.cli.migrate_legacy_state") as migrate,
            ):
                self.assertEqual(main(), 0)

            migrate.assert_not_called()


if __name__ == "__main__":
    unittest.main()