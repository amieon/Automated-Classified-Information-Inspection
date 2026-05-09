import os
import sys
import tempfile
from pathlib import Path
from typing import List
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker
from detector.leak_detector import LeakDetector
from utils.parallel import run_parallel
import threading

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
    for magic in AUDIO_MAGIC:
        if header.startswith(magic):
            return True
    if header[:4] == b'RIFF' and header[8:12] == b'WAVE':
        return True
    return False

# ==================== 语音识别（使用全局模型） ====================
def asr_audio(file_path: str) -> str:
    model = get_whisper_model()
    if model is None:
        print("⚠️ 未安装 openai-whisper，无法进行语音识别。")
        return ""
    try:
        result = model.transcribe(file_path, language="zh")
        return result["text"].strip()
    except Exception as e:
        print(f"⚠️ 语音识别失败: {e}")
        return ""

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
            p = Path(path)
            if not p.exists():
                return HTMLResponse(content="<div class='alert alert-danger'>路径不存在</div>")

            results = []
            if p.is_file():
                results.append(self._process_single_audio(str(p), detector))
            elif p.is_dir():
                for root, dirs, files in os.walk(p):
                    for file in files:
                        fp = Path(root) / file
                        results.append(self._process_single_audio(str(fp), detector))
            else:
                return HTMLResponse(content="<div class='alert alert-danger'>既不是文件也不是文件夹</div>")

            return self._build_html_result(results)

        # ------ 方式2：上传文件 （重写，避免流重复读取） ------
        @app.post("/check/audio/upload", response_class=HTMLResponse)
        async def check_audio_upload(
            files: List[UploadFile] = File(...),
            algorithm: str = Form("regex"),
            keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
            max_insert: int = Form(3)
        ):
            # 直接委托给内部统一处理函数，避免直接读取流
            return await self._handle_audio_upload(
                files, algorithm, keywords, max_insert
            )

    # ==================== 核心：统一的上传处理函数 ====================
    async def _handle_audio_upload(
        self,
        files: List[UploadFile],
        algorithm: str,
        keywords: str,
        max_insert: int
    ) -> HTMLResponse:
        # 第一步：一次性读取所有文件内容并缓存（与图像模块完全一致）
        filedata_list = []   # 元素: (filename, content_bytes)
        for file in files:
            content = await file.read()
            filedata_list.append((file.filename, content))

        # 第二步：分离有效音频和无效文件
        detector_kwargs = {
            "keywords": keywords,
            "algorithm": algorithm,
            "max_insert": max_insert
        }
        valid_tasks = []          # 元素: (filename, content, index)
        error_results = []        # 元素: dict (内容为空或非音频)
        for idx, (filename, content) in enumerate(filedata_list):
            if not content:
                error_results.append({
                    'path': filename,
                    'leak_lines': [],
                    'file_type': 'empty',
                    'note': '文件为空'
                })
                continue
            if not is_audio_bytes(content):
                error_results.append({
                    'path': filename,
                    'leak_lines': [],
                    'file_type': 'unknown',
                    'note': '不是音频文件'
                })
                continue
            valid_tasks.append((filename, content, idx))

        # 第三步：并行处理有效音频
        tmp_results = []
        if valid_tasks:
            # run_parallel 是线程池，每个 worker 接收 (filename, content, idx, detector_kwargs)
            processed = run_parallel(
                process_func=self._process_audio_bytes,
                items=valid_tasks,
                max_workers=2,   # 音频识别较重，不要开太高
                executor_type="thread",
                collect_results=True
            )
            # 按原始索引排序
            processed.sort(key=lambda r: r.pop('_index', 0))
            tmp_results = processed

        # 第四步：合并所有结果（错误文件 + 成功处理的）
        all_results = []   # 最终按上传顺序排列
        error_idx = 0
        valid_idx = 0
        # 由于 error_results 是按原始索引顺序添加的，但 valid_tasks 也是顺序的，
        # 我们可以按 filedata_list 索引重新组装
        for file_index in range(len(filedata_list)):
            # 先检查 error_results 中是否有对应的（它们也按顺序，但可能有跳过的）
            # 更清晰的方法：建立一个映射 dict -> result
            pass  # 稍后用合并数组

        # 简洁合并方案：建立一个 size 与 filedata_list 相同的列表，填充结果
        final_results = [None] * len(filedata_list)
        # 先填入错误结果（它们已经带有文件名，可直接按顺序插空）
        # error_results 是与 filedata_list 顺序一致的（因为遍历时按顺序追加的）
        error_ptr = 0
        for i in range(len(filedata_list)):
            filename, _ = filedata_list[i]
            if error_ptr < len(error_results) and error_results[error_ptr]['path'] == filename:
                final_results[i] = error_results[error_ptr]
                error_ptr += 1
            else:
                # 应该在 tmp_results 中
                pass
        for res in tmp_results:
            # res 中包含 '_index' 字段，我们已经 pop 并排序，现在需要按原位置插入
            pass  # 上面设计有问题，重新构思

        # 更简单的做法：不对 error_results 单独处理，而是在 valid_tasks 中保留索引，
        # 最后生成一个总 result 列表，初始全部为 None，然后填充。
        # 让我们重构合并部分……
        return await self._build_audio_upload_result(
            filedata_list, valid_tasks, error_results, tmp_results
        )

    # ------------- 辅助方法：处理单个音频字节流 -------------
    @staticmethod
    def _process_audio_bytes(item) -> dict:
        """
        在子线程中执行：保存临时文件 -> 语音识别 -> 敏感词检测
        参数 item: (filename, content_bytes, index, detector_kwargs)
        返回: 结果字典，包含原始 index
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

    # ==================== 路径模式下的单文件处理 ====================
    def _process_single_audio(self, file_path: str, detector: LeakDetector) -> dict:
        if not is_audio_file(file_path):
            return {
                'path': file_path,
                'leak_lines': [],
                'file_type': 'unknown',
                'note': '不是音频文件'
            }

        text = asr_audio(file_path)
        if not text:
            return {
                'path': file_path,
                'leak_lines': [],
                'file_type': 'audio',
                'note': '音频中未识别出语音'
            }

        leak_lines = detector.check_text(text)
        return {
            'path': file_path,
            'leak_lines': leak_lines,
            'file_type': 'audio',
            'note': ''
        }

    # ==================== 结果构建 ====================
    async def _build_audio_upload_result(self, filedata_list, valid_tasks, error_results, tmp_results):
        """将错误结果和并行处理结果按文件顺序合并，并生成HTML"""
        results = [None] * len(filedata_list)

        # 填入错误结果（空文件、非音频）
        # error_results 已经按 filedata_list 顺序存放，可直接对应
        error_ptr = 0
        for i, (filename, _) in enumerate(filedata_list):
            if error_ptr < len(error_results) and error_results[error_ptr]['path'] == filename:
                results[i] = error_results[error_ptr]
                error_ptr += 1

        # 填入有效结果
        for res in tmp_results:
            idx = res.pop('_index')
            results[idx] = res

        # 确保没有漏项
        results = [r for r in results if r is not None]

        # 生成纯文本报告
        text_report = self._generate_text_report(results, mode="音频上传检查")
        main_mod = sys.modules.get('__main__')
        if main_mod:
            main_mod.LATEST_REPORT = text_report

        return HTMLResponse(content=self._build_html_result(results))

    @staticmethod
    def _generate_text_report(results: list, mode: str = "") -> str:
        # ... 保持原实现不变 ...
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
        # ... 保持原实现不变，标题已是“音频检查结果” ...
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

        # 弹窗所需CSS和JS
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