from .regex_leak_detector import RegexLeakDetector
from .AC_leak_detector import ACLeakDetector
from checkers.keywords import KEYWORDS


class LeakDetector:
    """涉密关键词检测器"""

    def __init__(self, keywords=None, algorithm="regex", max_insert=3):
        """
        :param keywords: 关键词列表（或逗号分隔字符串）
        :param algorithm: "regex" 或 "ac"，前端直接传入
        :param max_insert: 模糊匹配最大插入字符数（仅 regex 模式生效）
        """
        # 支持逗号分隔的字符串或列表
        if isinstance(keywords, str):
            self.keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        else:
            self.keywords = keywords or KEYWORDS

        self.algorithm = algorithm
        self.max_insert = max_insert

        if algorithm == "regex":
            # 把 max_insert 传给 RegexLeakDetector（需要它也支持）
            self.detector = RegexLeakDetector(
                keywords=self.keywords,
                max_errors=max_insert
            )
        else:
            self.detector = ACLeakDetector(keywords=self.keywords)

    def check_text(self, text: str) -> list:
        if not text:
            return []
        lines = text.split('\n')
        results = self.detector.detect(lines)
        return [(r["line"], r["keyword"], r["content"]) for r in results]