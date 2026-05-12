import os
import tempfile
from pathlib import Path
from typing import List
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker
from detector.leak_detector import LeakDetector
from utils.parallel import run_parallel
import threading
from utils.cache_manager import DetectionCache
from utils.report_exporter import publish_latest_report

# ==================== 全局 Whisper 模型单例（线程安全） ====================
_WHISPER_MODEL = None
_model_lock = threading.Lock()

def get_whisper_model():
    """获取已加载的 Whisper 模型（base），保证只加载一次"""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        with _model_lock:
            if _WHISPER_MODEL is None:
                try:
                    import whisper
                    _WHISPER_MODEL = whisper.load_model("base")
                except ImportError:
                    return None
    return _WHISPER_MODEL

# ==================== 音频文件魔数 ====================
AUDIO_MAGIC = {
    b'\x49\x44\x33': 'mp3',
    b'\xff\xfb': 'mp3',
    b'RIFF': 'wav',
    b'fLaC': 'flac',
    b'OggS': 'ogg',
    b'\x00\x00\x00\x18ftyp': 'm4a',
    b'\x00\x00\x00\x14ftyp': 'm4a',
    b'\x00\x00\x00\x1cftyp': 'm4a',
}

def is_audio_file(file_path: str) -> bool:
    try:
        with open(file_path, 'rb') as f:
            header = f.read(20)
        for magic in AUDIO_MAGIC:
            if header.startswith(magic):
                return True
        if header[:4] == b'RIFF' and header[8:12] == b'WAVE':
            return True
    except:
        pass
    return False


def is_audio_bytes(data: bytes) -> bool:
    if not data:
        return False
    header = data[:20]

    # 原有魔数检查
    for magic in AUDIO_MAGIC:
        if header.startswith(magic):
            return True
    if header[:4] == b'RIFF' and len(header) >= 12 and header[8:12] == b'WAVE':
        return True

    # 新增：通用 MP4/m4a 检测（前12字节）
    if len(header) >= 12:
        # 寻找 'ftyp' 标识
        # MP4 格式：开头4字节是 box size，接着4字节是 'ftyp'，再接着是品牌
        if header[4:8] == b'ftyp':
            brand = header[8:12]
            # 常见音频品牌：M4A, M4B, mp42, isom, 3gp 等
            if brand in (b'M4A ', b'M4B ', b'mp42', b'isom', b'3gp5', b'3gp6'):
                return True
    return False

# ==================== 语音识别（使用全局模型） ====================
import threading
from opencc import OpenCC

_CC = None
_cc_lock = threading.Lock()


def get_opencc():
    global _CC
    if _CC is None:
        with _cc_lock:
            if _CC is None:
                try:
                    _CC = OpenCC('t2s')
                except ImportError:
                    return None
    return _CC


def asr_audio(file_path: str) -> str:
    model = get_whisper_model()
    if model is None:
        print("⚠️ 未安装 openai-whisper，无法进行语音识别。")
        return ""
    try:
        result = model.transcribe(
            file_path,
            language="zh",
            initial_prompt="以下是简体中文普通话的转录结果。请使用简体中文输出。"
        )
        text = result["text"].strip()

        # 兜底：如果仍有繁体，用 opencc 转换
        cc = get_opencc()
        if cc:
            text = cc.convert(text)

        return text
    except Exception as e:
        print(f"⚠️ 语音识别失败: {e}")
        return ""

# ==================== 纯检测函数（无缓存，供子进程调用） ====================
def _detect_audio_bytes(item) -> dict:
    """
    在子进程中执行：保存临时文件 -> 语音识别 -> 敏感词检测
    参数: (filename, content_bytes, index, detector_kwargs)
    返回: dict 包含 index，用于主进程后处理
    """
    filename, content, index, detector_kwargs = item
    detector = LeakDetector(**detector_kwargs)

    # 保存临时文件
    suffix = Path(filename).suffix or '.wav'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        text = asr_audio(tmp_path)
    except Exception:
        text = ""
    finally:
        os.unlink(tmp_path)

    if not text:
        return {
            'path': filename,
            'leak_lines': [],
            'file_type': 'audio',
            'note': '语音识别未能提取文字',
            '_index': index
        }

    leak_lines = detector.check_text(text)
    return {
        'path': filename,
        'leak_lines': leak_lines,
        'file_type': 'audio',
        'note': '',
        '_index': index
    }

# ==================== FastAPI 路由注册 ====================
class AudioCheckerModule(BaseChecker):
    def register_routes(self, app: FastAPI):
        # ------ 方式1：输入路径 ------
        @app.post("/check/audio/path", response_class=HTMLResponse)
        async def check_audio_path(
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
            # 为本次请求创建独立缓存，避免配置混乱
            cache = DetectionCache()
            cache.config_fingerprint(keywords=keywords, algorithm=algorithm, max_insert=max_insert)

            p = Path(path)
            if not p.exists():
                return HTMLResponse(content="<div class='alert alert-danger'>路径不存在</div>")

            results = []
            if p.is_file():
                results.append(self._process_single_audio(str(p), detector, cache))
            elif p.is_dir():
                for root, dirs, files in os.walk(p):
                    for file in files:
                        fp = Path(root) / file
                        results.append(self._process_single_audio(str(fp), detector, cache))
            else:
                return HTMLResponse(content="<div class='alert alert-danger'>既不是文件也不是文件夹</div>")

            text_report = self._generate_text_report(results, mode="音频路径检查")
            publish_latest_report(text_report)
            return self._build_html_result(results)

        # ------ 方式2：上传文件 ------
        @app.post("/check/audio/upload", response_class=HTMLResponse)
        async def check_audio_upload(
            files: List[UploadFile] = File(...),
            algorithm: str = Form("regex"),
            keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
            max_insert: int = Form(3)
        ):
            return await self._handle_audio_upload(
                files, algorithm, keywords, max_insert
            )

    # ==================== 上传处理主流程 ====================
    async def _handle_audio_upload(
        self,
        files: List[UploadFile],
        algorithm: str,
        keywords: str,
        max_insert: int
    ) -> HTMLResponse:
        # 1. 一次性读取所有文件内容
        filedata_list = []   # (filename, content_bytes)
        for file in files:
            content = await file.read()
            filedata_list.append((file.filename, content))

        # 2. 创建本次请求的缓存实例，配置指纹
        cache = DetectionCache()
        cache.config_fingerprint(keywords=keywords, algorithm=algorithm, max_insert=max_insert)

        detector_kwargs = {
            "keywords": keywords,
            "algorithm": algorithm,
            "max_insert": max_insert
        }

        # 3. 分离有效音频、错误文件，并建立索引映射
        error_results = []          # 含 _index
        valid_items = []            # (filename, content, index)
        for idx, (filename, content) in enumerate(filedata_list):
            if not content:
                error_results.append({
                    '_index': idx,
                    'path': filename,
                    'leak_lines': [],
                    'file_type': 'empty',
                    'note': '文件为空'
                })
                continue
            if not is_audio_bytes(content):
                error_results.append({
                    '_index': idx,
                    'path': filename,
                    'leak_lines': [],
                    'file_type': 'unknown',
                    'note': '不是音频文件'
                })
                continue
            valid_items.append((filename, content, idx))

        # 4. 缓存检查，拆分命中与未命中的任务
        cached_results = []
        need_process_items = []   # (filename, content, idx)
        # 为了后续缓存写入，建立 idx -> content 的快速查找
        idx_content_map = {}
        for filename, content, idx in valid_items:
            idx_content_map[idx] = content
            cached = cache.get_audio(content)
            if cached is not None:
                # cached 应包含 'leak_lines', 'file_type', 'note' 等字段
                res = {
                    '_index': idx,
                    'path': filename,
                    'leak_lines': cached.get('leak_lines', []),
                    'file_type': cached.get('file_type', 'audio'),
                    'note': cached.get('note', '')
                }
                cached_results.append(res)
            else:
                need_process_items.append((filename, content, idx))

        # 5. 并行处理未命中缓存的任务（如果需要）
        processed_results = []
        if need_process_items:
            items = [(filename, content, idx, detector_kwargs)
                     for filename, content, idx in need_process_items]
            processed_results = run_parallel(
                process_func=_detect_audio_bytes,
                items=items,
                max_workers=2,
                executor_type="process",
                collect_results=True
            )
            # 写入缓存
            for res in processed_results:
                idx = res['_index']
                content = idx_content_map.get(idx)
                if content is not None:
                    # 缓存核心检测结果（不含路径等外部信息）
                    cache.set_audio(content, {
                        'leak_lines': res.get('leak_lines', []),
                        'file_type': res.get('file_type', 'audio'),
                        'note': res.get('note', '')
                    })

        # 6. 合并所有结果（带 _index）
        all_results = error_results + cached_results + processed_results
        all_results.sort(key=lambda r: r['_index'])

        # 7. 清除 _index 字段，生成最终结果列表
        final_results = []
        for r in all_results:
            r.pop('_index', None)
            final_results.append(r)

        # 8. 生成纯文本报告 & HTML
        text_report = self._generate_text_report(final_results, mode="音频上传检查")
        publish_latest_report(text_report)

        return HTMLResponse(content=self._build_html_result(final_results))

    # ==================== 路径模式单文件处理 ====================
    def _process_single_audio(self, file_path: str, detector: LeakDetector, cache: DetectionCache) -> dict:
        # 读取文件内容
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
        except (FileNotFoundError, PermissionError):
            return {
                'path': file_path,
                'leak_lines': [],
                'file_type': 'error',
                'note': '文件无法读取'
            }

        # 非音频文件提前返回（可选，保持兼容）
        if not is_audio_bytes(content):
            return {
                'path': file_path,
                'leak_lines': [],
                'file_type': 'unknown',
                'note': '不是音频文件'
            }

        # 尝试从缓存获取
        cached = cache.get_audio(content)
        if cached is not None:
            return {
                'path': file_path,
                'leak_lines': cached.get('leak_lines', []),
                'file_type': cached.get('file_type', 'audio'),
                'note': cached.get('note', '')
            }

        # 未命中，执行 ASR + 检测
        text = asr_audio(file_path)
        if not text:
            # 无语音识别结果，也要缓存这个状态，避免重复 ASR
            core_result = {
                'leak_lines': [],
                'file_type': 'audio',
                'note': '音频中未识别出语音'
            }
            cache.set_audio(content, core_result)
            return {
                'path': file_path,
                'leak_lines': [],
                'file_type': 'audio',
                'note': '音频中未识别出语音'
            }

        leak_lines = detector.check_text(text)
        core_result = {
            'leak_lines': leak_lines,
            'file_type': 'audio',
            'note': ''
        }
        cache.set_audio(content, core_result)
        return {
            'path': file_path,
            'leak_lines': leak_lines,
            'file_type': 'audio',
            'note': ''
        }

    # ==================== 报告生成函数（保持原有实现） ====================
    @staticmethod
    def _generate_text_report(results: list, mode: str = "") -> str:
        from datetime import datetime
        lines = []
        lines.append("=" * 60)
        lines.append("          音频涉密数据检查报告")
        lines.append("=" * 60)
        lines.append(f"检查模式: {mode}")
        lines.append(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        total = len(results)
        leak_count = sum(1 for r in results if r['leak_lines'])
        lines.append(f"扫描音频数: {total}")
        lines.append(f"发现涉密音频数: {leak_count}")
        lines.append("-" * 60)
        for i, r in enumerate(results, 1):
            path = r.get('path', '未知路径')
            leak_lines = r.get('leak_lines', [])
            lines.append(f"\n【音频 {i}】{path}")
            if leak_lines:
                lines.append(f"  涉密信息 ({len(leak_lines)} 处):")
                for start_time, keyword, content in leak_lines:
                    lines.append(f"    时间 {start_time}s | 关键词 [{keyword}] → {content}")
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
        <h3>✅ 音频检查结果</h3>
        <p>共检查 <strong>{len(results)}</strong> 个音频文件，发现 <strong>{total_leak}</strong> 个含涉密语音</p>
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
                        <p><strong>文件类型：</strong>音频</p>
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
                <td>音频</td>
                <td>{lines_str}</td>
                <td>{btn}{modal}</td>
                <td>{note}</td>
            </tr>
            """

        html += "</tbody></table>"

        # 弹窗样式与脚本
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
