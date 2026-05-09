import re
from typing import List, Dict, Optional


class RegexLeakDetector:
    """
    模糊匹配检测器（优化版）
    用贪心扫描替代合并正则，配合字符集预过滤，大幅提速
    """

    def __init__(self, keywords=None, max_insert=3):
        self.keywords = keywords or []
        self.max_insert = max_insert

        # ★ 预计算每个关键词的字符集（用于快速预过滤）
        self._keyword_char_sets = {kw: set(kw) for kw in self.keywords}

    def detect(self, lines: List[str]) -> List[Dict]:
        results = []
        for idx, line in enumerate(lines, start=1):
            if not line:
                continue
            line_chars = set(line)  # 行字符集，只算一次

            for kw in self.keywords:
                # ★ 预过滤：关键词所有字符都必须出现在行中
                if not self._keyword_char_sets[kw].issubset(line_chars):
                    continue

                # ★ 贪心扫描匹配
                matched = self._greedy_fuzzy_match(kw, line)
                if matched is not None:
                    results.append({
                        "line": idx,
                        "content": line.strip(),
                        "keyword": kw,
                        "matched_text": matched
                    })
                    break  # 一行只报告第一个命中（如需全部命中删掉这行）
        return results

    def _greedy_fuzzy_match(self, keyword: str, text: str) -> Optional[str]:
        """
        贪心扫描：在 text 中按顺序找 keyword 的每个字符，
        相邻字符之间最多跳过 max_insert 个无关字符。
        返回匹配到的文本片段，若不匹配返回 None。
        """
        ti = 0  # text 指针
        max_gap = self.max_insert + 1  # 每次最多尝试的步数

        for ch in keyword:
            found = False
            limit = min(ti + max_gap, len(text))
            while ti < limit:
                if text[ti] == ch:
                    found = True
                    ti += 1
                    break
                ti += 1
            if not found:
                return None

        # 匹配成功，返回匹配片段（从第一个字符匹配位置到最后一个）
        # 简化：直接返回 keyword（或可以根据需要计算实际片段）
        return keyword

    # 保留兼容接口
    def full_check(self, lines: List[str]) -> Dict:
        matches = self.detect(lines)
        return {
            "fuzzy_matches": matches,
            "total_danger_lines": len(matches),
        }