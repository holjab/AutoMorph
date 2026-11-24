import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.morphe import Morphe


class PatchOutputTest(unittest.TestCase):
    def test_published_apk_changes_only_after_successful_output(self):
        for outcome in ("failure", "missing", "success"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original = root / "original" / "example.app" / "1"
                original.mkdir(parents=True)
                (original / "example.app.apk").write_bytes(b"input")
                output = root / "published" / "example.app.apk"
                output.parent.mkdir()
                output.write_bytes(b"published")

                morphe = Morphe.__new__(Morphe)
                morphe.low_spec_mode = False
                morphe.morphe_location = root / "morphe.jar"
                morphe.repo_details = {"repo": {"owner": "owner", "repo_name": "repo"}}
                morphe.original_dir = root / "original"
                morphe.get_cached_repo_path = lambda *args: root / "patches.mpp"

                def run(command, **kwargs):
                    candidate = Path(command[command.index("--out") + 1])
                    if outcome != "missing":
                        candidate.write_bytes(b"new")
                    if outcome == "failure":
                        raise subprocess.CalledProcessError(1, command, stderr="failed")

                with patch("scripts.morphe.subprocess.run", side_effect=run):
                    result = morphe.patch("example.app", "1", {"repo"}, root / "options.json", False, 2, "arm64-v8a", output)

                self.assertEqual(result[0], outcome == "success")
                self.assertEqual(output.read_bytes(), b"new" if outcome == "success" else b"published")
                self.assertEqual(list(output.parent.iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
