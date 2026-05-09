import regex
from typing import List, Dict, Optional, Set, Tuple


class RegexLeakDetector:
    """
    准确模糊匹配检测器（基于 regex 的模糊匹配）
    支持最多 N 个字符的插入、删除、替换。
    """

    def __init__(
        self,
        keywords: Optional[List[str]] = None,
        max_errors: int = 3,
        ignore_case: bool = True,
        workers: int = 1,
    ):
        """
        :param keywords: 敏感词列表
        :param max_errors: 允许的最大编辑距离（0 表示精确匹配）
        :param ignore_case: 是否忽略大小写
        :param workers: 并行线程数（1 表示单线程；>1 会用线程池）
        """
        self.keywords = keywords or []
        self.max_errors = max_errors
        self.ignore_case = ignore_case
        self.workers = workers

        # 每个关键词的字符集（用于快速预过滤）
        self._keyword_char_sets: Dict[str, Set[str]] = {
            kw: set(kw) if not ignore_case else set(kw.lower())
            for kw in self.keywords
        }

        # 预编译正则缓存
        self._pattern_cache: Dict[str, regex.Pattern] = {}

    def _build_pattern(self, keyword: str) -> regex.Pattern:
        """
        构建单个关键词的模糊正则。
        例如 keyword="secret", max_errors=2 =>
            r'(?:secret){e<=2}'
        忽略大小写时自动添加 (?i) 前缀。
        """
        if keyword in self._pattern_cache:
            return self._pattern_cache[keyword]

        # 转义正则特殊字符
        escaped = regex.escape(keyword)
        # 模糊限定符：{e<=N} 表示最多 N 个错误（插入/删除/替换）
        fuzzy_part = f"(?:{escaped}){{e<={self.max_errors}}}"
        # 标志：忽略大小写
        flags = regex.IGNORECASE if self.ignore_case else 0

        pattern = regex.compile(f"({escaped}){{e<={self.max_errors}}}", flags)
        self._pattern_cache[keyword] = pattern
        return pattern

    def _line_matches_keyword(
        self, line: str, keyword: str
    ) -> Optional[str]:
        """
        如果行与关键词模糊匹配，返回实际匹配到的文本片段；
        否则返回 None。
        """
        pattern = self._build_pattern(keyword)
        match = pattern.search(line)
        return match.group() if match else None

    def detect(self, lines: List[str]) -> List[Dict]:
        """
        对每行文本执行模糊检测。
        返回命中列表，每行只报告第一个命中（可自行修改为报告全部）。
        """
        if not self.keywords or not lines:
            return []

        results = []

        # 如果有多线程需求，可以用 concurrent.futures 并行检测行
        # 这里先给出单线程清晰版本，并附带并行版本说明
        if self.workers > 1:
            return self._detect_parallel(lines)

        for idx, line in enumerate(lines, start=1):
            if not line:
                continue

            # 预处理行：根据大小写敏感选择
            line_for_check = line if not self.ignore_case else line.lower()
            line_chars = set(line_for_check)

            for kw in self.keywords:
                # 快速预过滤：关键词所有字符必须出现在行中（大小写已处理）
                if not self._keyword_char_sets[kw].issubset(line_chars):
                    continue

                matched = self._line_matches_keyword(line_for_check, kw)
                if matched is not None:
                    results.append({
                        "line": idx,
                        "content": line.strip(),   # 保留原始内容
                        "keyword": kw,
                        "matched_text": matched    # 实际匹配的文本片段
                    })
                    break   # 一行只报告第一个命中，若需全部命中请删掉此行
        return results

    # 并行检测（可选）
    def _detect_parallel(self, lines: List[str]) -> List[Dict]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 将 lines 分块，每个块由一个线程处理
        chunk_size = max(1, len(lines) // self.workers)
        chunks = []
        for i in range(0, len(lines), chunk_size):
            chunk = lines[i:i + chunk_size]
            start_idx = i + 1  # 行号从 1 开始
            chunks.append((chunk, start_idx))

        all_results = []
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # 提交所有块
            future_to_chunk = {
                executor.submit(self._detect_chunk, chunk, start): (chunk, start)
                for chunk, start in chunks
            }
            for future in as_completed(future_to_chunk):
                all_results.extend(future.result())

        # 按行号排序，保持原始顺序
        all_results.sort(key=lambda r: r["line"])
        return all_results

    def _detect_chunk(self, chunk: List[str], start_line: int) -> List[Dict]:
        """处理一个块，返回带正确行号的命中列表"""
        chunk_results = self.detect(chunk)  # 递归调用 detect（worker=0 防止再分）
        # 调整行号
        for r in chunk_results:
            r["line"] += start_line - 1
        return chunk_results

    # 兼容旧接口
    def full_check(self, lines: List[str]) -> Dict:
        matches = self.detect(lines)
        return {
            "fuzzy_matches": matches,
            "total_danger_lines": len({m["line"] for m in matches}),
        }