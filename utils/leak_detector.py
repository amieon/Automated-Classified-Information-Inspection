from .regex_leak_detector import RegexLeakDetector  # 确保在同一包下
from .AC_leak_detector import ACLeakDetector
from checkers.keywords import KEYWORDS


class LeakDetector:
    """涉密关键词检测器（基于正则表达式）"""
    def __init__(self, keywords=None, mode="exact"):
        """
        :param keywords: 关键词列表，默认使用常见涉密词
        :param mode: 匹配模式，可选 "exact"（精确）或 "fuzzy"（模糊）
        """
        self.keywords = keywords or KEYWORDS
        self.mode = mode
        if mode == 'fuzzy':
            self.detector = RegexLeakDetector(keywords=self.keywords)
        else:
            self.detector = ACLeakDetector(keywords=self.keywords)

    def check_text(self, text: str) -> list:
        """
        返回涉密行列表，每项为 (行号, 关键词, 行内容)
        内部调用 RegexLeakDetector.detect()
        """
        lines = text.split('\n')
        results = self.detector.detect(lines)
        # 转换为原有格式
        return [(r["line"], r["keyword"], r["content"]) for r in results]