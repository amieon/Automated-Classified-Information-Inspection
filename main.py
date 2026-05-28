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

from checkers.batch_handler import check_batch_core, db_scan_all_core
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