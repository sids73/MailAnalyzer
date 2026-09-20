from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mailcode.auth import (
    CredentialStore,
    GOOGLE_SCOPES,
    MICROSOFT_SCOPES,
    TOKEN_CHUNK_SIZE,
    get_google_access_token,
    get_microsoft_access_token,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class AuthTests(unittest.TestCase):
    def test_credential_store_round_trips_chunked_values(self) -> None:
        keyring = FakeKeyring()
        store = CredentialStore(keyring)
        value = "x" * (TOKEN_CHUNK_SIZE * 2 + 25)

        store.set("gmail", "Owner@Gmail.com", value)

        self.assertEqual(store.get("gmail", "owner@gmail.com"), value)
        self.assertEqual(len(keyring.values), 4)

        store.set("gmail", "owner@gmail.com", "short")
        self.assertEqual(store.get("gmail", "owner@gmail.com"), "short")
        self.assertEqual(len(keyring.values), 2)

    def test_google_uses_valid_cached_credentials(self) -> None:
        store = MagicMock()
        store.get.return_value = '{"token": "cached"}'
        credentials = SimpleNamespace(
            expired=False,
            refresh_token="refresh",
            valid=True,
            token="access-token",
            to_json=lambda: '{"token": "saved"}',
        )
        with patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_info",
            return_value=credentials,
        ) as from_info:
            token = get_google_access_token("owner@gmail.com", None, store)

        self.assertEqual(token, "access-token")
        from_info.assert_called_once_with({"token": "cached"}, GOOGLE_SCOPES)
        store.set.assert_called_once_with("gmail", "owner@gmail.com", '{"token": "saved"}')

    def test_google_runs_installed_app_flow_without_cache(self) -> None:
        store = MagicMock()
        store.get.return_value = None
        credentials = SimpleNamespace(
            valid=True,
            token="new-token",
            to_json=lambda: '{"token": "new-token"}',
        )
        flow = MagicMock()
        flow.run_local_server.return_value = credentials
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
                return_value=flow,
            ) as from_file,
        ):
            secrets = Path(directory) / "client.json"
            token = get_google_access_token("owner@gmail.com", secrets, store)

        self.assertEqual(token, "new-token")
        from_file.assert_called_once_with(str(secrets), GOOGLE_SCOPES)
        flow.run_local_server.assert_called_once_with(port=0)

    def test_google_requires_client_secrets_for_first_authorization(self) -> None:
        store = MagicMock()
        store.get.return_value = None

        with self.assertRaisesRegex(RuntimeError, "client-secrets"):
            get_google_access_token("owner@gmail.com", None, store)

    def test_microsoft_uses_cached_account_silently(self) -> None:
        store = MagicMock()
        store.get.return_value = "serialized-cache"
        cache = MagicMock()
        cache.has_state_changed = False
        application = MagicMock()
        application.get_accounts.return_value = [{"username": "owner@outlook.com"}]
        application.acquire_token_silent.return_value = {"access_token": "cached-token"}

        with (
            patch("msal.SerializableTokenCache", return_value=cache),
            patch("msal.PublicClientApplication", return_value=application) as app_class,
        ):
            token = get_microsoft_access_token(
                "owner@outlook.com", "client-id", "consumers", store
            )

        self.assertEqual(token, "cached-token")
        cache.deserialize.assert_called_once_with("serialized-cache")
        app_class.assert_called_once_with(
            "client-id",
            authority="https://login.microsoftonline.com/consumers",
            token_cache=cache,
        )
        application.acquire_token_silent.assert_called_once_with(
            MICROSOFT_SCOPES, account={"username": "owner@outlook.com"}
        )

    def test_microsoft_falls_back_to_device_code_and_saves_cache(self) -> None:
        store = MagicMock()
        store.get.return_value = None
        cache = MagicMock()
        cache.has_state_changed = True
        cache.serialize.return_value = "updated-cache"
        application = MagicMock()
        application.get_accounts.return_value = []
        flow = {"user_code": "ABCD", "message": "Open the Microsoft sign-in page"}
        application.initiate_device_flow.return_value = flow
        application.acquire_token_by_device_flow.return_value = {"access_token": "new-token"}

        with (
            patch("msal.SerializableTokenCache", return_value=cache),
            patch("msal.PublicClientApplication", return_value=application),
            patch("builtins.print"),
        ):
            token = get_microsoft_access_token(
                "owner@outlook.com", "client-id", "organizations", store
            )

        self.assertEqual(token, "new-token")
        application.initiate_device_flow.assert_called_once_with(scopes=MICROSOFT_SCOPES)
        application.acquire_token_by_device_flow.assert_called_once_with(flow)
        store.set.assert_called_once_with("outlook", "owner@outlook.com", "updated-cache")

    def test_microsoft_requires_client_id(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "client-id"):
            get_microsoft_access_token("owner@outlook.com", None)


if __name__ == "__main__":
    unittest.main()
