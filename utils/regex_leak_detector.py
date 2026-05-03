import re
from typing import List, Dict, Optional

class RegexLeakDetector:
    """
    纯文字正则检测器
    支持三种模式：
    - exact: 精确匹配（普通关键词查找）
    - fuzzy: 模糊匹配（关键词每个字之间可插入最多3个任意字符）
    - vertical: 竖排检测（将多行文本当作单行处理，跨行匹配）
    """

    def __init__(self, keywords: Optional[List[str]] = None):
        self.keywords = keywords or [
            "秘密", "机密", "绝密", "内部", "涉密",
            "军事秘密", "国家秘密", "商业秘密",
            "保密", "密级", "不予公开"
        ]

    # ---------- 核心：生成各种模式的正则表达式 ----------
    def pattern_exact(self, keyword: str) -> str:
        """精确匹配：直接转义关键词"""
        return re.escape(keyword)

    def pattern_fuzzy(self, keyword: str, max_insert: int = 3) -> str:
        """模糊匹配：每个字之间允许最多 max_insert 个任意字符"""
        chars = list(keyword)
        if len(chars) <= 1:
            return re.escape(keyword)
        # 在每个字符后加上 .{0,max_insert}
        # 注意：最后一个字后面不加
        parts = [re.escape(chars[0])]
        for ch in chars[1:]:
            parts.append(f".{{0,{max_insert}}}{re.escape(ch)}")
        return ''.join(parts)

    def pattern_vertical(self, keyword: str) -> str:
        """
        竖排匹配：相当于把多行文本当一行处理，关键词可能跨行
        使用 . 匹配任意字符（包括换行），每个字之间允许0-1个换行符（可调整）
        """
        chars = list(keyword)
        if len(chars) <= 1:
            return re.escape(keyword)
        parts = [re.escape(chars[0])]
        for ch in chars[1:]:
            # 允许字与字之间插入任意字符（含换行），最多5个（可以调整）
            parts.append(f".{{0,5}}{re.escape(ch)}")
        return ''.join(parts)

    # ---------- 检测方法 ----------
    def detect(self, lines: List[str], mode: str = "exact") -> List[Dict]:
        """
        lines: 文本行列表（例如从文件或网页提取）
        mode: "exact" | "fuzzy" | "vertical"
        返回: [{"line": int, "content": str, "keyword": str, "matched_text": str}, ...]
        注意：
        - "exact" 和 "fuzzy" 返回匹配所在行。
        - "vertical" 返回匹配所在的行的范围。
        """
        results = []

        if mode == "vertical":
            # 竖排模式：将整个文本合并成一个字符串，记录每个字符所属的行号
            merged = ""
            char_line_map = []
            for idx, line in enumerate(lines, start=1):
                for ch in line:
                    merged += ch
                    char_line_map.append(idx)
            # 对整个合并的字符串进行正则搜索
            for kw in self.keywords:
                pattern = self.pattern_vertical(kw)
                for match in re.finditer(pattern, merged):
                    start = match.start()
                    end = match.end()
                    matched_text = match.group()
                    # 确定 start 和 end 对应的行号范围
                    start_line = char_line_map[start]
                    end_line = char_line_map[end - 1] if end <= len(char_line_map) else start_line
                    results.append({
                        "keyword": kw,
                        "matched_text": matched_text,
                        "lines_range": (start_line, end_line)
                    })
            return results

        else:
            # 精确或模糊模式：逐行匹配
            pattern_func = self.pattern_exact if mode == "exact" else self.pattern_fuzzy
            for idx, line in enumerate(lines, start=1):
                for kw in self.keywords:
                    pattern = pattern_func(kw)
                    if re.search(pattern, line):
                        # 找到匹配内容（提取实际匹配的字符串）
                        match = re.search(pattern, line)
                        results.append({
                            "line": idx,
                            "content": line.strip(),
                            "keyword": kw,
                            "matched_text": match.group()
                        })
                        break  # 一行只报告第一个匹配的关键词
            return results

    # ---------- 组合检测：先精确，再模糊，最后竖排 ----------
    def full_check(self, lines: List[str]) -> Dict:
        """
        返回汇总结果，包含三种模式的结果
        """
        result = {
            "exact": self.detect(lines, mode="exact"),
            "fuzzy": self.detect(lines, mode="fuzzy"),
            "vertical": self.detect(lines, mode="vertical")
        }
        # 合并所有危险行（去重）
        all_matches = result["exact"] + result["fuzzy"]
        # 对于竖排匹配，我们额外列出
        return {
            "exact_matches": result["exact"],
            "fuzzy_matches": result["fuzzy"],
            "vertical_matches": result["vertical"],
            "total_danger_lines": len(result["exact"]) + len(result["fuzzy"]) + len(result["vertical"]),
        }


# ==================== 测试 ====================
if __name__ == "__main__":
    # 模拟测试数据
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
    print("=== 竖排跨行匹配结果 ===")
    for r in result["vertical_matches"]:
        print(f"  行范围 {r['lines_range']}: 匹配文本 '{r['matched_text']}' (关键词: {r['keyword']})")
    print(f"\n总计危险项: {result['total_danger_lines']}")