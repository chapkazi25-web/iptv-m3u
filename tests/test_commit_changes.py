from __future__ import annotations

import subprocess
import unittest

from scripts.ci import commit_changes


class CommitChangesTests(unittest.TestCase):
    def test_run_streams_output_by_default(self) -> None:
        result = commit_changes.run(["git", "--version"])
        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertIsNone(result.stdout)

    def test_run_captures_output_on_request(self) -> None:
        """Branch detection reads stdout, so capture must actually capture."""
        result = commit_changes.run(["git", "branch", "--show-current"], capture=True)
        self.assertIsNotNone(result.stdout)
        self.assertIsInstance(result.stdout.strip(), str)

    def test_capture_disabled_still_returns_process(self) -> None:
        result = commit_changes.run(["git", "rev-parse", "--is-inside-work-tree"], capture=False)
        self.assertIsInstance(result, subprocess.CompletedProcess)


if __name__ == "__main__":
    unittest.main()
