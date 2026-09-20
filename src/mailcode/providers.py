from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MailProvider:
    key: str
    display_name: str
    imap_host: str
    imap_port: int = 993
    default_auth: str = "app-password"
    auth_methods: tuple[str, ...] = ("app-password",)
    trash_fallbacks: tuple[str, ...] = ("Trash",)


PROVIDERS = {
    "yahoo": MailProvider("yahoo", "Yahoo", "imap.mail.yahoo.com"),
    "gmail": MailProvider(
        "gmail",
        "Gmail",
        "imap.gmail.com",
        default_auth="oauth",
        auth_methods=("oauth", "app-password"),
        trash_fallbacks=("[Gmail]/Trash", "[Google Mail]/Trash", "Trash"),
    ),
    "outlook": MailProvider(
        "outlook",
        "Outlook",
        "outlook.office365.com",
        default_auth="oauth",
        auth_methods=("oauth",),
        trash_fallbacks=("Deleted Items", "Trash"),
    ),
}


def get_provider(name: str, email_address: str) -> MailProvider:
    if name != "auto":
        return PROVIDERS[name]

    domain = email_address.rpartition("@")[2].lower()
    if domain in {"gmail.com", "googlemail.com"}:
        return PROVIDERS["gmail"]
    if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com"}:
        return PROVIDERS["outlook"]
    if domain == "yahoo.com" or domain.startswith("yahoo.") or domain.endswith(".yahoo.com"):
        return PROVIDERS["yahoo"]
    raise ValueError("Could not infer the provider; specify --provider yahoo, gmail, or outlook")