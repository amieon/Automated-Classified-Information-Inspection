import argparse
import uvicorn
import webbrowser
import threading
import time
import json
from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.staticfiles import StaticFiles
from utils.cache_manager import get_cache
from utils.middleware import ProcessTimeMiddleware
from utils.report_exporter import (
    DEFAULT_REPORT_FORMAT,
    REPORT_MEDIA_TYPES,
    build_report_exports,
    publish_latest_report,
    text_report_to_markdown,
)


LATEST_REPORT = ""
LATEST_REPORTS = {}

CHECKER_MODULES = {
    "web": "checkers.web_checker.WebCheckerModule",
    "file": "checkers.file_checker.FileCheckerModule",
    "image": "checkers.image_checker.ImageCheckerModule",
    "audio": "checkers.audio_checker.AudioCheckerModule",
    "db": "checkers.db_checker.DBCheckerModule",
}

def create_app(modules: list = None):
    app = FastAPI()
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")

    @app.get("/download_report")
    async def download_report(format: str = DEFAULT_REPORT_FORMAT):
        report_format = (format or DEFAULT_REPORT_FORMAT).lower()
        # 1. 优先使用已生成的最新报告（包含批量全检）
        reports = LATEST_REPORTS or build_report_exports(LATEST_REPORT)
        if not reports.get("txt"):
            return PlainTextResponse("暂无报告", status_code=400)
        if report_format not in REPORT_MEDIA_TYPES:
            return PlainTextResponse("不支持的报告格式", status_code=400)
        return Response(
            reports[report_format],
            media_type=REPORT_MEDIA_TYPES[report_format],
        )

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/api/cache/stats")
    async def cache_stats():
        cache = get_cache()
        return cache.stats()

    @app.post("/api/cache/clear")
    async def cache_clear():
        cache = get_cache()
        cache.clear()
        return {"status": "ok", "message": "缓存已清空"}

    app.add_middleware(ProcessTimeMiddleware)

    # ===== 动态注册各模块路由 =====
    if modules is None:
        modules = list(CHECKER_MODULES.keys())

    module_instances = {}
    for mod_name in modules:
        if mod_name in CHECKER_MODULES:
            mod_path, class_name = CHECKER_MODULES[mod_name].rsplit(".", 1)
            mod = __import__(mod_path, fromlist=[class_name])
            checker_class = getattr(mod, class_name)
            checker_instance = checker_class()
            checker_instance.register_routes(app)
            module_instances[mod_name] = checker_instance
        else:
            print(f"⚠️ 未知模块: {mod_name}，已跳过")

    # ===== 新增批量全检接口 =====
    @app.post("/check/batch")
    async def check_batch(
        # --- 原有表单参数 ---
        url_configs_json: str = Form(None),
        db_configs_json: str = Form(None),
        file_path: str = Form(None),
        image_path: str = Form(None),
        audio_path: str = Form(None),
        algorithm: str = Form("regex"),
        keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
        max_insert: int = Form(3),
        # --- 新增：支持直接返回报告格式 ---
        format: str = Query(DEFAULT_REPORT_FORMAT),
    ):
        # 如果 format 是 md 或 txt，但 LATEST_REPORT 已经有内容（例如页面加载后立即下载），
        # 可以直接返回 —— 但这里我们还是执行检查并生成报告，确保数据最新。
        # 为了避免重复执行，前端应该先调用（不带 format）获得 HTML，再调用带 format 下载。

        url_configs = json.loads(url_configs_json) if url_configs_json else []
        db_configs = json.loads(db_configs_json) if db_configs_json else []

        html_parts = []
        full_text = ""

        # --- 网页 ---
        if url_configs and "web" in module_instances:
            html_parts.append("<h3>🌐 网页批量检查</h3><hr>")
            web_checker = module_instances["web"]
            for cfg in url_configs:
                url = cfg.get("url", "").strip()
                if not url:
                    continue
                try:
                    h, t, _ = await web_checker.check_single_url(
                        url, algorithm, keywords, max_insert
                    )
                    html_parts.append(f"<h5>🔗 {url}</h5><hr>{h}")
                    full_text += f"URL: {url}\n{t}\n{'-'*40}\n"
                except Exception as e:
                    html_parts.append(
                        f"<div class='alert alert-danger'>❌ {url} 失败: {e}</div>"
                    )
                    full_text += f"URL: {url} 失败: {e}\n"

        # --- 数据库 ---
        if db_configs and "db" in module_instances:
            html_parts.append('<h3 class="mt-4">🗄️ 数据库批量检查</h3><hr>')
            from checkers.db_checker import DBConnector, DBCheckerModule
            db_checker = DBCheckerModule()

            for idx, cfg in enumerate(db_configs, 1):
                db_type = cfg.get("db_type", "mysql")
                host = cfg.get("host", "localhost")
                port = int(cfg.get("port", 3306))
                user = cfg.get("user", "")
                password = cfg.get("password", "")
                database = cfg.get("database") or cfg.get("dbname", "")
                scan_all = cfg.get("scan_all", False)

                conn_kwargs = dict(
                    db_type=db_type, host=host, port=port,
                    user=user, password=password, database=database,
                )
                connector = DBConnector(**conn_kwargs)
                if not connector.connect():
                    html_parts.append(
                        f"<div class='alert alert-danger'>❌ 数据库 #{idx} 连接失败</div>"
                    )
                    continue
                try:
                    if scan_all:
                        # 扫描所有库
                        databases = connector.get_databases()
                        for db_name in databases:
                            conn_kwargs["database"] = db_name
                            connector2 = DBConnector(**conn_kwargs)
                            if not connector2.connect():
                                continue
                            try:
                                tables = connector2.get_tables()
                                if not tables:
                                    continue
                                results = db_checker._parallel_scan(
                                    tables=tables, keywords=keywords,
                                    algorithm=algorithm, max_insert=max_insert,
                                    **conn_kwargs,
                                )
                                db_info = f"{db_type} {host}:{port}/{db_name}"
                                html_block = db_checker._build_html_result(
                                    results, tables, len(tables), db_path=db_info
                                )
                                text_block = db_checker._generate_text_report(
                                    results, tables, len(tables), f"数据库 #{idx}: {db_info}"
                                )
                                html_parts.append(
                                    f"<h5>🗄️ {db_info}</h5><hr>{html_block}"
                                )
                                full_text += text_block + "\n\n"
                            finally:
                                connector2.disconnect()
                    else:
                        tables = connector.get_tables()
                        if not tables:
                            html_parts.append(
                                f"<div class='alert alert-warning'>数据库 #{idx} 无表</div>"
                            )
                            continue
                        results = db_checker._parallel_scan(
                            tables=tables, keywords=keywords,
                            algorithm=algorithm, max_insert=max_insert,
                            **conn_kwargs,
                        )
                        db_info = f"{db_type} {host}:{port}/{database}"
                        html_block = db_checker._build_html_result(
                            results, tables, len(tables), db_path=db_info
                        )
                        text_block = db_checker._generate_text_report(
                            results, tables, len(tables), f"数据库 #{idx}: {db_info}"
                        )
                        html_parts.append(
                            f"<h5>🗄️ 数据库 #{idx}：{db_info}</h5><hr>{html_block}"
                        )
                        full_text += text_block + "\n\n"
                finally:
                    connector.disconnect()

        # --- 文件/图片/音频路径 ---
        path_tasks = [
            ("file", file_path, "📁 文件路径检查"),
            ("image", image_path, "🖼️ 图片路径检查"),
            ("audio", audio_path, "🎵 音频路径检查"),
        ]
        for mod_key, path_val, title in path_tasks:
            if path_val and path_val.strip() and mod_key in module_instances:
                html_parts.append(f"<h3 class='mt-3'>{title}</h3><hr>")
                checker = module_instances[mod_key]
                try:
                    h, t, _ = await checker.check_path(
                        path_val.strip(), algorithm, keywords, max_insert
                    )
                    html_parts.append(h)
                    full_text += t + "\n"
                except AttributeError:
                    html_parts.append(
                        f"<div class='alert alert-warning'>{title} 模块未实现批量接口</div>"
                    )
                except Exception as e:
                    html_parts.append(
                        f"<div class='alert alert-danger'>{title} 失败: {e}</div>"
                    )
                    full_text += f"{title} 失败: {e}\n"

        full_html = "".join(html_parts)

        # 保存报告（用于后续 /download_report）
        global LATEST_REPORT, LATEST_REPORTS
        LATEST_REPORT = full_text
        LATEST_REPORTS = {}                # 让 download_report 动态生成 MD/TXT
        publish_latest_report(full_text)

        # --- 如果明确要求返回文本报告，直接返回纯文本/Markdown ---
        report_format = format.lower()
        if report_format in ("md", "txt"):
            reports = build_report_exports(full_text)  # 会调用 text_report_to_markdown
            return Response(
                reports[report_format],
                media_type=REPORT_MEDIA_TYPES[report_format],
            )

        # 默认返回 HTML
        return HTMLResponse(content=full_html)

    # ===== 扫描数据库所有库 =====
    @app.post("/check/db/scan-all", response_class=HTMLResponse)
    async def db_scan_all(
        db_type: str = Form("mysql"),
        host: str = Form("localhost"),
        port: int = Form(None),
        dbname: str = Form(""),
        user: str = Form(""),
        password: str = Form(""),
        conn_str: str = Form(""),
        scan_all: str = Form("true"),
        algorithm: str = Form("regex"),
        keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
        max_insert: int = Form(3),
        # --- 同样支持直接返回报告 ---
        format: str = Query(DEFAULT_REPORT_FORMAT),
    ):
        from checkers.db_checker import DBConnector, DBCheckerModule

        html_parts = []
        full_text = ""

        conn_kwargs = dict(
            db_type=db_type, host=host, port=port or 3306,
            user=user, password=password, database=dbname,
        )

        connector = DBConnector(**conn_kwargs)
        if not connector.connect():
            return HTMLResponse(
                "<div class='alert alert-danger'>❌ 数据库连接失败</div>"
            )

        try:
            databases = connector.get_databases()
            if not databases:
                return HTMLResponse(
                    "<div class='alert alert-warning'>⚠️ 连接成功，但未找到任何库</div>"
                )

            db_checker = DBCheckerModule()
            all_db_count = len(databases)
            scanned_count = 0

            for db_name in databases:
                conn_kwargs["database"] = db_name
                connector2 = DBConnector(**conn_kwargs)
                if not connector2.connect():
                    html_parts.append(
                        f"<div class='alert alert-warning'>⚠️ 库 {db_name} 连接失败，跳过</div>"
                    )
                    continue
                try:
                    tables = connector2.get_tables()
                    if not tables:
                        html_parts.append(
                            f"<div class='text-muted'>📭 库 <b>{db_name}</b>：无表</div>"
                        )
                        continue

                    results = db_checker._parallel_scan(
                        tables=tables, keywords=keywords,
                        algorithm=algorithm, max_insert=max_insert,
                        **conn_kwargs,
                    )
                    db_info = f"{db_type} {host}:{port}/{db_name}"
                    html_block = db_checker._build_html_result(
                        results, tables, len(tables), db_path=db_info
                    )
                    text_block = db_checker._generate_text_report(
                        results, tables, len(tables), f"数据库: {db_info}"
                    )

                    html_parts.append(
                        f"<h5 style='margin-top:1rem;'>🗄️ {db_info}（{len(tables)} 张表）</h5><hr>{html_block}"
                    )
                    full_text += text_block + "\n\n"
                    scanned_count += 1
                finally:
                    connector2.disconnect()

            # 汇总标题
            summary = (
                f"<h4>🔍 扫描所有库完成：共 {all_db_count} 个库，"
                f"实际扫描 {scanned_count} 个库</h4><hr>"
            )
            full_html = summary + "".join(html_parts)

            global LATEST_REPORT, LATEST_REPORTS
            LATEST_REPORT = full_text
            LATEST_REPORTS = {}
            publish_latest_report(full_text)

            # 如果要求报告格式，返回文本
            report_format = format.lower()
            if report_format in ("md", "txt"):
                reports = build_report_exports(full_text)
                return Response(
                    reports[report_format],
                    media_type=REPORT_MEDIA_TYPES[report_format],
                )

            return HTMLResponse(content=full_html)

        finally:
            connector.disconnect()

    @app.post("/report/combine")
    async def combine_reports(reports_json: str = Form("[]")):
        """接收前端传来的各子任务纯文本，合并后存入全局报告"""
        import json as _json
        parts = _json.loads(reports_json)
        combined = "\n\n---\n\n".join(parts)
        global LATEST_REPORT, LATEST_REPORTS
        LATEST_REPORT = combined
        LATEST_REPORTS = {}
        publish_latest_report(combined)
        return {"status": "ok", "count": len(parts)}


    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动涉密检查服务")
    parser.add_argument(
        "--modules", nargs="+", default=list(CHECKER_MODULES.keys()),
        help="要加载的检查模块，例如 --modules web file",
    )
    args = parser.parse_args()
    app = create_app(modules=args.modules)
    port = 8001
    threading.Timer(1.5, lambda: webbrowser.open_new(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port)