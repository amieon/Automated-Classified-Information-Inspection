# checkers/leak_detector.py
class LeakDetector:
    """涉密关键词检测器"""
    def __init__(self, keywords=None):
        self.keywords = keywords or ["机密", "秘密", "绝密", "内部", "保密", "隐私"]

    def check_text(self, text: str) -> list:
        """返回涉密行列表，每项为 (行号, 关键词, 行内容)"""
        lines = text.split('\n')
        leak_lines = []
        for i, line in enumerate(lines, 1):
            for kw in self.keywords:
                if kw in line:
                    leak_lines.append((i, kw, line.strip()))
                    break
        return leak_lines