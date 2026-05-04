# checkers/file_checker.py
import os
from pathlib import Path
from typing import List
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker
from utils.leak_detector import LeakDetector

# ==================== 文件类型识别（基于文件头） ====================
FILE_SIGNATURES = {
    'pdf': b'%PDF',
    'zip': b'PK\x03\x04',
    'gzip': b'\x1f\x8b',
    'png': b'\x89PNG\r\n\x1a\n',
    'jpg': b'\xff\xd8\xff',
    'bmp': b'BM',
    'gif': b'GIF8',
    'xml': b'<?xml',
    'html': b'<htm',
    'rtf': b'{\\rtf',
    'exe': b'MZ',
    'elf': b'\x7fELF',
}

def guess_file_type(file_source, is_bytes: bool = False) -> str:
    """
    根据文件头猜测类型
    :param file_source: 文件路径（字符串）或字节数据
    :param is_bytes: 如果为True，file_source视为字节数据
    :return: 类型字符串 ('text', 'pdf', 'zip', 'html', 'xml', 'binary'等)
    """
    if is_bytes:
        header = file_source[:512]
    else:
        with open(file_source, 'rb') as f:
            header = f.read(512)

    if not header:
        return 'empty'

    # 遍历魔数签名
    for file_type, signature in FILE_SIGNATURES.items():
        if header.startswith(signature):
            return file_type

    # 检查是否大部分是可打印 ASCII / 常见文本字符
    printable = sum(1 for byte in header if 32 <= byte <= 126 or byte in (10, 13, 9))
    if printable >= len(header) * 0.9:
        return 'text'
    else:
        return 'binary'

def read_text_from_file(file_path: str) -> str:
    """根据文件类型读取文本内容（目前仅处理纯文本/代码/标记语言）"""
    ftype = guess_file_type(file_path)
    # 这些类型可直接按 UTF-8 读取
    if ftype in ('text', 'html', 'xml', 'rtf'):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except:
            return ""
    # 对于其他格式（PDF, DOCX, ZIP等）暂不处理，返回空字符串
    return ""

def read_text_from_bytes(content: bytes, filename: str) -> str:
    """从字节流中读取文本（简单解码）"""
    # 优先尝试 UTF-8 解码（忽略错误）
    return content.decode('utf-8', errors='ignore')

# ==================== FastAPI 路由注册 ====================
class FileCheckerModule(BaseChecker):
    def register_routes(self, app: FastAPI):
        # ------ 方式1：输入路径 ------
        @app.post("/check/file/path", response_class=HTMLResponse)
        async def check_file_path(path: str = Form(...)):
            detector = LeakDetector()
            p = Path(path)
            if not p.exists():
                return HTMLResponse(content="<div class='alert alert-danger'>路径不存在</div>")

            results = []
            if p.is_file():
                text = read_text_from_file(str(p))
                leak_lines = detector.check_text(text) if text else []
                results.append({
                    'path': str(p),
                    'leak_lines': leak_lines,
                    'file_type': guess_file_type(str(p)),
                    'note': '' if text else '无法读取文本内容'
                })
            elif p.is_dir():
                for root, dirs, files in os.walk(p):
                    for file in files:
                        fp = Path(root) / file
                        text = read_text_from_file(str(fp))
                        leak_lines = detector.check_text(text) if text else []
                        results.append({
                            'path': str(fp),
                            'leak_lines': leak_lines,
                            'file_type': guess_file_type(str(fp)),
                            'note': '' if text else '无法读取文本内容'
                        })
            else:
                return HTMLResponse(content="<div class='alert alert-danger'>既不是文件也不是文件夹</div>")

            # 生成 HTML 结果表格
            return self._build_html_result(results)

        # ------ 方式2：文件上传（支持 webkitdirectory）------
        @app.post("/check/file/upload", response_class=HTMLResponse)
        async def check_file_upload(files: List[UploadFile] = File(...)):
            detector = LeakDetector()
            results = []
            for file in files:
                content = await file.read()
                text = read_text_from_bytes(content, file.filename)
                leak_lines = detector.check_text(text) if text else []
                results.append({
                    'path': file.filename,
                    'leak_lines': leak_lines,
                    'file_type': guess_file_type(content, is_bytes=True),
                    'note': '' if text else '无法读取文本内容'
                })

            return self._build_html_result(results)

    @staticmethod
    def _build_html_result(results: list) -> str:
        """将检查结果渲染成 HTML 表格"""
        total_leak = sum(1 for r in results if r['leak_lines'])
        html = f"""
        <h3>✅ 文件检查结果</h3>
        <p>共检查 <strong>{len(results)}</strong> 个文件，发现 <strong>{total_leak}</strong> 个含涉密信息</p>
        <table class="table table-bordered">
            <thead>
                <tr><th>文件路径</th><th>类型</th><th>涉密行数</th><th>备注</th></tr>
            </thead>
            <tbody>
        """
        for r in results:
            lines_str = "; ".join([f"第{l[0]}行" for l in r['leak_lines']]) if r['leak_lines'] else "无"
            note = r.get('note', '')
            html += f"<tr><td>{r['path']}</td><td>{r['file_type']}</td><td>{lines_str}</td><td>{note}</td></tr>"
        html += "</tbody></table>"
        return html