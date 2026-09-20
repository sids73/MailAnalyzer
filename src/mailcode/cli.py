from __future__ import annotations

import argparse
import getpass
import imaplib
import os
import sqlite3
import sys
from pathlib import Path

from mailcode.auth import get_google_access_token, get_microsoft_access_token
from mailcode.imap_client import IMAPMailbox
from mailcode.maintenance import reset_local_state
from mailcode.paths import RuntimePaths, get_runtime_paths, migrate_legacy_state
from mailcode.providers import PROVIDERS, MailProvider, get_provider
from mailcode.reporting import generate_reports
from mailcode.storage import claim_legacy_yahoo_messages, connect, remove_messages, store_messages, sync_messages


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_uid_list(raw_uids: str) -> list[int]:
    values = [value.strip() for value in raw_uids.split(",")]
    if not values or any(not value.isdecimal() or int(value) <= 0 for value in values):
        raise argparse.ArgumentTypeError("UIDs must be comma-separated positive integers")
    return list(dict.fromkeys(int(value) for value in values))


def _add_mailbox_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email", default=os.environ.get("MAILCODE_EMAIL") or os.environ.get("YAHOO_EMAIL"))
    parser.add_argument("--provider", choices=("auto", *PROVIDERS), default="auto")
    parser.add_argument("--auth", choices=("auto", "app-password", "oauth"), default="auto")
    parser.add_argument(
        "--client-secrets",
        type=Path,
        default=os.environ.get("MAILCODE_GOOGLE_CLIENT_SECRETS"),
        help="Google Desktop OAuth client JSON file",
    )
    parser.add_argument(
        "--client-id",
        default=os.environ.get("MAILCODE_MICROSOFT_CLIENT_ID"),
        help="Microsoft Entra public client application ID",
    )
    parser.add_argument("--tenant", default=os.environ.get("MAILCODE_MICROSOFT_TENANT", "consumers"))


def _mailbox_from_args(args: argparse.Namespace) -> tuple[str, MailProvider, IMAPMailbox]:
    email_address = (args.email or input("Email address: ")).strip().lower()
    provider = get_provider(args.provider, email_address)
    auth_method = provider.default_auth if args.auth == "auto" else args.auth
    if auth_method not in provider.auth_methods:
        raise RuntimeError(f"{provider.display_name} does not support {auth_method} authentication in MailCode")

    if auth_method == "app-password":
        credential = getpass.getpass(f"{provider.display_name} app password (not stored): ")
    elif provider.key == "gmail":
        credential = get_google_access_token(email_address, args.client_secrets)
    else:
        credential = get_microsoft_access_token(email_address, args.client_id, args.tenant)
    return email_address, provider, IMAPMailbox(provider, email_address, credential, auth_method)


def build_parser(runtime_paths: RuntimePaths | None = None) -> argparse.ArgumentParser:
    paths = runtime_paths or get_runtime_paths()
    parser = argparse.ArgumentParser(
        description="Analyze email locally and optionally move selected inbox messages to Trash."
    )
    parser.add_argument("--database", type=Path, default=paths.database)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Read INBOX headers into the local database")
    _add_mailbox_arguments(scan)
    scan.add_argument("--limit", type=int, help="Scan only the newest N messages for a trial run")
    scan.add_argument("--output", type=Path, default=paths.reports, help="Directory for refreshed reports")
    scan.add_argument("--no-report", action="store_true", help="Update the database without refreshing reports")

    report = subparsers.add_parser("report", help="Create CSV summaries and a cleanup plan")
    report.add_argument("--output", type=Path, default=paths.reports)

    delete = subparsers.add_parser("delete", help="Preview or move selected INBOX messages to Trash")
    _add_mailbox_arguments(delete)
    delete_target = delete.add_mutually_exclusive_group(required=True)
    delete_target.add_argument("--sender", help="Exact sender email address")
    delete_target.add_argument("--uids", type=parse_uid_list, help="Comma-separated inbox UIDs")
    delete.add_argument("--confirm", action="store_true", help="Actually move all matches to Trash")

    reset = subparsers.add_parser("reset", help="Reinitialize the local database and generated reports")
    reset.add_argument("--output", type=Path, default=paths.reports)
    reset.add_argument("--confirm", action="store_true", help="Actually erase and reinitialize local data")
    return parser


def main() -> int:
    try:
        return _run()
    except (RuntimeError, ValueError, OSError, sqlite3.Error, imaplib.IMAP4.error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def _run() -> int:
    runtime_paths = get_runtime_paths()
    args = build_parser(runtime_paths).parse_args()
    output_path = getattr(args, "output", runtime_paths.reports)
    uses_default_paths = (
        "MAILCODE_HOME" not in os.environ
        and args.database == runtime_paths.database
        and output_path == runtime_paths.reports
    )
    if uses_default_paths:
        migration = migrate_legacy_state(PROJECT_ROOT, runtime_paths)
        if migration.migrated:
            print(f"Migrated local state from {migration.source} to {migration.destination}.")
        elif migration.conflict:
            print(
                f"Warning: state exists in both {migration.source} and {migration.destination}; "
                "using the per-user destination without merging."
            )

    if args.command == "reset":
        print(f"Database: {args.database.resolve()}")
        print(f"Generated reports: {args.output.resolve()}")
        if not args.confirm:
            print("Preview only. Re-run with --confirm to erase and reinitialize local data.")
            return 0
        database_removed, reports_removed = reset_local_state(args.database, args.output)
        database_status = "recreated" if database_removed else "created"
        print(f"Local database {database_status} with an empty schema.")
        print(f"Deleted {reports_removed:,} generated report files.")
        print("No mail provider was contacted or changed.")
        return 0

    connection = connect(args.database)
    try:
        if args.command == "scan":
            email_address, provider, mailbox = _mailbox_from_args(args)
            if provider.key == "yahoo":
                claim_legacy_yahoo_messages(connection, email_address)
            print(f"Opening {provider.display_name} INBOX in read-only mode...")
            with mailbox:
                messages = mailbox.scan_inbox(limit=args.limit)
                if args.limit is None:
                    count, removed = sync_messages(
                        connection, messages, "INBOX", provider.key, email_address
                    )
                else:
                    count = store_messages(connection, messages, provider.key, email_address)
                    removed = 0
            print(f"Stored metadata for {count:,} messages in {args.database}.")
            if args.limit is None:
                print(
                    f"Removed {removed:,} stale local records no longer present in "
                    f"{provider.display_name} INBOX."
                )
            if not args.no_report:
                generate_reports(connection, args.output)
                print(f"Reports written to {args.output}.")
        elif args.command == "report":
            generate_reports(connection, args.output)
            print(f"Reports written to {args.output}.")
        else:
            email_address, provider, mailbox = _mailbox_from_args(args)
            if provider.key == "yahoo":
                claim_legacy_yahoo_messages(connection, email_address)
            with mailbox:
                if args.sender:
                    print(f"Searching {provider.display_name} INBOX for exact sender {args.sender}...")
                    matches = mailbox.find_inbox_messages_from(args.sender)
                else:
                    print(
                        f"Looking up {len(args.uids):,} requested UIDs in "
                        f"{provider.display_name} INBOX..."
                    )
                    matches = mailbox.find_inbox_messages_by_uids(args.uids)
                print(f"Found {len(matches):,} matching messages.")
                for message in matches[:10]:
                    print(f"  {message.received_at.date()}  {message.subject or '(no subject)'}")
                if len(matches) > 10:
                    print(f"  ... and {len(matches) - 10:,} more")

                if not args.confirm:
                    print(
                        "Preview only. Re-run with --confirm to move these messages to "
                        f"{provider.display_name} Trash."
                    )
                    return 0

                moved = mailbox.move_inbox_messages_to_trash([message.uid for message in matches])
            removed = remove_messages(
                connection,
                "INBOX",
                [message.uid for message in matches],
                provider.key,
                email_address,
            )
            print(
                f"Moved {moved:,} messages to {provider.display_name} Trash and removed "
                f"{removed:,} local records."
            )
            print("Run 'mailcode report' to refresh the reports.")
    finally:
        connection.close()
    return 0