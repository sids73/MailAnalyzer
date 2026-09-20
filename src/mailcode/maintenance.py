from __future__ import annotations

from pathlib import Path

from mailcode.reporting import REPORT_FILENAMES
from mailcode.storage import connect


def reset_local_state(database_path: Path, reports_path: Path) -> tuple[bool, int]:
    database_removed = False
    for path in (
        database_path,
        Path(f"{database_path}-shm"),
        Path(f"{database_path}-wal"),
    ):
        if path.exists():
            path.unlink()
            database_removed = True

    reports_removed = 0
    for filename in REPORT_FILENAMES:
        report_path = reports_path / filename
        if report_path.is_file():
            report_path.unlink()
            reports_removed += 1

    connection = connect(database_path)
    connection.close()
    return database_removed, reports_removed