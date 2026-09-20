from __future__ import annotations

import imaplib
import re
import ssl
from collections.abc import Iterator
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from mailcode.analyzer import ScannedMessage, normalize_sender
from mailcode.providers import MailProvider, PROVIDERS


_UID_PATTERN = re.compile(rb"\bUID (\d+)\b")
_SIZE_PATTERN = re.compile(rb"\bRFC822\.SIZE (\d+)\b")
_DATE_PATTERN = re.compile(rb'\bINTERNALDATE "([^"]+)"')
_LIST_PATTERN = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$')


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError):
        return value


def _parse_received_at(date_header: str | None, metadata: bytes) -> datetime:
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            if parsed:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass

    match = _DATE_PATTERN.search(metadata)
    if match:
        try:
            return datetime.strptime(
                match.group(1).decode("ascii", errors="replace"), "%d-%b-%Y %H:%M:%S %z"
            )
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _parse_fetch_item(metadata: bytes, headers: bytes, folder: str) -> ScannedMessage:
    uid_match = _UID_PATTERN.search(metadata)
    if not uid_match:
        raise ValueError("The IMAP server returned a message without a UID")

    parsed = BytesParser(policy=policy.default).parsebytes(headers, headersonly=True)
    content_type = parsed.get_content_type()
    disposition = (parsed.get("Content-Disposition") or "").lower()
    size_match = _SIZE_PATTERN.search(metadata)

    return ScannedMessage(
        uid=int(uid_match.group(1)),
        folder=folder,
        sender=_decode_header(parsed.get("From")),
        subject=_decode_header(parsed.get("Subject")),
        received_at=_parse_received_at(parsed.get("Date"), metadata),
        size_bytes=int(size_match.group(1)) if size_match else 0,
        is_read=b"\\Seen" in metadata,
        mime_type=content_type,
        has_attachment="attachment" in disposition or content_type == "multipart/mixed",
    )


class IMAPMailbox:
    def __init__(
        self,
        provider: MailProvider,
        email_address: str,
        credential: str,
        auth_method: str = "app-password",
    ) -> None:
        self.provider = provider
        self.email_address = email_address
        self.credential = credential
        self.auth_method = auth_method
        self.connection: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> IMAPMailbox:
        context = ssl.create_default_context()
        self.connection = imaplib.IMAP4_SSL(
            self.provider.imap_host, self.provider.imap_port, ssl_context=context
        )
        if self.auth_method == "oauth":
            self.connection.authenticate("XOAUTH2", lambda _: self._xoauth2_response())
        else:
            self.connection.login(self.email_address, self.credential)
        return self

    def _xoauth2_response(self) -> bytes:
        return f"user={self.email_address}\x01auth=Bearer {self.credential}\x01\x01".encode("utf-8")

    def __exit__(self, *_: object) -> None:
        if self.connection is not None:
            try:
                self.connection.logout()
            except imaplib.IMAP4.error:
                pass

    def scan_inbox(self, limit: int | None = None, batch_size: int = 100) -> Iterator[ScannedMessage]:
        if self.connection is None:
            raise RuntimeError("Mailbox is not connected")

        status, _ = self.connection.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(f"{self.provider.display_name} did not allow read-only access to INBOX")

        status, search_data = self.connection.uid("search", None, "ALL")
        if status != "OK" or not search_data:
            raise RuntimeError(f"Could not list {self.provider.display_name} inbox messages")

        uids = search_data[0].split()
        if limit is not None:
            uids = uids[-limit:]

        yield from self._fetch_inbox_messages(uids, batch_size)

    def find_inbox_messages_by_uids(
        self, uids: list[int], batch_size: int = 100
    ) -> list[ScannedMessage]:
        if self.connection is None:
            raise RuntimeError("Mailbox is not connected")

        status, _ = self.connection.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(f"{self.provider.display_name} did not allow read-only access to INBOX")

        requested = {str(uid).encode("ascii") for uid in uids}
        messages = list(self._fetch_inbox_messages(list(requested), batch_size))
        messages_by_uid = {message.uid: message for message in messages}
        return [messages_by_uid[uid] for uid in uids if uid in messages_by_uid]

    def _fetch_inbox_messages(
        self, uids: list[bytes], batch_size: int
    ) -> Iterator[ScannedMessage]:
        if self.connection is None:
            raise RuntimeError("Mailbox is not connected")

        query = "(UID FLAGS RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE CONTENT-TYPE CONTENT-DISPOSITION)])"
        for offset in range(0, len(uids), batch_size):
            batch = b",".join(uids[offset : offset + batch_size])
            status, response = self.connection.uid("fetch", batch, query)
            if status != "OK":
                raise RuntimeError(f"{self.provider.display_name} fetch failed near message {offset + 1}")
            for item in response:
                if isinstance(item, tuple) and len(item) == 2:
                    yield _parse_fetch_item(item[0], item[1], "INBOX")

    def find_inbox_messages_from(self, sender: str) -> list[ScannedMessage]:
        expected = normalize_sender(sender)
        if "@" not in expected:
            raise ValueError("Sender must be a complete email address")
        return [message for message in self.scan_inbox() if normalize_sender(message.sender) == expected]

    def move_inbox_messages_to_trash(self, uids: list[int], batch_size: int = 100) -> int:
        if self.connection is None:
            raise RuntimeError("Mailbox is not connected")
        if not uids:
            return 0
        capabilities = {
            capability.decode("ascii", errors="ignore").upper()
            if isinstance(capability, bytes)
            else capability.upper()
            for capability in self.connection.capabilities
        }
        if "MOVE" not in capabilities:
            raise RuntimeError(
                f"{self.provider.display_name} did not advertise IMAP MOVE; no messages were changed"
            )

        trash_folder = self._find_trash_folder()

        status, _ = self.connection.select("INBOX", readonly=False)
        if status != "OK":
            raise RuntimeError(f"{self.provider.display_name} did not allow write access to INBOX")

        moved = 0
        for offset in range(0, len(uids), batch_size):
            batch = ",".join(str(uid) for uid in uids[offset : offset + batch_size])
            status, _ = self.connection.uid("MOVE", batch, trash_folder)
            if status != "OK":
                raise RuntimeError(
                    f"{self.provider.display_name} move to Trash failed after {moved:,} messages"
                )
            moved += len(uids[offset : offset + batch_size])
        return moved

    def _find_trash_folder(self) -> str:
        if self.connection is None:
            raise RuntimeError("Mailbox is not connected")
        status, response = self.connection.list()
        discovered: list[tuple[set[bytes], str]] = []
        if status == "OK":
            for item in response:
                if not isinstance(item, bytes):
                    continue
                match = _LIST_PATTERN.match(item)
                if not match:
                    continue
                flags = {flag.lower() for flag in match.group("flags").split()}
                raw_name = match.group("name").strip()
                if raw_name.startswith(b'"') and raw_name.endswith(b'"'):
                    raw_name = raw_name[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
                discovered.append((flags, raw_name.decode("utf-8", errors="replace")))

        for flags, name in discovered:
            if b"\\trash" in flags:
                return name
        names = {name.casefold(): name for _, name in discovered}
        for fallback in self.provider.trash_fallbacks:
            if fallback.casefold() in names:
                return names[fallback.casefold()]
        raise RuntimeError(f"Could not find the {self.provider.display_name} Trash folder")


class YahooMailbox(IMAPMailbox):
    def __init__(self, email_address: str, app_password: str) -> None:
        super().__init__(PROVIDERS["yahoo"], email_address, app_password)