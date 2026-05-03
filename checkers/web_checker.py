from typing import List, Dict
from utils.regex_leak_detector import RegexLeakDetector   # 之前的正则检测器
from utils.crawler import WebCrawler                      # 新爬虫

class WebChecker:
    def __init__(self, detector: RegexLeakDetector, crawler: WebCrawler = None):
        self.detector = detector
        self.crawler = crawler or WebCrawler(max_pages=20)   # 默认爬虫

    # ---------- 文本提取（内部使用） ----------
    @staticmethod
    def _extract_text(html: str) -> List[str]:
        """从 HTML 提取纯文本，返回行列表"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'noscript', 'iframe']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        return text.split('\n')

    # ---------- 竖排检测（内部使用） ----------
    @staticmethod
    def _detect_vertical(lines: List[str]) -> bool:
        if not lines:
            return False
        short = sum(1 for l in lines if len(l.strip()) <= 2)
        return short / len(lines) > 0.7

    # ---------- 核心接口 ----------
    def crawl_and_check(self, start_url: str) -> Dict:
        """
        爬取并检测，返回结果：
        {
            "success": True,
            "total_pages": 整站爬取总页数,
            "secret_pages": 发现涉密内容的页数,
            "details": [ 每一页的详细结果 ]
        }
        """
        # 1. 用爬虫获取所有页面
        raw_pages = self.crawler.crawl(start_url)
        details = []

        for page in raw_pages:
            url = page["url"]
            html = page["html"]

            # 2. 如果 HTML 为空则跳过
            if not html:
                details.append({"url": url, "status": "error", "leak_info": []})
                continue

            # 3. 提取文本行
            lines = self._extract_text(html)

            # 4. 根据是否竖排选择检测模式
            if self._detect_vertical(lines):
                leakage = self.detector.detect(lines, mode="vertical")
            else:
                exact = self.detector.detect(lines, mode="exact")
                fuzzy = self.detector.detect(lines, mode="fuzzy")
                leakage = exact + fuzzy

            # 5. 记录该页结果
            details.append({
                "url": url,
                "status": "危险" if leakage else "安全",
                "leak_info": leakage
            })

        # 6. 统计
        secret_pages = [d for d in details if d["status"] == "危险"]
        return {
            "success": True,
            "total_pages": len(raw_pages),
            "secret_pages": len(secret_pages),
            "details": details
        }


# ========== 测试 ==========
if __name__ == "__main__":
    # 测试用正则检测器
    detector = RegexLeakDetector(keywords=["秘密", "机密", "绝密", "内部"])
    checker = WebChecker(detector)

    # 测试一个地址（可以换成自己搭建的本地网站）
    result = checker.crawl_and_check("https://baidu.com")
    print(f"扫描完成，共 {result['total_pages']} 页，发现 {result['secret_pages']} 页存在风险")
    for p in result['details']:
        if p['leak_info']:
            print(f"  ❌ {p['url']}  风险数: {len(p['leak_info'])}")
        else:
            print(f"  ✅ {p['url']}  安全")