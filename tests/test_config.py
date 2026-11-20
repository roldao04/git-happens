"""Settings (source/config.py): the camera's address is read but never shown.

  uv run python -m unittest tests.test_config -v
"""
import os
import unittest
from unittest import mock

from source.config import load_settings

URL = "rtsp://relay.example:8554/secret-stream"


class CameraAddress(unittest.TestCase):
    def test_read_from_the_environment(self):
        with mock.patch.dict(os.environ, {"CAMERA_RTSP_URL": URL}):
            self.assertEqual(load_settings().camera_rtsp_url, URL)

    def test_never_in_the_settings_repr(self):
        with mock.patch.dict(os.environ, {"CAMERA_RTSP_URL": URL}):
            self.assertNotIn("relay.example", repr(load_settings()))


if __name__ == "__main__":
    unittest.main()
