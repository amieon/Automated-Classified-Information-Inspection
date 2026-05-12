import sys
import re
from typing import Dict, List, Tuple


DEFAULT_REPORT_FORMAT = "md"
REPORT_MEDIA_TYPES = {
    "md": "text/markdown",
    "txt": "text/plain",
}


_SEPARATOR_RE = re.compile(r"^\s*(=|-){3,}\s*$")
_DETAIL_HEADER_RE = re.compile(r"^【(?P<label>.+?)】(?P<target>.*)$")
_DB_HIT_RE = re.compile(
    r"^行\s+(?P<row>.+?)\s*\|\s*字段\s+(?P<field>.+?)\s*\|\s*关键词\s*\[(?P<keyword>[^\]]+)\]\s*→\s*(?P<content>.*)$"
)
_GENERIC_HIT_RE = re.compile(
    r"^(?P<location>.+?)\s*\|\s*关键词\s*\[(?P<keyword>[^\]]+)\]\s*→\s*(?P<content>.*)$"
)
_COUNT_LINE_RE = re.compile(r"^涉密信息\s*\((?P<count>.+?)\):?$")


def _table_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>").strip()


def _strip_report_lines(text_report: str) -> List[str]:
    return [line.rstrip() for line in (text_report or "").splitlines()]


def _is_separator(line: str) -> bool:
    return bool(_SEPARATOR_RE.match(line.strip()))


def _find_title(lines: List[str]) -> Tuple[str, int]:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.endswith("检查报告"):
            return stripped, index
    return "检查报告", -1


def _split_report(lines: List[str], title_index: int) -> Tuple[List[str], List[str]]:
    summary: List[str] = []
    details: List[str] = []
    in_details = False

    for line in lines[title_index + 1:]:
        stripped = line.strip()
        if not stripped:
            if in_details and details and details[-1] != "":
                details.append("")
            continue
        if stripped == "报告结束":
            continue
        if _is_separator(stripped):
            if summary or in_details:
                in_details = True
            continue
        if stripped == "详细结果：":
            in_details = True
            continue

        if in_details:
            details.append(line)
        else:
            summary.append(line)

    return summary, details


def _format_summary(summary_lines: List[str]) -> List[str]:
    rows: List[Tuple[str, str]] = []
    distribution_title = ""
    distribution_rows: List[Tuple[str, str]] = []

    for line in summary_lines:
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        if not value and ("分布" in key or "统计" in key):
            distribution_title = key
            continue

        if distribution_title and line[:1].isspace():
            distribution_rows.append((key, value))
        else:
            rows.append((key, value))

    output = ["## 检查摘要", ""]
    if rows:
        output.extend(["| 项目 | 内容 |", "| --- | --- |"])
        output.extend(f"| {_table_cell(key)} | {_table_cell(value)} |" for key, value in rows)
        output.append("")
    else:
        output.extend(["暂无摘要信息。", ""])

    if distribution_rows:
        output.extend([f"### {distribution_title}", "", "| 类型 | 数量 |", "| --- | --- |"])
        output.extend(f"| {_table_cell(key)} | {_table_cell(value)} |" for key, value in distribution_rows)
        output.append("")

    return output


def _format_header(label: str, target: str) -> List[str]:
    label = label.strip()
    target = target.strip()

    if label == "表名":
        table_name = target.split("(", 1)[0].strip()
        lines = [f"### 表名：{table_name or '未知表'}"]
        count_match = re.search(r"\((?P<count>.+?)\)", target)
        if count_match:
            lines.append(f"- **命中数：** {count_match.group('count').strip()}")
        return lines

    if label == "文件":
        return [f"### 文件：{target or '未知文件'}"]

    lines = [f"### {label}"]
    if target:
        lines.append(f"**目标：** {target}")
    return lines


def _format_detail_line(line: str) -> List[str]:
    stripped = line.strip()
    if not stripped:
        return []

    db_match = _DB_HIT_RE.match(stripped)
    if db_match:
        return [
            "- 行 `{row}` | 字段 `{field}` | 关键词 `{keyword}` | {content}".format(
                row=db_match.group("row").strip(),
                field=db_match.group("field").strip(),
                keyword=db_match.group("keyword").strip(),
                content=db_match.group("content").strip(),
            )
        ]

    generic_match = _GENERIC_HIT_RE.match(stripped)
    if generic_match:
        return [
            "- {location} | 关键词 `{keyword}` | {content}".format(
                location=generic_match.group("location").strip(),
                keyword=generic_match.group("keyword").strip(),
                content=generic_match.group("content").strip(),
            )
        ]

    count_match = _COUNT_LINE_RE.match(stripped)
    if count_match:
        return [f"- **涉密信息：** {count_match.group('count').strip()}"]

    if ":" in stripped:
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"类型", "备注"}:
            return [f"- **{key}：** {value}"]

    if stripped in {"未发现涉密数据。", "没有扫描任何网页。"}:
        return [stripped]

    return [f"- {stripped}"]


def _format_details(detail_lines: List[str]) -> List[str]:
    output = ["## 详细结果", ""]
    if not detail_lines:
        output.append("无详细结果。")
        output.append("")
        return output

    wrote_any = False
    for line in detail_lines:
        stripped = line.strip()
        if not stripped:
            continue

        header_match = _DETAIL_HEADER_RE.match(stripped)
        if header_match:
            if wrote_any:
                output.append("")
            output.extend(_format_header(header_match.group("label"), header_match.group("target")))
            wrote_any = True
            continue

        output.extend(_format_detail_line(stripped))
        wrote_any = True

    if not wrote_any:
        output.append("无详细结果。")
    output.append("")
    return output


def text_report_to_markdown(text_report: str) -> str:
    body = (text_report or "").rstrip()
    if not body:
        return "# 检查报告\n\n## 详细结果\n\n暂无报告内容。\n"

    lines = _strip_report_lines(body)
    title, title_index = _find_title(lines)
    if title_index < 0:
        return f"# 检查报告\n\n## 原始报告\n\n```text\n{body}\n```\n"

    summary_lines, detail_lines = _split_report(lines, title_index)
    markdown_lines = [f"# {title}", ""]
    markdown_lines.extend(_format_summary(summary_lines))
    markdown_lines.extend(_format_details(detail_lines))
    return "\n".join(markdown_lines).rstrip() + "\n"


def build_report_exports(text_report: str) -> Dict[str, str]:
    return {
        "txt": text_report,
        "md": text_report_to_markdown(text_report),
    }


def publish_latest_report(text_report: str) -> None:
    reports = build_report_exports(text_report)
    for module_name in ("__main__", "main"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        if hasattr(module, "LATEST_REPORT") or hasattr(module, "LATEST_REPORTS"):
            module.LATEST_REPORT = text_report
            module.LATEST_REPORTS = reports.copy()
