import re
from typing import List, Dict, Optional
from  checkers.keywords import KEYWORDS


class RegexLeakDetector:
    """
    纯文字正则检测器
    支持两种模式：
    - exact: 精确匹配（普通关键词查找）
    - fuzzy: 模糊匹配（关键词每个字之间可插入最多3个任意字符）
    """

    def __init__(self, keywords: Optional[List[str]] = None):
        self.keywords = keywords or KEYWORDS

    # ---------- 核心：生成各种模式的正则表达式 ----------
    def pattern_exact(self, keyword: str) -> str:
        """精确匹配：直接转义关键词"""
        return re.escape(keyword)

    def pattern_fuzzy(self, keyword: str, max_insert: int = 3) -> str:
        """模糊匹配：每个字之间允许最多 max_insert 个任意字符"""
        chars = list(keyword)
        if len(chars) <= 1:
            return re.escape(keyword)
        parts = [re.escape(chars[0])]
        for ch in chars[1:]:
            parts.append(f".{{0,{max_insert}}}{re.escape(ch)}")
        return ''.join(parts)

    # ---------- 检测方法 ----------
    def detect(self, lines: List[str], mode: str = "exact") -> List[Dict]:
        """
        lines: 文本行列表（例如从文件或网页提取）
        mode: "exact" | "fuzzy"
        返回: [{"line": int, "content": str, "keyword": str, "matched_text": str}, ...]
        """
        results = []
        pattern_func = self.pattern_exact if mode == "exact" else self.pattern_fuzzy
        for idx, line in enumerate(lines, start=1):
            for kw in self.keywords:
                pattern = pattern_func(kw)
                match = re.search(pattern, line)
                if match:
                    results.append({
                        "line": idx,
                        "content": line.strip(),
                        "keyword": kw,
                        "matched_text": match.group()
                    })
                    break  # 一行只报告第一个匹配的关键词
        return results

    # ---------- 组合检测：先精确，再模糊 ----------
    def full_check(self, lines: List[str]) -> Dict:
        """
        返回汇总结果，包含两种模式的结果
        """
        exact_matches = self.detect(lines, mode="exact")
        fuzzy_matches = self.detect(lines, mode="fuzzy")
        return {
            "exact_matches": exact_matches,
            "fuzzy_matches": fuzzy_matches,
            "total_danger_lines": len(exact_matches) + len(fuzzy_matches),
        }


# ==================== 测试 ====================
if __name__ == "__main__":
    test_lines = [
        "本项目为绝密资料",
        "涉及机密事项",
        "这是内部文件",
        "另见绝*密（中间有符号）",
        "还有绝[星星]密",
        "竖排测试：",
        "这",
        "是",
        "秘",
        "密",
        "内",
        "容"
    ]

    detector = RegexLeakDetector()
    result = detector.full_check(test_lines)
    print("=== 精确匹配结果 ===")
    for r in result["exact_matches"]:
        print(f"  第{r['line']}行: {r['content']} (关键词: {r['keyword']}, 匹配文本: {r['matched_text']})")
    print("=== 模糊匹配结果 ===")
    for r in result["fuzzy_matches"]:
        print(f"  第{r['line']}行: {r['content']} (关键词: {r['keyword']}, 匹配文本: {r['matched_text']})")
    print(f"\n总计危险项: {result['total_danger_lines']}")