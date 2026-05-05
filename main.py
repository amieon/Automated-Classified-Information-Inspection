import argparse
import uvicorn
import webbrowser
import threading
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# 模块注册表 - 可通过配置文件或环境变量动态扩展
CHECKER_MODULES = {
    "web": "checkers.web_checker.WebCheckerModule",
    "file": "checkers.file_checker.FileCheckerModule",
    "image": "checkers.image_checker.ImageCheckerModule",
    "audio": "checkers.audio_checker.AudioCheckerModule",
}

def create_app(modules: list = None):
    """根据传入的模块列表创建 FastAPI 应用"""
    app = FastAPI()
    templates = Jinja2Templates(directory="templates")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    # 动态注册选中的模块路由
    if modules is None:
        # 默认加载所有已安装模块
        modules = list(CHECKER_MODULES.keys())
    for mod_name in modules:
        if mod_name in CHECKER_MODULES:
            # 通过字符串动态导入模块
            import importlib
            mod_path, class_name = CHECKER_MODULES[mod_name].rsplit(".", 1)
            mod = importlib.import_module(mod_path)
            checker_class = getattr(mod, class_name)
            checker_instance = checker_class()
            checker_instance.register_routes(app)
        else:
            print(f"⚠️ 未知模块: {mod_name}，已跳过")

    return app

if __name__ == "__main__":
    # 使用 argparse 解析命令行参数（可选的）
    parser = argparse.ArgumentParser(description="启动涉密检查服务")
    parser.add_argument("--modules", nargs="+", default=list(CHECKER_MODULES.keys()),
                        help="要加载的检查模块，例如 --modules web file")
    args = parser.parse_args()

    # 创建应用，加载指定模块
    app = create_app(modules=args.modules)

    port = 8001
    threading.Timer(1.5, lambda: webbrowser.open_new(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port)