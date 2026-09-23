import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReleaseMetadataScriptTests(unittest.TestCase):
    def test_optional_architectures_are_not_invented(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            name = "PCL-Relay-macOS.zip"
            (directory / name).write_bytes(b"fixture")
            digest = hashlib.sha256(b"fixture").hexdigest()
            (directory / (name + ".sha256")).write_text(digest + "  " + name)
            script = Path(__file__).resolve().parents[1] / "scripts/release_metadata.py"
            subprocess.run([sys.executable, str(script), temp], check=True, capture_output=True)
            metadata = json.loads((directory / "release-metadata.json").read_text())
            self.assertEqual([a["name"] for a in metadata["assets"]], [name])
            self.assertEqual(metadata["assets"][0]["digest"], "sha256:" + digest)

    def test_empty_release_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(__file__).resolve().parents[1] / "scripts/release_metadata.py"
            result = subprocess.run([sys.executable, str(script), temp], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(temp) / "release-metadata.json").exists())
