from pathlib import Path
from typing import List
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker
from detector.leak_detector import LeakDetector
from PIL import Image
import io
import pytesseract
import sys
# 自动检测并设置 tesseract 路径
if sys.platform == 'win32':
    possible_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in possible_paths:
        import os
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break

# ==================== 图片魔数（文件头） ====================
IMAGE_HEADERS = {
    b'\x89PNG\r\n\x1a\n': 'png',
    b'\xff\xd8\xff':      'jpg/jpeg',
    b'GIF8':              'gif',
    b'BM':                'bmp',
    b'RIFF':              'webp',   # WebP 以 RIFF 开头
}

def is_image_file(file_path: str) -> bool:
    """通过文件头判断是否为图片"""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(16)   # 读取足够字节用于判断
        for magic, img_type in IMAGE_HEADERS.items():
            if header.startswith(magic):
                return True
        # 对JPEG的补充：JPEG可以以0xFFD8FF开头（已含）或0xFFD8FFE0开头等
        # 我们已包括'b\xff\xd8\xff'，但有时JPEG的APP0标记可能是\xff\xd8\xff\xe0
        # 所以更精确的判断：检查前两个字节是否为0xFFD8
        if len(header) >= 2 and header[0] == 0xff and header[1] == 0xd8:
            return True
        return False
    except Exception:
        return False

def ocr_image(file_path: str) -> str:
    """OCR提取图片中的文字，返回字符串"""
    try:
        img = Image.open(file_path)
        # 使用中文+英文语言包，若只需中文可改为 'chi_sim'
        text = pytesseract.image_to_string(img, lang='chi_sim+eng')
        return text.strip()
    except Exception as e:
        return ""   # OCR失败返回空字符串

# ==================== FastAPI 路由注册 ====================
class ImageCheckerModule(BaseChecker):
    def register_routes(self, app: FastAPI):
        # ------ 方式1：输入路径 ------
        @app.post("/check/image/path", response_class=HTMLResponse)
        async def check_image_path(
            path: str = Form(...),
            algorithm: str = Form("regex"),
            keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
            max_insert: int = Form(3)
        ):
            detector = LeakDetector(
                keywords=keywords,
                algorithm=algorithm,
                max_insert=max_insert
            )
            p = Path(path)
            if not p.exists():
                return HTMLResponse(content="<div class='alert alert-danger'>路径不存在</div>")

            results = []
            if p.is_file():
                res = self._process_single_image(str(p), detector)
                results.append(res)
            elif p.is_dir():
                for root, dirs, files in os.walk(p):
                    for file in files:
                        fp = Path(root) / file
                        res = self._process_single_image(str(fp), detector)
                        results.append(res)
            else:
                return HTMLResponse(content="<div class='alert alert-danger'>既不是文件也不是文件夹</div>")

            return self._build_html_result(results)

        # ------ 方式2：上传文件 ------
        @app.post("/check/image/upload", response_class=HTMLResponse)
        async def check_image_upload(files: List[UploadFile] = File(...)):
            detector = LeakDetector()
            results = []
            for file in files:
                # 将上传的文件保存到临时文件才能用PIL读取（或者直接从BytesIO读取）
                # 为了利用 is_image_file（需要文件路径），我们可以先保存到临时文件
                # 但更好的方式是直接从字节流判断文件头（不用临时文件）
                content = await file.read()
                if not content:
                    results.append({
                        'path': file.filename,
                        'leak_lines': [],
                        'file_type': 'empty',
                        'note': '文件为空'
                    })
                    continue

                # 判断是否为图片（基于字节头）
                if not self._is_image_bytes(content):
                    results.append({
                        'path': file.filename,
                        'leak_lines': [],
                        'file_type': 'unknown',
                        'note': '不是图片文件'
                    })
                    continue

                # 从字节流进行OCR（使用 PIL 的 BytesIO）
                try:
                    img = Image.open(io.BytesIO(content))
                    text = pytesseract.image_to_string(img, lang='chi_sim+eng').strip()
                except Exception as e:
                    text = ""

                leak_lines = detector.check_text(text) if text else []
                results.append({
                    'path': file.filename,
                    'leak_lines': leak_lines,
                    'file_type': 'image',
                    'note': '' if text else 'OCR未能提取文字'
                })
            #报告生成并写入全局变量
            text_report = self._generate_text_report(results, mode="图片上传检查")
            main_mod = sys.modules.get('__main__')
            if main_mod:
                main_mod.LATEST_REPORT = text_report
            return self._build_html_result(results)

    # -------------------- 内部方法 --------------------
    def _process_single_image(self, file_path: str, detector: LeakDetector) -> dict:
        """处理单个图片文件，返回结果字典"""
        # 1. 判断是否为图片
        if not is_image_file(file_path):
            return {
                'path': file_path,
                'leak_lines': [],
                'file_type': 'unknown',
                'note': '不是图片文件'
            }
        img_format = self._get_image_format(file_path)
        # 2. OCR
        text = ocr_image(file_path)
        if not text:
            return {
                'path': file_path,
                'leak_lines': [],
                'file_type': img_format,
                'note': '图片中未检测到文字'
            }

        # 3. 敏感词检测
        leak_lines = detector.check_text(text)
        return {
            'path': file_path,
            'leak_lines': leak_lines,
            'file_type': img_format,
            'note': ''
        }

    @staticmethod
    def _get_image_format(file_path: str) -> str:
        with open(file_path, 'rb') as f:
            header = f.read(16)
        for magic, fmt in IMAGE_HEADERS.items():
            if header.startswith(magic):
                return fmt
        # 补充JPEG判断（前两个字节0xFFD8）
        if len(header) >= 2 and header[0] == 0xff and header[1] == 0xd8:
            return 'jpeg'
        return 'unknown'

    @staticmethod
    def _is_image_bytes(data: bytes) -> bool:
        """根据字节数据判断是否为图片"""
        if not data:
            return False
        header = data[:16]
        for magic, img_type in IMAGE_HEADERS.items():
            if header.startswith(magic):
                return True
        # JPEG补充判断（前两个字节0xFFD8）
        if len(header) >= 2 and header[0] == 0xff and header[1] == 0xd8:
            return True
        return False
    @staticmethod
    def _generate_text_report(results: list, mode: str = "") -> str:
        from datetime import datetime
        from collections import Counter
        total_leak_lines = sum(len(r['leak_lines']) for r in results)
        type_counter = Counter(r.get('file_type', 'unknown') for r in results)
        type_str = "\n" + "\n".join([f"   {fmt}: {cnt}" for fmt, cnt in type_counter.items()])
        lines = []
        lines.append("=" * 60)
        lines.append("          图片涉密数据检查报告")
        lines.append("=" * 60)
        lines.append(f"检查模式: {mode}")
        lines.append(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        total_images = len(results)
        leak_images = sum(1 for r in results if r['leak_lines'])
        lines.append(f"扫描图片数: {total_images}")
        lines.append(f"图片类型分布: {type_str}")
        lines.append(f"发现涉密图片数: {leak_images}")
        lines.append(f"涉密数据总行数: {total_leak_lines}")
        lines.append("-" * 60)
        for i, r in enumerate(results, 1):
            path = r.get('path', '未知路径')
            leak_lines = r.get('leak_lines', [])
            lines.append(f"\n【图片 {i}】{path}")
            if leak_lines:
                lines.append(f"  涉密信息 ({len(leak_lines)} 处):")
                for line_no, keyword, content in leak_lines:
                    lines.append(f"    区域 {line_no} | 关键词 [{keyword}] → {content}")
            else:
                lines.append("  未发现涉密数据。")
        lines.append("=" * 60)
        lines.append("报告结束")
        return "\n".join(lines)
    @staticmethod
    def _build_html_result(results: list) -> str:
        """生成结果HTML表格（与file_checker中的完全一致，此处复制以避免依赖）"""
        import html as html_mod
        total_leak = sum(1 for r in results if r['leak_lines'])
        html = f"""
        <h3>✅ 图片检查结果</h3>
        <p>共检查 <strong>{len(results)}</strong> 张图片，发现 <strong>{total_leak}</strong> 张含涉密文字</p>
        <table class="table table-bordered">
            <thead>
                <tr><th>文件路径</th><th>类型</th><th>涉密行数</th><th>操作</th><th>备注</th></tr>
            </thead>
            <tbody>
        """

        def format_leak_lines(leak_lines: list) -> str:
            """格式化涉密行详情"""
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
                        <span class="my-modal-title">{html_mod.escape(r['path'])}</span>
                        <span class="my-modal-close" onclick="closeModal('modal_{i}')">&times;</span>
                    </div>
                    <div class="my-modal-body">
                        <p><strong>文件路径：</strong>{html_mod.escape(r['path'])}</p>
                        <p><strong>文件类型：</strong>图片</p>
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

            html += f"""
            <tr>
                <td>{r['path']}</td>
                <td>图片</td>
                <td>{lines_str}</td>
                <td>{btn}{modal}</td>
                <td>{note}</td>
            </tr>
            """

        html += "</tbody></table>"

        # 弹窗所需CSS和JS（仅注入一次）
        html += """
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
        return html