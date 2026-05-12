import sys
from typing import Dict


DEFAULT_REPORT_FORMAT = "md"
REPORT_MEDIA_TYPES = {
    "md": "text/markdown",
    "txt": "text/plain",
}


def text_report_to_markdown(text_report: str) -> str:
    body = (text_report or "").rstrip()
    return f"# Report\n\n```text\n{body}\n```\n"


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
