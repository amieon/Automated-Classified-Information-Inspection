from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse

from utils.cache_manager import get_cache
from .base_checker import BaseChecker
from detector.leak_detector import LeakDetector
from PIL import Image
import io
import pytesseract
import sys
import os

# 通用并行执行器（需提前创建 utils/parallel.py 并放入 run_parallel 函数）
from utils.parallel import run_parallel

# 自动检测并设置 tesseract 路径
if sys.platform == 'win32':
    possible_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in possible_paths:
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break

# ==================== 图片魔数（文件头） ====================
IMAGE_HEADERS = {
    b'\x89PNG\r\n\x1a\n': 'png',
    b'\xff\xd8\xff':      'jpg/jpeg',
    b'GIF8':              'gif',
    b'BM':                'bmp',
    b'RIFF':              'webp',
}

def is_image_file(file_path: str) -> bool:
    """通过文件头判断是否为图片"""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(16)
        for magic in IMAGE_HEADERS:
            if header.startswith(magic):
                return True
        if len(header) >= 2 and header[0] == 0xff and header[1] == 0xd8:
            return True
        return False
    except Exception:
        return False

def ocr_image(file_path: str) -> str:
    """OCR提取图片中的文字，返回字符串"""
    try:
        img = Image.open(file_path)
        text = pytesseract.image_to_string(img, lang='chi_sim+eng')
        return text.strip()
    except Exception:
        return ""

# ==================== 并行任务函数（供 run_parallel 调用） ====================

def _process_image_path(args: tuple) -> Optional[dict]:
    file_path, detector_kwargs = args
    cache = get_cache()
    # 设置配置指纹（与主进程保持一致）
    cache.config_fingerprint(
        keywords=detector_kwargs.get("keywords", ""),
        algorithm=detector_kwargs.get("algorithm", "regex"),
        max_insert=detector_kwargs.get("max_insert", 3)
    )

    if not is_image_file(file_path):
        return {
            'path': file_path,
            'leak_lines': [],
            'file_type': 'unknown',
            'note': '不是图片文件'
        }

    # 读取完整字节内容用于缓存
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
    except Exception:
        return {
            'path': file_path,
            'leak_lines': [],
            'file_type': 'unknown',
            'note': '读取文件失败'
        }

    # --- 缓存尝试 ---
    cached = cache.get_image(content)
    if cached is not None:
        # 缓存命中，直接返回存储的结果（需补上 path 字段）
        result = cached.copy()
        result['path'] = file_path
        return result
    # -----------------

    # 未命中，执行原本的 OCR+检测
    img_format = 'unknown'
    try:
        header = content[:16]
        for magic, fmt in IMAGE_HEADERS.items():
            if header.startswith(magic):
                img_format = fmt
                break
        if img_format == 'unknown' and len(header) >= 2 and header[0] == 0xff and header[1] == 0xd8:
            img_format = 'jpeg'
    except Exception:
        img_format = 'unknown'

    text = ocr_image(file_path)          # 注意：ocr_image 内部会再次打开文件，效率稍低，可优化
    if not text:
        result = {
            'path': file_path,
            'leak_lines': [],
            'file_type': img_format,
            'note': '图片中未检测到文字'
        }
    else:
        detector = LeakDetector(**detector_kwargs)
        leak_lines = detector.check_text(text)
        result = {
            'path': file_path,
            'leak_lines': leak_lines,
            'file_type': img_format,
            'note': ''
        }

    # 写入缓存（不含 path，仅存可复用的检测结果）
    cache.set_image(content, {
        'leak_lines': result['leak_lines'],
        'file_type': result['file_type'],
        'note': result['note']
    })
    return result


def _process_image_bytes(args: tuple) -> Optional[dict]:
    filename, img_bytes, index, detector_kwargs = args
    cache = get_cache()
    cache.config_fingerprint(
        keywords=detector_kwargs.get("keywords", ""),
        algorithm=detector_kwargs.get("algorithm", "regex"),
        max_insert=detector_kwargs.get("max_insert", 3)
    )

    # --- 缓存尝试 ---
    cached = cache.get_image(img_bytes)
    if cached is not None:
        result = cached.copy()
        result.update({
            'path': filename,
            '_index': index
        })
        return result
    # -----------------

    # 未命中，执行 OCR+检测
    try:
        img = Image.open(io.BytesIO(img_bytes))
        text = pytesseract.image_to_string(img, lang='chi_sim+eng').strip()
    except Exception:
        text = ""

    detector = LeakDetector(**detector_kwargs)
    leak_lines = detector.check_text(text) if text else []

    result = {
        'path': filename,
        'leak_lines': leak_lines,
        'file_type': 'image',
        'note': '' if text else 'OCR未能提取文字',
        '_index': index
    }

    # 写入缓存（只存可复用部分）
    cache.set_image(img_bytes, {
        'leak_lines': leak_lines,
        'file_type': 'image',
        'note': '' if text else 'OCR未能提取文字'
    })
    return result


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
            detector_kwargs = {
                "keywords": keywords,
                "algorithm": algorithm,
                "max_insert": max_insert
            }
            cache = get_cache()
            cache.config_fingerprint(keywords=keywords, algorithm=algorithm, max_insert=max_insert)
            p = Path(path)
            if not p.exists():
                return HTMLResponse(content="<div class='alert alert-danger'>路径不存在</div>")

            results = []
            if p.is_file():
                # 单文件直接处理
                res = _process_image_path((str(p), detector_kwargs))
                if res:
                    results.append(res)
            elif p.is_dir():
                # 收集所有文件路径
                file_list = []
                for root, dirs, files in os.walk(p):
                    for file in files:
                        file_list.append(str(Path(root) / file))
                # 并行处理
                if file_list:
                    # 每个任务参数为 (path, detector_kwargs)
                    tasks = [(fp, detector_kwargs) for fp in file_list]
                    tmp_results = run_parallel(
                        process_func=_process_image_path,
                        items=tasks,
                        max_workers=4,
                        executor_type="process",
                        description="扫描图片文件中"
                    )
                    # 按路径排序（可选）
                    tmp_results.sort(key=lambda r: r.get('path', ''))
                    results = tmp_results
            else:
                return HTMLResponse(content="<div class='alert alert-danger'>既不是文件也不是文件夹</div>")

            return self._build_html_result(results)

        # ------ 方式2：上传文件 ------
        @app.post("/check/image/upload", response_class=HTMLResponse)
        async def check_image_upload(
            files: List[UploadFile] = Form(...),
            algorithm: str = Form("regex"),
            keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
            max_insert: int = Form(3)
        ):
            return await self._handle_image_upload(files=files, algorithm=algorithm,keywords=keywords,max_insert=max_insert)
    async def _handle_image_upload(
            self,
            files: List[UploadFile] = Form(...),
            algorithm: str = Form("regex"),
            keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
            max_insert: int = Form(3)
        ):
        detector_kwargs = {
            "keywords": keywords,
            "algorithm": algorithm,
            "max_insert": max_insert
        }
        # 收集每个文件的数据：文件名、字节内容、原始索引
        filedata = []
        for idx, file in enumerate(files):
            content = await file.read()
            filedata.append((file.filename, content, idx))

        # 分拣：图片文件送并行，其他直接生成结果
        image_tasks = []
        results_map = {}   # 索引 -> 结果字典

        for filename, content, idx in filedata:
            if not content:
                results_map[idx] = {
                    'path': filename,
                    'leak_lines': [],
                    'file_type': 'empty',
                    'note': '文件为空'
                }
                continue
            if not self._is_image_bytes(content):
                results_map[idx] = {
                    'path': filename,
                    'leak_lines': [],
                    'file_type': 'unknown',
                    'note': '不是图片文件'
                }
                continue
            # 有效图片，加入并行任务
            image_tasks.append((filename, content, idx, detector_kwargs))

        # 并行处理图片
        if image_tasks:
            image_results = run_parallel(
                process_func=_process_image_bytes,
                items=image_tasks,
                max_workers=4,
                executor_type="process",
                description="扫描上传图片中"
            )
            for r in image_results:
                idx = r.pop('_index', 0)
                results_map[idx] = r

        # 按索引顺序重建最终结果列表
        results = [results_map[i] for i in sorted(results_map.keys())]

        # 生成报告并写入全局变量
        text_report = self._generate_text_report(results, mode="图片上传检查")
        main_mod = sys.modules.get('__main__')
        if main_mod:
            main_mod.LATEST_REPORT = text_report
        return self._build_html_result(results)

    # -------------------- 内部工具方法 --------------------
    @staticmethod
    def _is_image_bytes(data: bytes) -> bool:
        """根据字节数据判断是否为图片"""
        if not data:
            return False
        header = data[:16]
        for magic in IMAGE_HEADERS:
            if header.startswith(magic):
                return True
        if len(header) >= 2 and header[0] == 0xff and header[1] == 0xd8:
            return True
        return False

    @staticmethod
    def _generate_text_report(results: list, mode: str = "") -> str:
        from datetime import datetime
        from collections import Counter
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
        total_leak_lines = sum(len(r['leak_lines']) for r in results)
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
                // 按 ESC 键关闭弹窗
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