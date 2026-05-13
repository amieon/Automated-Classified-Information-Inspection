import asyncio
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from utils.report_exporter import build_report_exports, text_report_to_markdown


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
        main.LATEST_REPORT = "\n".join([
            "=" * 60,
            "          网页涉密数据检查报告",
            "=" * 60,
            "检查模式: 网页爬取检查",
            "检查时间: 2026-05-12 19:30:00",
            "共检查网页数: 1",
            "发现涉密网页数: 1",
            "涉密数据总条数: 1",
            "-" * 60,
            "",
            "【网页 1】http://example.test",
            "  涉密信息 (1 处):",
            "    第3行 | 关键词 [秘密] → 包含秘密内容",
            "=" * 60,
            "报告结束",
        ])
        main.LATEST_REPORTS = {}

        with patch.object(main, "Jinja2Templates", DummyTemplates):
            app = main.create_app(modules=[])
        download_endpoint = next(route.endpoint for route in app.routes if route.path == "/download_report")

        markdown_response = asyncio.run(download_endpoint())
        self.assertEqual(markdown_response.status_code, 200)
        self.assertEqual(markdown_response.headers["content-type"], "text/markdown; charset=utf-8")
        markdown = markdown_response.body.decode()
        self.assertIn("# 网页涉密数据检查报告", markdown)
        self.assertIn("## 检查摘要", markdown)
        self.assertIn("| 共检查网页数 | 1 |", markdown)
        self.assertIn("## 详细结果", markdown)
        self.assertIn("### 网页 1", markdown)
        self.assertNotIn("```text", markdown)

        text_response = asyncio.run(download_endpoint(format="txt"))
        self.assertEqual(text_response.status_code, 200)
        self.assertEqual(text_response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(text_response.body.decode(), main.LATEST_REPORT)

    def test_text_report_to_markdown_formats_each_report_type(self):
        cases = {
            "web": (
                "\n".join([
                    "=" * 60,
                    "          网页涉密数据检查报告",
                    "=" * 60,
                    "检查模式: 网页爬取检查",
                    "检查时间: 2026-05-12 19:30:00",
                    "共检查网页数: 1",
                    "发现涉密网页数: 1",
                    "涉密数据总条数: 1",
                    "-" * 60,
                    "",
                    "【网页 1】http://example.test",
                    "  涉密信息 (1 处):",
                    "    第3行 | 关键词 [秘密] → 包含秘密内容",
                    "=" * 60,
                    "报告结束",
                ]),
                ["# 网页涉密数据检查报告", "### 网页 1", "**目标：** http://example.test", "- 第3行 | 关键词 `秘密` | 包含秘密内容"],
            ),
            "database": (
                "\n".join([
                    "=" * 60,
                    "          数据库涉密数据检查报告",
                    "=" * 60,
                    "数据库信息: MySQL localhost:3306/baomi",
                    "检查时间: 2026-05-12 19:30:00",
                    "扫描表数: 2",
                    "涉密数据条数: 1",
                    "涉及表数: 1",
                    "-" * 60,
                    "详细结果：",
                    "",
                    "【表名】posts (共 1 条)",
                    "  行 42 | 字段 content | 关键词 [保密] → 保密材料",
                    "=" * 60,
                    "报告结束",
                ]),
                ["# 数据库涉密数据检查报告", "### 表名：posts", "- 行 `42` | 字段 `content` | 关键词 `保密` | 保密材料"],
            ),
            "file": (
                "\n".join([
                    "=" * 60,
                    "           文件涉密数据检查报告",
                    "=" * 60,
                    "检查方式: 文件路径检查",
                    "检查时间: 2026-05-12 19:30:00",
                    "检查文件数: 1",
                    "发现涉密文件数: 1",
                    "-" * 60,
                    "详细结果：",
                    "",
                    "【文件】demo.txt",
                    "  类型: text",
                    "  备注: 隐藏文件",
                    "  涉密信息 (1 处):",
                    "    第7行 | 关键词 [绝密] → 绝密项目",
                    "=" * 60,
                    "报告结束",
                ]),
                ["# 文件涉密数据检查报告", "### 文件：demo.txt", "- **类型：** text", "- **备注：** 隐藏文件", "- 第7行 | 关键词 `绝密` | 绝密项目"],
            ),
            "image": (
                "\n".join([
                    "=" * 60,
                    "          图片涉密数据检查报告",
                    "=" * 60,
                    "检查模式: 图片路径检查",
                    "检查时间: 2026-05-12 19:30:00",
                    "扫描图片数: 1",
                    "图片类型分布:",
                    "   png: 1",
                    "发现涉密图片数: 1",
                    "涉密数据总行数: 1",
                    "-" * 60,
                    "",
                    "【图片 1】demo.png",
                    "  涉密信息 (1 处):",
                    "    区域 1 | 关键词 [机密] → 机密截图",
                    "=" * 60,
                    "报告结束",
                ]),
                ["# 图片涉密数据检查报告", "| png | 1 |", "### 图片 1", "- 区域 1 | 关键词 `机密` | 机密截图"],
            ),
            "audio": (
                "\n".join([
                    "=" * 60,
                    "          音频涉密数据检查报告",
                    "=" * 60,
                    "检查模式: 音频路径检查",
                    "检查时间: 2026-05-12 19:30:00",
                    "扫描音频数: 1",
                    "发现涉密音频数: 1",
                    "-" * 60,
                    "",
                    "【音频 1】demo.wav",
                    "  涉密信息 (1 处):",
                    "    时间 2.5s | 关键词 [泄密] → 泄密语音",
                    "=" * 60,
                    "报告结束",
                ]),
                ["# 音频涉密数据检查报告", "### 音频 1", "- 时间 2.5s | 关键词 `泄密` | 泄密语音"],
            ),
        }

        for name, (text_report, expected_fragments) in cases.items():
            with self.subTest(name=name):
                markdown = text_report_to_markdown(text_report)
                self.assertIn("## 检查摘要", markdown)
                self.assertIn("## 详细结果", markdown)
                self.assertNotIn("```text", markdown)
                for fragment in expected_fragments:
                    self.assertIn(fragment, markdown)

    def test_text_report_to_markdown_keeps_empty_result_message(self):
        text_report = "\n".join([
            "=" * 60,
            "          文件涉密数据检查报告",
            "=" * 60,
            "检查方式: 文件路径检查",
            "检查时间: 2026-05-12 19:30:00",
            "检查文件数: 1",
            "发现涉密文件数: 0",
            "-" * 60,
            "未发现涉密数据。",
            "=" * 60,
            "报告结束",
        ])

        markdown = text_report_to_markdown(text_report)

        self.assertIn("# 文件涉密数据检查报告", markdown)
        self.assertIn("## 详细结果", markdown)
        self.assertIn("未发现涉密数据。", markdown)
        self.assertEqual(build_report_exports(text_report)["txt"], text_report)


if __name__ == "__main__":
    unittest.main()
