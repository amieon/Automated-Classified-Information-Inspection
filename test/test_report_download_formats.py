import asyncio
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class DummyTemplates:
    def __init__(self, *args, **kwargs):
        pass

    def TemplateResponse(self, *args, **kwargs):
        raise AssertionError("Template rendering is not used by these tests")


class ReportDownloadFormatTest(unittest.TestCase):
    def setUp(self):
        main.LATEST_REPORT = ""
        main.LATEST_REPORTS = {}

    def test_download_report_defaults_to_markdown_and_keeps_txt(self):
        main.LATEST_REPORT = "plain text report"
        main.LATEST_REPORTS = {
            "txt": "plain text report",
            "md": "# Report\n\n```text\nplain text report\n```\n",
        }

        with patch.object(main, "Jinja2Templates", DummyTemplates):
            app = main.create_app(modules=[])
        download_endpoint = next(route.endpoint for route in app.routes if route.path == "/download_report")

        markdown_response = asyncio.run(download_endpoint())
        self.assertEqual(markdown_response.status_code, 200)
        self.assertEqual(markdown_response.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertEqual(markdown_response.body.decode(), "# Report\n\n```text\nplain text report\n```\n")

        text_response = asyncio.run(download_endpoint(format="txt"))
        self.assertEqual(text_response.status_code, 200)
        self.assertEqual(text_response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(text_response.body.decode(), "plain text report")


if __name__ == "__main__":
    unittest.main()
