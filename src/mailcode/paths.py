from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from mailcode.reporting import REPORT_FILENAMES


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    database: Path
    reports: Path


@dataclass(frozen=True)
class MigrationResult:
    migrated: bool
    conflict: bool
    source: Path
    destination: Path


def get_runtime_paths(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    user_home: Path | None = None,
) -> RuntimePaths:
    current_platform = platform or sys.platform
    environment = os.environ if environ is None else environ
    home = user_home or Path.home()

    override = environment.get("MAILCODE_HOME")
    if override:
        app_home = Path(override).expanduser()
    elif current_platform == "win32":
        local_app_data = environment.get("LOCALAPPDATA")
        app_home = Path(local_app_data) / "MailCode" if local_app_data else home / "AppData" / "Local" / "MailCode"
    elif current_platform == "darwin":
        app_home = home / "Library" / "Application Support" / "MailCode"
    else:
        xdg_data_home = environment.get("XDG_DATA_HOME")
        app_home = Path(xdg_data_home) / "mailcode" if xdg_data_home else home / ".local" / "share" / "mailcode"

    app_home = app_home.resolve()
    return RuntimePaths(app_home, app_home / "mailcode.db", app_home / "reports")


def migrate_legacy_state(legacy_root: Path, paths: RuntimePaths) -> MigrationResult:
    legacy_root = legacy_root.resolve()
    migration_marker = paths.home / ".legacy-migration-v1"
    if migration_marker.is_file():
        return MigrationResult(False, False, legacy_root, paths.home)

    source_database = legacy_root / "data" / "mailcode.db"
    source_reports = legacy_root / "reports"
    source_files = [source_database, Path(f"{source_database}-wal"), Path(f"{source_database}-shm")]
    source_files.extend(source_reports / filename for filename in REPORT_FILENAMES)

    if not any(path.is_file() for path in source_files):
        return MigrationResult(False, False, legacy_root, paths.home)

    if paths.home.exists() and any(path.is_file() for path in paths.home.rglob("*")):
        return MigrationResult(False, True, legacy_root, paths.home)

    copies: list[tuple[Path, Path]] = []
    for source in source_files[:3]:
        if source.is_file():
            suffix = source.name.removeprefix(source_database.name)
            copies.append((source, Path(f"{paths.database}{suffix}")))
    copies.extend((source, paths.reports / source.name) for source in source_files[3:] if source.is_file())

    paths.home.mkdir(parents=True, exist_ok=True)
    paths.reports.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    try:
        for source, destination in copies:
            shutil.copy2(source, destination)
            copied.append(destination)
        if any(source.stat().st_size != destination.stat().st_size for source, destination in copies):
            raise OSError("Legacy migration verification failed")
        migration_marker.write_text("Migration completed after verified copies.\n", encoding="utf-8")
    except Exception:
        for destination in copied:
            destination.unlink(missing_ok=True)
        migration_marker.unlink(missing_ok=True)
        raise

    # Cleanup is best-effort: a locked legacy file must never invalidate the verified copy.
    for source, _ in copies:
        try:
            source.unlink()
        except OSError:
            pass

    return MigrationResult(True, False, legacy_root, paths.home)