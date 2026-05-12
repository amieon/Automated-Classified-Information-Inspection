import unittest
from pathlib import Path


class ResultFrameHeightTest(unittest.TestCase):
    def test_result_iframe_height_is_capped_for_large_result_sets(self):
        html = Path("templates/index.html").read_text(encoding="utf-8")

        self.assertIn("contentHeight", html)
        self.assertIn("maxFrameHeight", html)
        self.assertIn("Math.min(contentHeight, maxFrameHeight)", html)
        self.assertNotIn("resultFrame.style.height = height + 'px';", html)


if __name__ == "__main__":
    unittest.main()
