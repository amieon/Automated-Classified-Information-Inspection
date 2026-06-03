import re
from typing import List, Dict, Optional, Set


class RegexLeakDetector:
    """
    准确模糊匹配检测器
    当前实现主要支持：关键词字符按顺序出现，字符之间允许插入最多 max_errors 个字符。
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
        :param max_errors: 允许插入的最大字符数
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
        self._pattern_cache: Dict[str, str] = {}

    def _build_pattern(self, keyword: str) -> str:
        if keyword in self._pattern_cache:
            return self._pattern_cache[keyword]

        # 构建模式：字符间允许任意字符（非贪婪），例如 "1.*?2.*?3.*?4"
        pattern = r'.*?'.join(re.escape(ch) for ch in keyword)
        self._pattern_cache[keyword] = pattern
        return pattern

    def _line_matches_keyword(self, line: str, keyword: str) -> Optional[str]:
        """
        返回行中与关键词模糊匹配的完整变形体，或 None。
        """
        if not keyword or not line:
            return None

        keyword_len = len(keyword)
        max_len = keyword_len + self.max_errors

        for match in re.finditer(self._build_pattern(keyword), line, re.DOTALL):
            sub = match.group()
            if len(sub) <= max_len:
                return sub

        return None

    def detect(self, lines: List[str]) -> List[Dict]:
        """
        对每行文本执行检测。
        返回命中列表，每行只报告第一个命中。
        """
        if not self.keywords or not lines:
            return []

        # 多线程只在有必要时启用。
        # len(lines) == 1 时强制走单线程，避免为了单行文本创建线程池。
        if self.workers > 1 and len(lines) > 1:
            return self._detect_parallel(lines)

        return self._detect_serial(lines, start_line=1)

    def _detect_serial(self, lines: List[str], start_line: int = 1) -> List[Dict]:
        """
        单线程检测逻辑。

        注意：
        这个函数不会再调用 detect()，因此可以安全地被 _detect_parallel() 的 worker 调用，
        不会发生并行递归 / 线程池套娃。
        """
        results = []

        for offset, line in enumerate(lines):
            idx = start_line + offset

            if not line:
                continue

            # 根据大小写敏感选择预处理文本
            line_for_check = line if not self.ignore_case else line.lower()
            line_chars = set(line_for_check)

            for kw in self.keywords:
                # 快速预过滤：关键词所有字符必须出现在行中
                if not self._keyword_char_sets[kw].issubset(line_chars):
                    continue

                # ignore_case=True 时，keyword 也要转小写再构建正则
                kw_for_check = kw if not self.ignore_case else kw.lower()

                matched = self._line_matches_keyword(line_for_check, kw_for_check)
                if matched is not None:
                    results.append({
                        "line": idx,
                        "content": line.strip(),
                        "keyword": kw,
                        "matched_text": matched
                    })
                    break  # 一行只报告第一个命中

        return results

    def _detect_parallel(self, lines: List[str]) -> List[Dict]:
        """
        并行检测。

        修复点：
        每个 chunk 内部必须调用 _detect_serial()，不能调用 detect()，
        否则 workers > 1 时会继续进入 _detect_parallel()，造成无限套娃线程池。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not lines:
            return []

        worker_count = min(self.workers, len(lines))

        # 用向上取整，避免创建远超 worker_count 的小块
        chunk_size = (len(lines) + worker_count - 1) // worker_count

        chunks = []
        for i in range(0, len(lines), chunk_size):
            chunk = lines[i:i + chunk_size]
            start_idx = i + 1
            chunks.append((chunk, start_idx))

        all_results = []

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_chunk = {
                executor.submit(self._detect_serial, chunk, start): (chunk, start)
                for chunk, start in chunks
            }

            for future in as_completed(future_to_chunk):
                all_results.extend(future.result())

        all_results.sort(key=lambda r: r["line"])
        return all_results

    def _detect_chunk(self, chunk: List[str], start_line: int) -> List[Dict]:
        """
        保留这个方法只是为了兼容旧代码。

        重点：
        这里不能调用 self.detect(chunk)，必须调用单线程实现。
        """
        return self._detect_serial(chunk, start_line=start_line)

    def full_check(self, lines: List[str]) -> Dict:
        matches = self.detect(lines)
        return {
            "fuzzy_matches": matches,
            "total_danger_lines": len({m["line"] for m in matches}),
        }