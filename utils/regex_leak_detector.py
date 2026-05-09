import re
from typing import List, Dict, Optional, Tuple
from checkers.keywords import KEYWORDS


class RegexLeakDetector:
    """
    纯模糊匹配检测器（合并正则优化版）
    每个关键词的每个字之间允许插入最多 max_insert 个任意字符。
    """

    def __init__(self, keywords=None, max_insert=3):
        self.keywords = keywords or []
        self.max_insert = max_insert
        # 构建模糊正则：在关键词字符间允许插入最多 max_insert 个任意字符
        self.patterns = []
        for kw in self.keywords:
            fuzzy = ".{0," + str(max_insert) + "}".join(re.escape(c) for c in kw)
            self.patterns.append((kw, re.compile(fuzzy)))
        self._fuzzy_regex = self._build_combined_fuzzy_regex()

    def _build_combined_fuzzy_regex(self) -> re.Pattern:
        """将所有关键词的模糊模式合并为一个正则表达式"""
        if not self.keywords:
            return re.compile(r'(?!)')  # 永不匹配
        patterns = []
        for kw in self.keywords:
            chars = list(kw)
            if len(chars) <= 1:
                patterns.append(re.escape(kw))
            else:
                # 字间加 .{0,max_insert}
                pattern = re.escape(chars[0])
                for ch in chars[1:]:
                    pattern += f".{{0,{self.max_insert}}}{re.escape(ch)}"
                patterns.append(pattern)
        # 合并，注意关键词可能很多，但 Python re 处理长模式尚可
        return re.compile('|'.join(patterns))

    def detect(self, lines: List[str]) -> List[Dict]:
        """
        模糊检测
        :param lines: 文本行列表
        :return: [{"line": int, "content": str, "keyword": str, "matched_text": str}, ...]
        """
        results = []
        for idx, line in enumerate(lines, start=1):
            for match_obj in self._fuzzy_regex.finditer(line):
                matched_text = match_obj.group()
                # 反向查找原始关键词
                keyword = self._find_keyword(matched_text)
                if keyword is not None:
                    results.append({
                        "line": idx,
                        "content": line.strip(),
                        "keyword": keyword,
                        "matched_text": matched_text
                    })
                    # 一行只报告第一个匹配（如需报告所有可移除 break）
                    break
        return results

    def _find_keyword(self, matched_text: str) -> Optional[str]:
        """
        根据匹配到的文本反向查找原始关键词。
        采用线性扫描，但只对每个匹配文本执行一次，效率可接受。
        如果关键词数量极大（>1万），可构建 Trie 或前缀树优化。
        """
        # 优先检查长度相同的关键词（模糊匹配可能插入额外字符，所以长度不同）
        # 直接用原始关键词判断 matched_text 是否包含其所有字符（顺序）
        for kw in self.keywords:
            if self._fuzzy_match_single(kw, matched_text):
                return kw
        return None

    @staticmethod
    def _fuzzy_match_single(keyword: str, text: str) -> bool:
        """检查 text 是否按顺序包含 keyword 的每个字符（允许中间有任意字符）"""
        it = iter(text)
        for ch in keyword:
            if ch not in it:
                return False
        return True

    # 如果需要兼容旧版 full_check 接口，可保留包装
    def full_check(self, lines: List[str]) -> Dict:
        matches = self.detect(lines)
        return {
            "fuzzy_matches": matches,
            "total_danger_lines": len(matches),
        }