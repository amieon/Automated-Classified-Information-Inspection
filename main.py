import uvicorn
import webbrowser
import threading
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ✅ 修正导入路径
from checkers.web_checker import check_website

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/check/web", response_class=HTMLResponse)
async def check_web(url: str = Form(...)):
    try:
        result = check_website(url)
        # 构建 HTML 表格（避免引号冲突，使用双引号 + 三引号）
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
        # ✅ 显式返回 HTMLResponse (确保正确渲染)
        return HTMLResponse(content=html)
    except Exception as e:
        # 同上
        return HTMLResponse(content=f"<div class='alert alert-danger'>出错：{str(e)}</div>")

if __name__ == "__main__":
    # 使用固定端口 8000（或自动选端口，这里为了方便演示用8000）
    port = 8000
    threading.Timer(1.5, lambda: webbrowser.open_new(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port)