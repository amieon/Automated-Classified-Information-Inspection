import io
import unittest

from PIL import Image

from checkers.image_checker import ImageCheckerModule, _image_preview_data_url


def _sample_png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), color=(220, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


class ImageDetailPreviewTest(unittest.TestCase):
    def test_image_preview_data_url_is_generated_from_image_bytes(self):
        data_url = _image_preview_data_url(_sample_png_bytes())

        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))
        self.assertGreater(len(data_url), len("data:image/jpeg;base64,"))

    def test_image_detail_modal_shows_large_image_next_to_matches(self):
        html = ImageCheckerModule._build_html_result([
            {
                "path": "demo.png",
                "leak_lines": [(3, "秘密", "这是一段命中秘密内容")],
                "file_type": "image",
                "note": "",
                "image_preview": "data:image/jpeg;base64,abc123",
            }
        ])

        self.assertIn('class="image-detail-layout"', html)
        self.assertIn('class="image-preview-large"', html)
        self.assertIn('src="data:image/jpeg;base64,abc123"', html)
        self.assertIn("命中内容", html)
        self.assertIn("这是一段命中秘密内容", html)


if __name__ == "__main__":
    unittest.main()
