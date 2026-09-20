from __future__ import annotations

import json
from pathlib import Path
from typing import Any


GOOGLE_SCOPES = ["https://mail.google.com/"]
MICROSOFT_SCOPES = ["https://outlook.office.com/IMAP.AccessAsUser.All"]
TOKEN_SERVICE = "MailCode OAuth"
TOKEN_CHUNK_SIZE = 1800


class CredentialStore:
    def __init__(self, keyring_module: Any | None = None) -> None:
        self.keyring = keyring_module or _load_keyring()

    def get(self, provider: str, email_address: str) -> str | None:
        key = _token_key(provider, email_address)
        try:
            count_value = self.keyring.get_password(TOKEN_SERVICE, f"{key}:parts")
            if not count_value:
                return None
            return "".join(
                self.keyring.get_password(TOKEN_SERVICE, f"{key}:{index}") or ""
                for index in range(int(count_value))
            )
        except Exception as error:
            raise RuntimeError("Could not read OAuth tokens from the operating-system credential store") from error

    def set(self, provider: str, email_address: str, value: str) -> None:
        key = _token_key(provider, email_address)
        chunks = [value[index : index + TOKEN_CHUNK_SIZE] for index in range(0, len(value), TOKEN_CHUNK_SIZE)]
        try:
            old_count_value = self.keyring.get_password(TOKEN_SERVICE, f"{key}:parts")
            old_count = int(old_count_value) if old_count_value else 0
            for index, chunk in enumerate(chunks):
                self.keyring.set_password(TOKEN_SERVICE, f"{key}:{index}", chunk)
            self.keyring.set_password(TOKEN_SERVICE, f"{key}:parts", str(len(chunks)))
            for index in range(len(chunks), old_count):
                self.keyring.delete_password(TOKEN_SERVICE, f"{key}:{index}")
        except Exception as error:
            raise RuntimeError("Could not save OAuth tokens in the operating-system credential store") from error


def _load_keyring() -> Any:
    try:
        import keyring
    except ImportError as error:
        raise RuntimeError("OAuth requires the 'keyring' package; reinstall MailCode") from error
    return keyring


def _token_key(provider: str, email_address: str) -> str:
    return f"{provider}:{email_address.strip().lower()}"


def get_google_access_token(
    email_address: str,
    client_secrets: Path | None,
    store: CredentialStore | None = None,
) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as error:
        raise RuntimeError(
            "Google OAuth dependencies are missing. Activate MailCode's virtual environment "
            "or install MailCode into this Python environment."
        ) from error

    credential_store = store or CredentialStore()
    serialized = credential_store.get("gmail", email_address)
    credentials = None
    if serialized:
        credentials = Credentials.from_authorized_user_info(json.loads(serialized), GOOGLE_SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

    if not credentials or not credentials.valid:
        if client_secrets is None:
            raise RuntimeError(
                "Gmail OAuth needs --client-secrets pointing to a Google Desktop OAuth client JSON file"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), GOOGLE_SCOPES)
        credentials = flow.run_local_server(port=0)

    credential_store.set("gmail", email_address, credentials.to_json())
    if not credentials.token:
        raise RuntimeError("Google OAuth did not return an access token")
    return credentials.token


def get_microsoft_access_token(
    email_address: str,
    client_id: str | None,
    tenant: str = "consumers",
    store: CredentialStore | None = None,
) -> str:
    if not client_id:
        raise RuntimeError("Outlook OAuth needs --client-id for a Microsoft Entra public client application")
    try:
        import msal
    except ImportError as error:
        raise RuntimeError("Microsoft OAuth dependencies are missing; reinstall MailCode") from error

    credential_store = store or CredentialStore()
    cache = msal.SerializableTokenCache()
    serialized = credential_store.get("outlook", email_address)
    if serialized:
        cache.deserialize(serialized)

    application = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache,
    )
    accounts = application.get_accounts(username=email_address)
    result = application.acquire_token_silent(MICROSOFT_SCOPES, account=accounts[0]) if accounts else None
    if not result:
        flow = application.initiate_device_flow(scopes=MICROSOFT_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Microsoft OAuth device flow could not start: {flow.get('error_description', flow)}")
        print(flow["message"], flush=True)
        result = application.acquire_token_by_device_flow(flow)

    if cache.has_state_changed:
        credential_store.set("outlook", email_address, cache.serialize())
    if not result or "access_token" not in result:
        detail = result.get("error_description", result.get("error", "unknown error")) if result else "no result"
        raise RuntimeError(f"Microsoft OAuth failed: {detail}")
    return result["access_token"]