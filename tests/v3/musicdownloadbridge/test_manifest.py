"""Release metadata must allow the verified minimum host version."""
import json
from pathlib import Path
import unittest


class ManifestTests(unittest.TestCase):
    def test_verified_host_316_is_allowed(self):
        root = Path(__file__).resolve().parents[3]
        metadata = json.loads((root / "package.v3.json").read_text())["MusicDownloadBridge"]
        self.assertEqual(metadata["system_version"], ">=3.1.6")
