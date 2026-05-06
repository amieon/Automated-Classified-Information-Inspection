# checkers/web_checker.py
import sys
import re
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from typing import List, Set, Tuple
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker
from utils.leak_detector import LeakDetector


class WebCheckerModule(BaseChecker):
    def register_routes(self, app: FastAPI):
        @app.post("/check/web/url", response_class=HTMLResponse)
        async def check_web_url(url: str = Form(...)):
            detector = LeakDetector()

            # ---------- 1. 爬取网站所有页面 ----------
            all_pages = self._crawl_website(url)

            # ---------- 2. 逐页检查涉密信息 ----------
            results = []
            for page_url, html_content in all_pages:
                # 提取纯文本

                soup = BeautifulSoup(html_content, 'html.parser')

                # 去掉 script 和 style 标签
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()

                text = soup.get_text(separator='\n', strip=True)

                # 检查涉密信息
                leak_lines = detector.check_text(text)

                results.append({
                    'url': page_url,
                    'leak_lines': leak_lines,
                    'note': '' if text else '无法读取页面内容'
                })

            # ---------- 3. 生成 HTML 结果 ----------
            html = self._build_html_result(results)

            # ---------- 4. 生成纯文本报告并写入全局变量 ----------
            text_report = self._generate_text_report(results, mode="网页爬取检查")
            main_mod = sys.modules.get('__main__')
            if main_mod:
                main_mod.LATEST_REPORT = text_report

            return HTMLResponse(content=html)

    # ==================== 爬虫核心方法 ====================
    @staticmethod
    def _crawl_website(start_url: str, max_pages: int = 500) -> List[Tuple[str, str]]:
        """
        从 start_url 出发，遍历网站所有同域名页面
        返回 [(url, html_content), ...]
        """
        # 解析起始域名，只爬同一域名下的页面
        parsed_start = urlparse(start_url)
        base_domain = parsed_start.netloc
        base_scheme = parsed_start.scheme

        visited: Set[str] = set()  # 已访问的 URL
        to_visit: Set[str] = {start_url}  # 待访问的 URL
        results: List[Tuple[str, str]] = []

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        # 循环爬取，直到没有新页面或达到上限
        while to_visit and len(visited) < max_pages:
            # 取出一个待访问的 URL
            current_url = to_visit.pop()
            if current_url in visited:
                continue

            # 去除 URL 中的锚点（#后面的部分）
            clean_url = current_url.split('#')[0]
            if clean_url in visited:
                continue

            try:
                # 请求页面
                resp = requests.get(clean_url, headers=headers, timeout=10)
                resp.encoding = 'utf-8'
                visited.add(clean_url)

                if resp.status_code != 200:
                    continue

                html_content = resp.text
                results.append((clean_url, html_content))

                # 解析页面中的所有链接
                content_type = resp.headers.get('Content-Type', '')
                if 'text/html' in content_type or 'application/xhtml' in content_type:
                    parser = 'html.parser'
                else:
                    # XML、RSS、Atom 等
                    parser = 'lxml-xml'  # 需安装 lxml，或回退到 'xml' 默认解析器
                    # 如果没有 lxml，BeautifulSoup 会回退到 Python 内置的 xml 解析器（功能有限，但不报错）
                soup = BeautifulSoup(html_content, parser)

                for a_tag in soup.find_all('a', href=True):
                    href = a_tag['href'].strip()

                    # 拼接完整 URL
                    full_url = urljoin(clean_url, href)

                    # 只保留同域名、http/https 协议的链接
                    parsed = urlparse(full_url)
                    if parsed.netloc != base_domain:
                        continue  # 忽略其他网站的链接（老师要求）
                    if parsed.scheme not in ('http', 'https'):
                        continue

                    # 去除锚点和末尾斜杠（避免重复）
                    canonical = parsed._replace(fragment='').geturl()
                    if canonical.endswith('/'):
                        canonical = canonical[:-1]

                    # 避免重复和已访问
                    if canonical not in visited and canonical not in to_visit:
                        # 跳过文件下载链接（可选）
                        if any(ext in canonical.lower() for ext in ['.zip', '.rar', '.7z', '.exe', '.msi']):
                            continue
                        to_visit.add(canonical)

            except Exception as e:
                print(f"  ⚠️ 访问失败: {clean_url} - {e}")
                visited.add(clean_url)
                continue

        print(f"  📊 爬取完成：共访问 {len(visited)} 个页面，成功获取 {len(results)} 个")
        return results

    # ==================== HTML 结果展示 ====================
    @staticmethod
    def _build_html_result(results: list) -> str:
        import html as html_mod
        total_pages = len(results)
        total_leak = sum(1 for r in results if r['leak_lines'])

        html_str = f"""
        <h3>✅ 网页检查结果</h3>
        <p>共检查 <strong>{total_pages}</strong> 个网页，发现 <strong>{total_leak}</strong> 个含涉密信息</p>
        <table class="table table-bordered">
            <thead>
                <tr><th>网页 URL</th><th>涉密行数</th><th>操作</th><th>备注</th></tr>
            </thead>
            <tbody>
        """

        def format_leak_lines(leak_lines: list) -> str:
            lines = []
            for line_no, keyword, content in leak_lines:
                lines.append(f'第{line_no}行，关键字为：“{keyword}”，具体内容：“{content}”')
            return '\n'.join(lines)

        for i, r in enumerate(results):
            lines_detail = format_leak_lines(r['leak_lines']) if r['leak_lines'] else "无"
            lines_str = "; ".join([f"第{l[0]}行" for l in r['leak_lines']]) if r['leak_lines'] else "无"
            note = r.get('note', '')

            btn = (
                f'<button class="btn btn-sm btn-outline-info" '
                f'onclick="showModal(\'modal_{i}\')">'
                f'查看详情</button>'
            )

            modal = f"""
            <div id="modal_{i}" class="my-modal-overlay" style="display:none;" onclick="closeModal('modal_{i}')">
                <div class="my-modal-content" onclick="event.stopPropagation();">
                    <div class="my-modal-header">
                        <span class="my-modal-title">{html_mod.escape(r['url'])}</span>
                        <span class="my-modal-close" onclick="closeModal('modal_{i}')">&times;</span>
                    </div>
                    <div class="my-modal-body">
                        <p><strong>网页URL：</strong>{html_mod.escape(r['url'])}</p>
                        <p><strong>涉密行数：</strong>{lines_str}</p>
                        <hr>
                        <pre style="white-space:pre-wrap; word-wrap:break-word; max-height:400px; overflow-y:auto;">{lines_detail}</pre>
                    </div>
                    <div class="my-modal-footer">
                        <button class="btn btn-sm btn-secondary" onclick="closeModal('modal_{i}')">关闭</button>
                    </div>
                </div>
            </div>
            """

            html_str += f"""
            <tr>
                <td style="word-break:break-all; max-width:400px;">{r['url']}</td>
                <td>{lines_str}</td>
                <td>{btn}{modal}</td>
                <td>{note}</td>
            </tr>
            """

        html_str += "</tbody></table>"

        # 弹窗 CSS 和 JS
        html_str += """
        <style>
            .my-modal-overlay {
                position: fixed;
                top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(0,0,0,0.5);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 9999;
            }
            .my-modal-content {
                background: #fff;
                border-radius: 8px;
                max-width: 700px;
                width: 90%;
                max-height: 80%;
                display: flex;
                flex-direction: column;
                box-shadow: 0 4px 15px rgba(0,0,0,0.3);
            }
            .my-modal-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px 16px;
                border-bottom: 1px solid #dee2e6;
                font-size: 16px;
                font-weight: bold;
            }
            .my-modal-close {
                font-size: 24px;
                font-weight: bold;
                cursor: pointer;
                color: #999;
            }
            .my-modal-close:hover { color: #000; }
            .my-modal-body {
                padding: 16px;
                overflow-y: auto;
                flex: 1;
            }
            .my-modal-footer {
                padding: 10px 16px;
                border-top: 1px solid #dee2e6;
                text-align: right;
            }
        </style>
        <script>
            function showModal(id) {
                document.getElementById(id).style.display = 'flex';
            }
            function closeModal(id) {
                document.getElementById(id).style.display = 'none';
            }
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape') {
                    document.querySelectorAll('.my-modal-overlay').forEach(function(el) {
                        if (el.style.display === 'flex') {
                            el.style.display = 'none';
                        }
                    });
                }
            });
        </script>
        """
        return html_str

    # ==================== 纯文本报告生成 ====================
    @staticmethod
    def _generate_text_report(results: list, mode: str = "") -> str:
        from datetime import datetime
        lines = []
        lines.append("=" * 60)
        lines.append("          网页涉密数据检查报告")
        lines.append("=" * 60)
        lines.append(f"检查模式: {mode}")
        lines.append(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        total_pages = len(results)
        leak_pages = sum(1 for r in results if r['leak_lines'])
        total_leak_lines = sum(len(r['leak_lines']) for r in results)
        lines.append(f"共检查网页数: {total_pages}")
        lines.append(f"发现涉密网页数: {leak_pages}")
        lines.append(f"涉密数据总条数: {total_leak_lines}")
        lines.append("-" * 60)

        if not results:
            lines.append("没有扫描任何网页。")
        else:
            for i, r in enumerate(results, 1):
                url = r.get('url', '未知URL')
                leak_lines = r.get('leak_lines', [])
                note = r.get('note', '')
                lines.append(f"\n【网页 {i}】{url}")
                if note:
                    lines.append(f"  备注: {note}")
                if leak_lines:
                    lines.append(f"  涉密信息 ({len(leak_lines)} 处):")
                    for line_no, keyword, content in leak_lines:
                        lines.append(f"    第{line_no}行 | 关键词 [{keyword}] → {content}")
                else:
                    lines.append("  未发现涉密数据。")

        lines.append("=" * 60)
        lines.append("报告结束")
        return "\n".join(lines)