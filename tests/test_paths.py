import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mailcode.paths import RuntimePaths, get_runtime_paths, migrate_legacy_state
from mailcode.reporting import REPORT_FILENAMES
from mailcode.storage import connect


class PathTests(unittest.TestCase):
    def test_resolves_native_platform_locations(self) -> None:
        home = Path("/users/example")
        self.assertEqual(
            get_runtime_paths(platform="win32", environ={"LOCALAPPDATA": "C:/Local"}, user_home=home).home,
            Path("C:/Local/MailCode").resolve(),
        )
        self.assertEqual(
            get_runtime_paths(platform="darwin", environ={}, user_home=home).home,
            (home / "Library" / "Application Support" / "MailCode").resolve(),
        )
        self.assertEqual(
            get_runtime_paths(platform="linux", environ={}, user_home=home).home,
            (home / ".local" / "share" / "mailcode").resolve(),
        )

    def test_honors_xdg_and_mailcode_home(self) -> None:
        home = Path("/users/example")
        xdg = get_runtime_paths(platform="linux", environ={"XDG_DATA_HOME": "/var/user-data"}, user_home=home)
        override = get_runtime_paths(
            platform="win32",
            environ={"LOCALAPPDATA": "C:/Local", "MAILCODE_HOME": "/portable/mailcode"},
            user_home=home,
        )
        self.assertEqual(xdg.home, Path("/var/user-data/mailcode").resolve())
        self.assertEqual(override.home, Path("/portable/mailcode").resolve())

    def test_windows_falls_back_to_user_home_without_local_app_data(self) -> None:
        home = Path("C:/Users/example")
        paths = get_runtime_paths(platform="win32", environ={}, user_home=home)
        self.assertEqual(paths.home, (home / "AppData" / "Local" / "MailCode").resolve())

    def test_migrates_legacy_state_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy"
            destination = root / "destination"
            database = legacy / "data" / "mailcode.db"
            connection = connect(database)
            connection.close()
            Path(f"{database}-wal").write_text("wal", encoding="utf-8")
            reports = legacy / "reports"
            reports.mkdir()
            (reports / "cleanup_plan.md").write_text("plan", encoding="utf-8")
            paths = RuntimePaths(destination, destination / "mailcode.db", destination / "reports")

            result = migrate_legacy_state(legacy, paths)

            self.assertTrue(result.migrated)
            self.assertFalse(result.conflict)
            self.assertTrue(paths.database.exists())
            self.assertTrue(Path(f"{paths.database}-wal").exists())
            self.assertTrue((paths.reports / "cleanup_plan.md").exists())
            self.assertTrue((paths.home / ".legacy-migration-v1").exists())
            connection = sqlite3.connect(paths.database)
            connection.execute("SELECT COUNT(*) FROM messages").fetchone()
            connection.close()
            second_result = migrate_legacy_state(legacy, paths)
            self.assertFalse(second_result.migrated)
            self.assertFalse(second_result.conflict)

    def test_does_not_overwrite_populated_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy"
            destination = root / "destination"
            (legacy / "data").mkdir(parents=True)
            (legacy / "data" / "mailcode.db").write_text("legacy", encoding="utf-8")
            destination.mkdir()
            destination_database = destination / "mailcode.db"
            destination_database.write_text("current", encoding="utf-8")
            paths = RuntimePaths(destination, destination_database, destination / "reports")

            result = migrate_legacy_state(legacy, paths)

            self.assertTrue(result.conflict)
            self.assertFalse(result.migrated)
            self.assertEqual(destination_database.read_text(encoding="utf-8"), "current")
            self.assertEqual((legacy / "data" / "mailcode.db").read_text(encoding="utf-8"), "legacy")

    def test_unrelated_destination_file_blocks_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy"
            destination = root / "destination"
            (legacy / "data").mkdir(parents=True)
            (legacy / "data" / "mailcode.db").write_text("legacy", encoding="utf-8")
            destination.mkdir()
            (destination / "keep.txt").write_text("current", encoding="utf-8")
            paths = RuntimePaths(destination, destination / "mailcode.db", destination / "reports")

            result = migrate_legacy_state(legacy, paths)

            self.assertTrue(result.conflict)
            self.assertTrue((legacy / "data" / "mailcode.db").exists())

    def test_copy_failure_preserves_all_legacy_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy"
            destination = root / "destination"
            database = legacy / "data" / "mailcode.db"
            database.parent.mkdir(parents=True)
            database.write_text("database", encoding="utf-8")
            reports = legacy / "reports"
            reports.mkdir()
            report = reports / "cleanup_plan.md"
            report.write_text("report", encoding="utf-8")
            paths = RuntimePaths(destination, destination / "mailcode.db", destination / "reports")

            with patch("mailcode.paths.shutil.copy2", side_effect=[None, OSError("locked")]):
                with self.assertRaises(OSError):
                    migrate_legacy_state(legacy, paths)

            self.assertTrue(database.exists())
            self.assertTrue(report.exists())
            self.assertFalse(paths.database.exists())

    def test_report_registry_contains_every_generated_file(self) -> None:
        self.assertIn("category_messages.csv", REPORT_FILENAMES)


if __name__ == "__main__":
    unittest.main()