# checkers/web_checker.py
import sys
import asyncio
import aiohttp
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from typing import List, Set, Tuple, Optional
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker
from detector.leak_detector import LeakDetector
from utils.parallel import run_parallel          # 你的多进程并行工具


class WebCheckerModule(BaseChecker):
    def register_routes(self, app: FastAPI):
        @app.post("/check/web/url", response_class=HTMLResponse)
        async def check_web_url(
            url: str = Form(...),
            algorithm: str = Form("regex"),
            keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
            max_insert: int = Form(3)
        ):
            detector_kwargs = dict(keywords=keywords, algorithm=algorithm, max_insert=max_insert)
            # ---------- 1. 异步爬取 ----------
            all_pages = await self._crawl_website_async(url)
            # ---------- 2. 多进程涉密检测 ----------
            # ✅ 修复：把 detector_kwargs 和页面数据打包，传给模块级函数
            items_with_kwargs = [(detector_kwargs, item) for item in all_pages]
            results = await asyncio.to_thread(
                run_parallel,
                process_func=_check_single_page_with_kwargs,  # ← 模块顶层函数
                items=items_with_kwargs,
                max_workers=4,
                executor_type="process",
                collect_results=True
            )
            # ---------- 3 & 4 不变 ----------
            html = self._build_html_result(results)
            text_report = self._generate_text_report(results, mode="网页爬取检查")
            main_mod = sys.modules.get('__main__')
            if main_mod:
                main_mod.LATEST_REPORT = text_report
            return HTMLResponse(content=html)



    # ==================== 异步爬虫核心 ====================
    @staticmethod
    async def _crawl_website_async(
            start_url: str,
            max_pages: int = 500,
            concurrency: int = 10
    ) -> List[Tuple[str, str]]:
        parsed_start = urlparse(start_url)
        base_domain = parsed_start.netloc
        visited: Set[str] = set()
        to_visit: List[str] = []
        initial_url = start_url.split('#')[0]
        to_visit.append(initial_url)
        visited.add(initial_url)
        results: List[Tuple[str, str]] = []
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
        }
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch(session: aiohttp.ClientSession, url: str) -> Optional[Tuple[str, str, str]]:
            """返回 (url, html, content_type) 三元组，增加 content_type 用于判断解析器"""
            async with semaphore:
                try:
                    async with session.get(
                            url,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        if resp.status != 200:
                            return None
                        html = await resp.text(encoding='utf-8')
                        content_type = resp.headers.get('Content-Type', '')
                        return (url, html, content_type)
                except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                    print(f"  ⚠️ 访问失败: {url} - {e}")
                    return None

        async with aiohttp.ClientSession() as session:
            while to_visit and len(visited) < max_pages:
                batch = []
                while to_visit and len(batch) < concurrency * 2:
                    url = to_visit.pop(0)
                    batch.append(url)
                if not batch:
                    break
                tasks = [fetch(session, url) for url in batch]
                responses = await asyncio.gather(*tasks)
                for resp in responses:
                    if resp is None:
                        continue
                    page_url, html, content_type = resp
                    results.append((page_url, html))
                    # ✅ 根据 Content-Type 选择解析器
                    if 'xml' in content_type.lower() or page_url.endswith('.xml'):
                        # XML 类型的页面
                        try:
                            soup = BeautifulSoup(html, 'xml')  # 使用 Python 内置 xml 解析器
                        except Exception:
                            # 回退到 html.parser
                            soup = BeautifulSoup(html, 'html.parser')
                    elif 'html' in content_type.lower() or not content_type:
                        # HTML 类型或未知类型
                        soup = BeautifulSoup(html, 'html.parser')
                    else:
                        # 其他类型（如纯文本），跳过链接提取
                        continue
                    # 提取新链接
                    try:
                        for a_tag in soup.find_all('a', href=True):
                            href = a_tag['href'].strip()
                            full_url = urljoin(page_url, href)
                            parsed = urlparse(full_url)
                            if parsed.netloc != base_domain:
                                continue
                            if parsed.scheme not in ('http', 'https'):
                                continue
                            canonical = parsed._replace(fragment='').geturl().rstrip('/')
                            if canonical not in visited:
                                visited.add(canonical)
                                if any(ext in canonical.lower() for ext in
                                       ['.zip', '.rar', '.7z', '.exe', '.msi', '.pdf', '.docx']):
                                    continue
                                to_visit.append(canonical)
                    except Exception as e:
                        print(f"  ⚠️ 解析链接失败: {page_url} - {e}")
        print(f"  📊 爬取完成：共访问 {len(visited)} 个页面，成功获取 {len(results)} 个")
        return results

    # ==================== HTML 结果展示（保持原样） ====================
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

        # 弹窗 CSS 和 JS（原样）
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

    # ==================== 纯文本报告生成（保持原样） ====================
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


# ==================== 辅助工厂函数（子进程内创建独立 detector） ====================
def _make_checker(detector_kwargs: dict):
    """
    返回一个可在子进程中调用的检测函数。
    每次调用都会在子进程内重新实例化 LeakDetector（避免序列化问题）。
    """
    def check_single_page(item: Tuple[str, str]) -> dict:
        page_url, html_content = item
        # 每个子进程内独立创建 detector
        detector = LeakDetector(
            keywords=detector_kwargs['keywords'],
            algorithm=detector_kwargs['algorithm'],
            max_insert=detector_kwargs['max_insert']
        )
        soup = BeautifulSoup(html_content, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        leak_lines = detector.check_text(text)
        return {
            'url': page_url,
            'leak_lines': leak_lines,
            'note': '' if text else '无法读取页面内容'
        }
    return check_single_page

# ==================== 模块顶层函数（必须在此位置，可被 pickle） ====================
def _check_single_page_with_kwargs(item_with_kwargs):
    """
    供进程池调用的检测函数。
    参数：(detector_kwargs, (page_url, html_content))
    """
    detector_kwargs, (page_url, html_content) = item_with_kwargs
    detector = LeakDetector(
        keywords=detector_kwargs['keywords'],
        algorithm=detector_kwargs['algorithm'],
        max_insert=detector_kwargs['max_insert']
    )
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    text = soup.get_text(separator='\n', strip=True)
    leak_lines = detector.check_text(text)
    return {
        'url': page_url,
        'leak_lines': leak_lines,
        'note': '' if text else '无法读取页面内容'
    }