import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mailcode.cli import build_parser
from mailcode.maintenance import reset_local_state
from mailcode.paths import RuntimePaths
from mailcode.reporting import REPORT_FILENAMES
from mailcode.storage import connect


class ResetTests(unittest.TestCase):
    def test_default_paths_do_not_depend_on_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = RuntimePaths(root, root / "mailcode.db", root / "reports")
            with patch("pathlib.Path.cwd", return_value=root / "other"):
                args = build_parser(paths).parse_args(["reset"])

        self.assertEqual(args.database, paths.database)
        self.assertEqual(args.output, paths.reports)

    def test_reset_recreates_empty_database_and_removes_generated_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "data" / "mailcode.db"
            reports = root / "reports"
            reports.mkdir()

            connection = connect(database)
            connection.execute(
                """
                INSERT INTO messages VALUES
                ('yahoo', 'owner@yahoo.com', 'INBOX', 1, 'sender@example.com', 'example.com', 'Subject',
                 '2026-01-01T00:00:00+00:00', 100, 1, 'text/plain', 0, 'unknown/review')
                """
            )
            connection.commit()
            connection.close()

            for filename in REPORT_FILENAMES:
                (reports / filename).write_text("generated", encoding="utf-8")
            unrelated = reports / "notes.txt"
            unrelated.write_text("keep", encoding="utf-8")

            database_removed, reports_removed = reset_local_state(database, reports)

            self.assertTrue(database_removed)
            self.assertEqual(reports_removed, len(REPORT_FILENAMES))
            self.assertTrue(unrelated.exists())
            self.assertTrue(all(not (reports / filename).exists() for filename in REPORT_FILENAMES))

            connection = connect(database)
            try:
                count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()