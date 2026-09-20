# MailCode

MailCode is a local email analyzer for Yahoo, Gmail, Outlook.com, and Microsoft 365. It connects directly to the provider over encrypted IMAP, downloads selected message headers, stores metadata in SQLite, and creates CSV summaries and a conservative cleanup plan. Scans and reports are read-only. Its guarded delete command moves exact-sender or UID matches from the inbox to the provider's Trash folder.

## Requirements

- Python 3.11 or newer
- IMAP access permitted for the account
- One supported authentication method:
  - Yahoo app password
  - Gmail OAuth (default) or Google app password
  - Microsoft OAuth for Outlook.com or Microsoft 365

Do not put an app password in a file or command-line argument. MailCode prompts for it without echoing and does not store it. OAuth refresh-token caches are stored in the operating system's credential manager through `keyring`, not in SQLite or a plaintext token file.

## Setup

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

### Linux and macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

After activation, the `mailcode` command works the same way on Windows, Linux, and macOS.

## Authentication setup

MailCode infers Yahoo, Gmail, Outlook, Hotmail, Live, and MSN addresses from their domains. Use `--provider yahoo`, `--provider gmail`, or `--provider outlook` for custom domains such as Google Workspace and Microsoft 365 accounts.

### Yahoo

Create a Yahoo app password, then run a command normally. MailCode prompts for the app password:

```powershell
mailcode scan --email your-address@yahoo.com --limit 500
```

### Gmail

OAuth is the default. In Google Cloud, configure an OAuth consent screen, create an OAuth client with application type **Desktop app**, and download its client JSON file. Add your account as a test user if the consent screen is in testing. The requested IMAP scope is `https://mail.google.com/`.

On first use, provide that JSON file. MailCode opens the Google authorization page and stores the resulting token cache in the OS credential manager:

```powershell
mailcode scan --email your-address@gmail.com --client-secrets C:\path\to\client_secret.json --limit 500
```

Later runs reuse the stored authorization and do not need `--client-secrets`. Set `MAILCODE_GOOGLE_CLIENT_SECRETS` instead of passing the path each time. To use a Google app password instead, enable 2-Step Verification for the account and run:

```powershell
mailcode scan --email your-address@gmail.com --auth app-password --limit 500
```

### Outlook and Microsoft 365

Register an application in Microsoft Entra ID with the appropriate supported account types. Enable public client flows and add the delegated **Office 365 Exchange Online** permission `IMAP.AccessAsUser.All`. Copy the application (client) ID, then run:

```powershell
mailcode scan --email your-address@outlook.com --client-id YOUR-CLIENT-ID --limit 500
```

MailCode prints Microsoft's device-code instructions on first use. The default tenant is `consumers`, suitable for personal Microsoft accounts. For work or school accounts use `--provider outlook --tenant organizations`, or pass the directory tenant ID required by your organization:

```powershell
mailcode scan --provider outlook --email you@company.example --client-id YOUR-CLIENT-ID --tenant organizations --limit 500
```

Set `MAILCODE_MICROSOFT_CLIENT_ID` and optionally `MAILCODE_MICROSOFT_TENANT` to avoid repeating those options.

## Scanning and reports

Start with the newest 500 inbox messages, using the authentication options for your provider:

```powershell
mailcode scan --email your-address@yahoo.com --limit 500
```

Each successful scan refreshes `reports/cleanup_plan.md` and the CSV files automatically. If the classifications look useful, scan the complete inbox:

```powershell
mailcode scan --email your-address@yahoo.com
```

Use `--no-report` for a database-only scan, or `--output PATH` to write reports elsewhere. `mailcode report` regenerates reports without contacting a mail provider.

`reports/category_messages.csv` contains one row per message with its provider, account, exact sender, subject, received date, age, category, and cleanup-candidate status. The `cleanup_rule` column identifies which candidate threshold selected the message. `senders.csv` is grouped by provider and account, so multiple mailboxes can safely share one database.

Re-running a scan updates existing message UIDs instead of duplicating them. Complete scans remove stale local records only for the scanned provider/account. Limited scans update fetched messages without removing other local records.

## Data locations

MailCode stores writable data in the operating system's per-user application-data directory:

- Windows: `%LOCALAPPDATA%\MailCode`
- macOS: `~/Library/Application Support/MailCode`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/mailcode`

The directory contains `mailcode.db` and the `reports` folder. Set `MAILCODE_HOME` to use a different base directory:

```powershell
$env:MAILCODE_HOME = "D:\MailCodeData"
```

```bash
export MAILCODE_HOME="$HOME/mailcode-data"
```

The first upgraded run copies and verifies project-local legacy data in the native location when the destination is empty. A migration marker prevents retries if an open file could not be removed. If both locations independently contain data, MailCode does not overwrite or merge them and prints a warning. `MAILCODE_HOME` and explicit paths bypass automatic location migration. The global `--database` option must appear before the command:

```text
mailcode --database /path/to/mailcode.db report --output /path/to/reports
```

Existing Yahoo-only databases are upgraded automatically without removing rows. Their rows are associated with the Yahoo address on the next authenticated Yahoo operation.

## Delete by sender or UID

Preview an exact, case-insensitive sender match first. Include the same provider and authentication options used for scanning:

```powershell
mailcode delete --email your-address@gmail.com --sender sender@example.com
```

After reviewing the count and sample subjects, move the current inbox matches to Trash:

```powershell
mailcode delete --email your-address@gmail.com --sender sender@example.com --confirm
mailcode report
```

This is recoverable through the provider's Trash folder until the provider empties it. MailCode does not permanently erase Trash. Matching uses current server headers and requires the complete sender address.

You can also use comma-separated UIDs copied from `reports/category_messages.csv`:

```powershell
mailcode delete --email your-address@gmail.com --uids 809646,796805,795188
mailcode delete --email your-address@gmail.com --uids 809646,796805,795188 --confirm
mailcode report
```

UIDs must be positive integers. Duplicates are ignored, and UIDs no longer present in that account's inbox are not moved. `--sender` and `--uids` cannot be used together.

## Reset local data

Preview the paths that will be reset, then confirm when ready:

```powershell
mailcode reset
mailcode reset --confirm
```

Reset never contacts a mail provider or changes a mailbox. It deletes the local database and MailCode's known generated reports, preserves unrelated report-directory files, and recreates an empty schema.

## Privacy and limitations

- Data stays on this computer unless you move or upload it.
- App passwords are held only for the current process. OAuth token caches are kept in the OS credential manager under `MailCode OAuth`.
- Keep the database on a local filesystem; network filesystems may not provide SQLite's required locking behavior.
- Sender, subject, date, flags, size, and top-level MIME headers are stored locally.
- Categories are conservative keyword estimates. `unknown/review` is intentionally common.
- Attachment detection is approximate because message bodies and MIME parts are not downloaded.
- Delete is a preview unless `--confirm` is supplied, and it only moves messages to Trash.
