import os
import unittest
from unittest.mock import patch

from app.sectors.client import SectorsClient


class SectorsClientTests(unittest.TestCase):
    @patch.dict(os.environ, {"SECTORS_API_KEY": "test-key"}, clear=True)
    def test_uses_environment_key(self):
        self.assertEqual(SectorsClient().api_key, "test-key")

    @patch.dict(os.environ, {}, clear=True)
    def test_requires_api_key(self):
        with self.assertRaises(ValueError):
            SectorsClient()


if __name__ == "__main__":
    unittest.main()
