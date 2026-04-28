import uvicorn
import webbrowser
import threading
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 导入网页检查模块（后面会定义）
from web_checker import checkers

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/check/web", response_class=HTMLResponse)
async def check_web(url: str = Form(...)):
    try:
        result = check_website(url)
        # 将结果渲染成 HTML 表格返回
        html = f"""
        <h3>检查结果</h3>
        <p>共检查网页：{result['checked_pages']} 个</p>
        <p>含涉密信息网页：{result['secret_pages']} 个</p>
        <table class="table table-bordered">
            <thead><tr><th>网页 URL</th><th>涉密行</th></tr></thead>
            <tbody>
        """
        for detail in result['details']:
            lines_str = "; ".join([f"第{l[0]}行" for l in detail['lines']])
            html += f"<tr><td>{detail['url']}</td><td>{lines_str}</td></tr>"
        html += "</tbody></table>"
        return html
    except Exception as e:
        return f"<div class='alert alert-danger'>检查出错：{str(e)}</div>"

if __name__ == "__main__":
    # 1.5秒后自动打开浏览器
    threading.Timer(1.5, lambda: webbrowser.open_new("http://127.0.0.1:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)