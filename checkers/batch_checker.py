# ========== 放在你的主 app 文件或一个新的 batch_checker 里 ==========
import json
from typing import List
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

@app.post("/check/batch", response_class=HTMLResponse)
async def check_batch(
    url_configs_json: str = Form(None),
    db_configs_json: str = Form(None),
    file_path: str = Form(None),
    image_path: str = Form(None),
    audio_path: str = Form(None),
    algorithm: str = Form("regex"),
    keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
    max_insert: int = Form(3)
):
    """
    批量全检接口：
    一次性提交多个 URL、多个数据库配置、以及文件/图片/音频路径。
    """
    url_configs = []
    db_configs = []
    html_parts = []
    full_text_report = ""

    # ===== 1. 解析 URL 配置 =====
    if url_configs_json:
        try:
            url_configs = json.loads(url_configs_json)
        except:
            return HTMLResponse(content="<div class='alert alert-danger'>URL 配置 JSON 格式错误</div>")

    # ===== 2. 解析数据库配置 =====
    if db_configs_json:
        try:
            db_configs = json.loads(db_configs_json)
        except:
            return HTMLResponse(content="<div class='alert alert-danger'>数据库配置 JSON 格式错误</div>")

    # ===== 3. 处理 URL =====
    if url_configs:
        urls = [cfg.get("url", "").strip() for cfg in url_configs if cfg.get("url", "").strip()]
        if urls:
            html_parts.append('<h3>🌐 网页批量检查</h3><hr>')
            # 复用已有的网页检查逻辑（假设 /check/web/url 支持多个 url 参数）
            fd = FormData()
            for u in urls:
                fd.append('url', u)
            fd.append('algorithm', algorithm)
            fd.append('keywords', keywords)
            fd.append('max_insert', str(max_insert))
            # 调用内部函数，避免重复创建请求
            url_html, url_text, url_leak_count = await _batch_check_urls(urls, algorithm, keywords, max_insert)
            html_parts.append(url_html)
            full_text_report += url_text + "\n"

    # ===== 4. 处理数据库 =====
    if db_configs:
        html_parts.append('<h3 class="mt-4">🗄️ 数据库批量检查</h3><hr>')
        db_html, db_text, db_leak_count, db_affected = _batch_check_databases(
            db_configs, algorithm, keywords, max_insert
        )
        html_parts.append(db_html)
        full_text_report += db_text + "\n"

    # ===== 5. 处理路径检查（文件/图片/音频） =====
    path_tasks = [
        ("📁 文件路径检查", "/check/file/path", file_path),
        ("🖼️ 图片路径检查", "/check/image/path", image_path),
        ("🎵 音频路径检查", "/check/audio/path", audio_path),
    ]
    for title, endpoint, path_val in path_tasks:
        if path_val and path_val.strip():
            html_parts.append(f'<h3 class="mt-3">{title}</h3><hr>')
            fd = FormData()
            fd.append('path', path_val.strip())
            fd.append('algorithm', algorithm)
            fd.append('keywords', keywords)
            fd.append('max_insert', str(max_insert))
            # 调用对应的路径检查函数（你可以用同样的方式包装）
            # 这里假设各模块都提供了类似的静态方法
            result_html, result_text = await _batch_check_path(endpoint, fd)
            html_parts.append(result_html)
            full_text_report += result_text + "\n"

    # ===== 6. 汇总 =====
    full_html = "".join(html_parts)
    # 保存报告
    from utils.report_exporter import publish_latest_report
    summary = f"批量全检报告\n{full_text_report}"
    publish_latest_report(summary)

    return HTMLResponse(content=full_html)


# ==================== 辅助函数（内部复用） ====================

async def _batch_check_urls(urls: List[str], algorithm, keywords, max_insert):
    """调用已有的网页检查器，返回 (html, text_report, leak_count)"""
    # 假设 WebCheckerModule 可以这样批量使用
    from .web_checker import WebCheckerModule
    checker = WebCheckerModule()
    # 构建参数传给已有的检查函数，具体根据你的实际代码调整
    results_html = []
    results_text = []
    total_leaks = 0
    for url in urls:
        try:
            html_single, text_single, leaks = await checker.check_single_url(
                url, algorithm, keywords, max_insert
            )
            results_html.append(f"<h5>🔗 {url}</h5>{html_single}")
            results_text.append(f"URL: {url}\n{text_single}\n{'-'*40}")
            total_leaks += leaks
        except Exception as e:
            results_html.append(f"<div class='alert alert-danger'>{url} 检查失败: {e}</div>")
            results_text.append(f"URL: {url} 失败: {e}")
    return "\n".join(results_html), "\n".join(results_text), total_leaks


def _batch_check_databases(db_configs, algorithm, keywords, max_insert):
    """复用 DBCheckerModule 的逻辑，返回 (html, text, leak_count, affected_tables)"""
    from .db_checker import DBConnector, DBCheckerModule
    checker = DBCheckerModule()
    all_html = []
    all_text = []
    total_leaks = 0
    total_affected = 0

    for idx, cfg in enumerate(db_configs, 1):
        db_type = cfg.get("db_type", "mysql")
        host = cfg.get("host", "localhost")
        port = int(cfg.get("port", 3306))
        user = cfg.get("user", "")
        password = cfg.get("password", "")
        database = cfg.get("database") or cfg.get("dbname", "")
        conn_str = cfg.get("conn_str", "")

        if conn_str:
            all_html.append(f"<div class='alert alert-warning'>数据库 #{idx}：连接字符串模式暂不支持</div>")
            continue

        conn_kwargs = dict(db_type=db_type, host=host, port=port,
                           user=user, password=password, database=database)
        connector = DBConnector(**conn_kwargs)
        if not connector.connect():
            all_html.append(f"<div class='alert alert-danger'>数据库 #{idx} 连接失败</div>")
            continue

        try:
            tables = connector.get_tables()
            if not tables:
                all_html.append(f"<div class='alert alert-warning'>数据库 #{idx} 无表</div>")
                continue

            results = checker._parallel_scan(
                tables=tables,
                keywords=keywords,
                algorithm=algorithm,
                max_insert=max_insert,
                **conn_kwargs
            )
            db_info = f"{db_type} {host}:{port}/{database}"
            html_block = checker._build_html_result(results, tables, len(tables), db_path=db_info)
            all_html.append(f"<h5>🗄️ 数据库 #{idx}：{db_info}</h5><hr>{html_block}")

            text_block = checker._generate_text_report(results, tables, len(tables), f"数据库 #{idx}: {db_info}")
            all_text.append(text_block)
            total_leaks += len(results)
            total_affected += len(set(r['table'] for r in results))
        finally:
            connector.disconnect()

    return "\n".join(all_html), "\n\n".join(all_text), total_leaks, total_affected


async def _batch_check_path(endpoint: str, form_data):
    """简单地发送请求到已有路径检查端点，返回 HTML"""
    # 这里可以复用已有的路径检查逻辑，或者提取公共函数
    # 临时方案：直接调用现有的路径检查函数（假设每个模块都有同步/异步方法）
    # 下面以文件路径为例，你需要根据实际结构调整
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(endpoint, data=form_data)
        html = resp.text
    return html, html   # 文本报告可以用 HTML 代替，或者单独解析