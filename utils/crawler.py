import requests
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

class WebCrawler:
    """
    站内爬虫：从起始URL开始，爬取同一域名的页面，返回页面HTML。
    """
    def __init__(self, max_pages: int = 20):
        self.max_pages = max_pages
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    @staticmethod
    def _normalize_url(base: str, href: str) -> Optional[str]:
        if not href:
            return None
        absolute = urljoin(base, href)
        if absolute.startswith(('http://', 'https://')):
            return absolute
        return None

    @staticmethod
    def _is_same_domain(url: str, base_domain: str) -> bool:
        try:
            parsed = urlparse(url)
            return parsed.netloc == base_domain
        except:
            return False

    def crawl(self, start_url: str) -> List[Dict]:
        """
        爬取同一域名的页面，返回列表，每个元素为 {"url": ..., "html": ...}
        """
        visited = set()
        to_visit = [start_url]
        base_domain = urlparse(start_url).netloc
        pages = []   # 存放抓取到的页面

        while to_visit and len(visited) < self.max_pages:
            url = to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)

            try:
                resp = self.session.get(url, timeout=10)
                resp.encoding = resp.apparent_encoding or 'utf-8'
                html = resp.text
            except Exception:
                # 请求失败时跳过，记录空 HTML
                pages.append({"url": url, "html": ""})
                continue

            pages.append({"url": url, "html": html})

            # 提取页面中的链接，准备后续爬取（仅同一域名）
            soup = BeautifulSoup(html, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a.get('href')
                abs_url = self._normalize_url(url, href)
                if abs_url and self._is_same_domain(abs_url, base_domain):
                    if abs_url not in visited and abs_url not in to_visit:
                        to_visit.append(abs_url)

        return pages