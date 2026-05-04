# checkers/web_checker.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker

# ---------- 原有的检测逻辑 ----------
class LeakDetector:
    """涉密信息检测器（保留原有逻辑）"""
    def __init__(self, keywords=None):
        self.keywords = keywords or ["机密", "秘密", "绝密", "内部", "保密", "隐私"]
        # 也可从配置文件读取

    def check_text(self, text):
        """检查文本中是否包含关键词"""
        lines = text.split('\n')
        leak_lines = []
        for i, line in enumerate(lines, 1):
            for kw in self.keywords:
                if kw in line:
                    # 记录行号和关键词
                    leak_lines.append((i, kw, line.strip()))
                    break
        return leak_lines

def check_website(start_url, max_pages=50):
    """爬取指定网站并检查涉密信息（原有的核心函数）"""
    detector = LeakDetector()
    visited = set()
    to_visit = {start_url}
    results = {
        'checked_pages': 0,
        'secret_pages': 0,
        'details': []
    }

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop()
        if url in visited:
            continue
        visited.add(url)

        try:
            resp = requests.get(url, timeout=10)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            page_text = soup.get_text(separator='\n')
            leak_lines = detector.check_text(page_text)

            page_detail = {'url': url, 'lines': leak_lines}
            results['details'].append(page_detail)
            results['checked_pages'] += 1

            if leak_lines:
                results['secret_pages'] += 1

            # 提取页面中所有链接
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                full_url = urljoin(url, href)
                # 仅保留同域名链接，避免爬到外网
                if urlparse(full_url).netloc == urlparse(start_url).netloc:
                    if full_url not in visited:
                        to_visit.add(full_url)

        except Exception as e:
            print(f"⚠️ 爬取 {url} 出错: {e}")

    return results

# ---------- FastAPI 模块封装 ----------
class WebCheckerModule(BaseChecker):
    """网页检查模块 - 注册 FastAPI 路由"""
    def register_routes(self, app: FastAPI):
        @app.post("/check/web", response_class=HTMLResponse)
        async def check_web(url: str = Form(...)):
            try:
                result = check_website(url)
                html = f"""
                <h3>✅ 检查结果</h3>
                <p>共检查 <strong>{result['checked_pages']}</strong> 个网页，
                   发现 <strong>{result['secret_pages']}</strong> 个含涉密信息</p>
                <table class="table table-bordered">
                    <thead><tr><th>网页 URL</th><th>涉密行数</th></tr></thead>
                    <tbody>
                """
                for detail in result['details']:
                    lines_str = "; ".join([f"第{l[0]}行" for l in detail['lines']])
                    html += f"<tr><td>{detail['url']}</td><td>{lines_str}</td></tr>"
                html += "</tbody></table>"
                return HTMLResponse(content=html)
            except Exception as e:
                return HTMLResponse(content=f"<div class='alert alert-danger'>出错：{str(e)}</div>")