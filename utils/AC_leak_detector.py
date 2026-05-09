# aho_detector.py
import ahocorasick
from typing import List, Dict, Optional
from checkers.keywords import KEYWORDS


class ACLeakDetector:
    """
    基于 AC 自动机的精确匹配检测器。
    接口与 RegexLeakDetector 完全兼容：
        - detect(lines, mode="exact") → List[Dict]
        - full_check(lines) → Dict
    注意：模糊匹配（mode="fuzzy"）暂返回空列表，因为 AC 自动机天然只做精确匹配。
    """

    def __init__(self, keywords: Optional[List[str]] = None):
        """
        :param keywords: 敏感词列表，默认使用 KEYWORDS
        """
        self.keywords = keywords or KEYWORDS
        self._automaton = self._build_automaton()

    def _build_automaton(self):
        """构建并返回 AC 自动机实例"""
        A = ahocorasick.Automaton()
        for idx, kw in enumerate(self.keywords):
            A.add_word(kw, (idx, kw))   # 存储 (索引, 关键词) 对，方便反向查找
        A.make_automaton()
        return A

    def detect(self, lines: List[str], mode: str = "exact") -> List[Dict]:
        """
        :param lines: 文本行列表
        :param mode:  仅为了接口兼容，实际只支持 "exact"，其他模式返回空列表
        :return: [{"line": int, "content": str, "keyword": str, "matched_text": str}, ...]
                每行只报告第一个匹配的关键词
        """
        if mode != "exact":
            # 如果需要模糊匹配，请使用 RegexLeakDetector 或扩展此类
            return []

        results = []
        for idx, line in enumerate(lines, start=1):
            # 获取该行的所有匹配，但只取第一个（如果一行内有多个，只报告最早出现的关键词）
            for end_index, (_, keyword) in self._automaton.iter(line):
                start_index = end_index - len(keyword) + 1
                matched_text = line[start_index:end_index + 1]
                results.append({
                    "line": idx,
                    "content": line.strip(),
                    "keyword": keyword,
                    "matched_text": matched_text
                })
                break  # 一行只报第一个
        return results

    def full_check(self, lines: List[str]) -> Dict:
        """
        返回完整检测结果字典，与 RegexLeakDetector 一致
        """
        exact_matches = self.detect(lines, mode="exact")
        return {
            "exact_matches": exact_matches,
            "fuzzy_matches": [],   # AC 自动机不做模糊匹配
            "total_danger_lines": len(exact_matches),
        }

    # 可选：提供 pattern_exact / pattern_fuzzy 方法，但这里不需要正则，
    #     可以留空或直接返回空字符串（接口兼容）
    def pattern_exact(self, keyword: str) -> str:
        return ""   # 不生成正则

    def pattern_fuzzy(self, keyword: str, max_insert: int = 3) -> str:
        return ""   # 不生成正则