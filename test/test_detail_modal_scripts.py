import os
import re
import subprocess
import tempfile
import unittest

from checkers.audio_checker import AudioCheckerModule
from checkers.file_checker import FileCheckerModule
from checkers.image_checker import ImageCheckerModule
from checkers.web_checker import WebCheckerModule


def _script_blocks(html):
    return re.findall(r"<script>(.*?)</script>", html, re.S)


class DetailModalScriptTest(unittest.TestCase):
    def test_result_detail_modal_scripts_are_valid_javascript(self):
        samples = {
            "web": WebCheckerModule._build_html_result([
                {"url": "http://example.test", "leak_lines": [(1, "秘密", "包含秘密内容")], "note": ""}
            ]),
            "file": FileCheckerModule._build_html_result([
                {
                    "path": "demo.txt",
                    "file_type": "txt",
                    "leak_lines": [(1, "秘密", "包含秘密内容")],
                    "note": "",
                }
            ]),
            "image": ImageCheckerModule._build_html_result([
                {"path": "demo.png", "leak_lines": [(1, "秘密", "包含秘密内容")], "note": ""}
            ]),
            "audio": AudioCheckerModule._build_html_result([
                {"path": "demo.wav", "leak_lines": [(1, "秘密", "包含秘密内容")], "note": ""}
            ]),
        }

        for name, html in samples.items():
            with self.subTest(name=name):
                scripts = _script_blocks(html)
                self.assertTrue(scripts, "result HTML should include modal JavaScript")

                for script in scripts:
                    fd, path = tempfile.mkstemp(suffix=".js")
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(script)

                        result = subprocess.run(
                            ["node", "--check", path],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                    finally:
                        os.unlink(path)

                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
