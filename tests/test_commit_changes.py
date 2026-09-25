from __future__ import annotations

import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from scripts.ci import commit_changes


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        check=True,
        capture_output=True,
    )


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


class PublishConflictTests(unittest.TestCase):
    """A push race on generated files must be resolved by regenerating.

    Textually merging two independently generated playlists produces a
    corrupt artifact, so the script must discard the local copy and rebuild
    from the updated branch instead.
    """

    def _make_repo(self, root: Path) -> None:
        git(root, "init", "--initial-branch=main")
        git(root, "config", "user.name", "tester")
        git(root, "config", "user.email", "tester@example.com")
        (root / "playlist.m3u").write_text("#EXTM3U\nbase\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-m", "base")
        git(root, "branch", "-M", "main")

    def test_regenerate_resolves_pull_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._make_repo(root)
            remote = root.parent / f"{root.name}-remote.git"
            subprocess.run(
                ["git", "init", "--bare", "--initial-branch=main", str(remote)],
                check=True,
                capture_output=True,
            )
            git(root, "remote", "add", "origin", str(remote))
            git(root, "push", "-u", "origin", "main")

            # A competing workflow lands a conflicting change on origin.
            competitor = root.parent / f"{root.name}-competitor"
            subprocess.run(
                ["git", "clone", str(remote), str(competitor)],
                check=True,
                capture_output=True,
            )
            git(competitor, "config", "user.name", "other")
            git(competitor, "config", "user.email", "other@example.com")
            (competitor / "playlist.m3u").write_text("#EXTM3U\ncompetitor\n", encoding="utf-8")
            git(competitor, "commit", "-am", "competitor edit")
            git(competitor, "push", "origin", "main")

            # Our run generated a different version of the same artifact.
            (root / "playlist.m3u").write_text("#EXTM3U\nours\n", encoding="utf-8")
            git(root, "commit", "-am", "generated")

            # Regeneration is deterministic, so it reproduces the same bytes.
            generator = root / "generate.py"
            generator.write_text(
                "from pathlib import Path\n"
                'Path("playlist.m3u").write_text("#EXTM3U\\nours\\n", encoding="utf-8")\n',
                encoding="utf-8",
            )

            with unittest.mock.patch.object(commit_changes, "PROJECT_ROOT", root):
                exit_code = commit_changes.publish(
                    "generated",
                    ["playlist.m3u"],
                    [f"{'python3'} {generator}"],
                )

            self.assertEqual(exit_code, 0)
            remote_playlist = subprocess.run(
                ["git", "show", "main:playlist.m3u"],
                cwd=remote,
                text=True,
                check=True,
                capture_output=True,
            ).stdout
            self.assertEqual(remote_playlist, "#EXTM3U\nours\n")
            self.assertNotIn("<<<<<<<", remote_playlist)

    def test_conflict_without_regenerate_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._make_repo(root)
            remote = root.parent / f"{root.name}-remote2.git"
            subprocess.run(
                ["git", "init", "--bare", "--initial-branch=main", str(remote)],
                check=True,
                capture_output=True,
            )
            git(root, "remote", "add", "origin", str(remote))
            git(root, "push", "-u", "origin", "main")

            competitor = root.parent / f"{root.name}-competitor2"
            subprocess.run(
                ["git", "clone", str(remote), str(competitor)],
                check=True,
                capture_output=True,
            )
            git(competitor, "config", "user.name", "other")
            git(competitor, "config", "user.email", "other@example.com")
            (competitor / "playlist.m3u").write_text("#EXTM3U\ncompetitor\n", encoding="utf-8")
            git(competitor, "commit", "-am", "competitor edit")
            git(competitor, "push", "origin", "main")

            (root / "playlist.m3u").write_text("#EXTM3U\nours\n", encoding="utf-8")
            git(root, "commit", "-am", "generated")

            with unittest.mock.patch.object(commit_changes, "PROJECT_ROOT", root):
                exit_code = commit_changes.publish("generated", ["playlist.m3u"], [])

            self.assertEqual(exit_code, 1)
            # Working tree must be left clean, not mid-conflict.
            status = git(root, "status", "--porcelain")
            self.assertEqual(status.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
