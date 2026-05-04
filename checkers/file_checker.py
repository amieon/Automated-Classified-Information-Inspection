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
    'pdf':  b'%PDF',
    'zip':  b'PK\x03\x04',
    'gzip': b'\x1f\x8b',
    'png':  b'\x89PNG\r\n\x1a\n',
    'jpg':  b'\xff\xd8\xff',
    'bmp':  b'BM',
    'gif':  b'GIF8',
    'xml':  b'<?xml',
    'html': b'<htm',
    'rtf':  b'{\\rtf',
    'exe':  b'MZ',
    'elf':  b'\x7fELF',
    'ole2': b'\xd0\xcf\x11\xe0',  # doc/xls/ppt 旧格式
    # 注意：docx/xlsx/pptx 会先被 'zip' 匹配（PK\x03\x04）
    # txt 没有固定魔数，由 is_text_content() 判断
}

def is_text_content(data: bytes, threshold: float = 0.9) -> bool:
    """
    判断字节数据是否为文本内容（支持 UTF-8 中文等）
    方法：尝试用 UTF-8 解码，计算成功解码的比例
    """
    if not data:
        return False

    # 方法1：直接尝试 UTF-8 解码
    try:
        data.decode('utf-8')
        return True  # 完全解码成功，肯定是文本
    except UnicodeDecodeError:
        pass

    # 方法2：检查是否大部分是 ASCII + 常见控制字符
    # 适用于纯英文文本
    ascii_printable = sum(1 for byte in data if 32 <= byte <= 126 or byte in (9, 10, 13))
    if ascii_printable >= len(data) * threshold:
        return True

    # 方法3：统计有效 UTF-8 序列的比例（处理混合内容）
    valid_utf8_bytes = 0
    i = 0
    while i < len(data):
        byte = data[i]
        if byte <= 0x7F:
            valid_utf8_bytes += 1
            i += 1
        elif 0xC2 <= byte <= 0xDF:
            # 2字节 UTF-8
            if i + 1 < len(data) and 0x80 <= data[i+1] <= 0xBF:
                valid_utf8_bytes += 2
                i += 2
            else:
                i += 1
        elif 0xE0 <= byte <= 0xEF:
            # 3字节 UTF-8（中文常用范围）
            if i + 2 < len(data) and 0x80 <= data[i+1] <= 0xBF and 0x80 <= data[i+2] <= 0xBF:
                valid_utf8_bytes += 3
                i += 3
            else:
                i += 1
        elif 0xF0 <= byte <= 0xF4:
            # 4字节 UTF-8
            if i + 3 < len(data) and all(0x80 <= data[j] <= 0xBF for j in range(i+1, i+3)):
                valid_utf8_bytes += 4
                i += 4
            else:
                i += 1
        else:
            i += 1

    return valid_utf8_bytes >= len(data) * threshold


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

    # 1️⃣ 遍历已知魔数签名
    for file_type, signature in FILE_SIGNATURES.items():
        if header.startswith(signature):
            return file_type

    # 2️⃣ 判断是否为文本内容
    if is_text_content(header):
        return 'text'

    # 3️⃣ 剩余的都算 binary
    return 'binary'


def read_text_from_file(file_path: str) -> str:
    """根据文件类型读取文本内容"""
    ftype = guess_file_type(file_path)
    # 这些类型可直接按 UTF-8 读取
    if ftype in ('text', 'html', 'xml', 'rtf'):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception:
            return ""
    # 对于其他格式（PDF, DOCX, ZIP等）暂不处理，返回空字符串
    return ""


def read_text_from_bytes(content: bytes, filename: str) -> str:
    """从字节流中读取文本"""
    # 先判断是否为文本
    if is_text_content(content):
        try:
            return content.decode('utf-8', errors='ignore')
        except Exception:
            return ""
    return ""


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