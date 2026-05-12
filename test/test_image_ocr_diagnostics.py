import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from checkers import image_checker


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class InMemoryImageCache:
    def __init__(self):
        self.store = {}
        self.config = None

    def config_fingerprint(self, **kwargs):
        self.config = tuple(sorted(kwargs.items()))

    def get_image(self, content):
        return self.store.get((content, self.config))

    def set_image(self, content, result):
        self.store[(content, self.config)] = result


class ImageOcrDiagnosticsTest(unittest.TestCase):
    def test_image_cache_changes_when_ocr_environment_changes(self):
        cache = InMemoryImageCache()
        data = _png_bytes()
        kwargs = {
            "keywords": "秘密",
            "algorithm": "regex",
            "max_insert": 0,
        }

        with patch.object(image_checker, "get_cache", return_value=cache), \
             patch.object(image_checker.pytesseract, "get_languages", side_effect=[["eng"], ["eng", "chi_sim"]]), \
             patch.object(image_checker.pytesseract, "image_to_string", side_effect=["ordinary text", "秘密内容"]):
            first = image_checker._process_image_bytes(("demo.png", data, 0, kwargs))
            second = image_checker._process_image_bytes(("demo.png", data, 0, kwargs))

        self.assertEqual(first["leak_lines"], [])
        self.assertEqual(len(second["leak_lines"]), 1)
        self.assertEqual(second["leak_lines"][0][1], "秘密")

    def test_missing_chinese_ocr_language_is_reported_in_note(self):
        cache = InMemoryImageCache()
        data = _png_bytes()
        kwargs = {
            "keywords": "秘密",
            "algorithm": "regex",
            "max_insert": 0,
        }

        with patch.object(image_checker, "get_cache", return_value=cache), \
             patch.object(image_checker.pytesseract, "get_languages", return_value=["eng"]), \
             patch.object(image_checker.pytesseract, "image_to_string", return_value="unreadable chinese text"):
            result = image_checker._process_image_bytes(("demo.png", data, 0, kwargs))

        self.assertIn("chi_sim", result["note"])


if __name__ == "__main__":
    unittest.main()
