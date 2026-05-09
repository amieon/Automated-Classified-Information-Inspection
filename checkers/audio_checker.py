import os
import io
import sys
import tempfile
from pathlib import Path
from typing import List
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker
from utils.leak_detector import LeakDetector

# ==================== 音频文件魔数 ====================
AUDIO_MAGIC = {
    b'\x49\x44\x33': 'mp3',           # ID3 tag 开头
    b'\xff\xfb': 'mp3',               # MPEG 音频同步头（部分 MP3）
    b'RIFF': 'wav',                   # WAV（还需检查第8字节是否为 WAVE，但简单判断足够）
    b'fLaC': 'flac',                  # FLAC
    b'OggS': 'ogg',                   # OGG / Opus
    b'\x00\x00\x00\x18ftyp': 'm4a',   # MP4 容器中的 AAC（简化处理）
    b'\x00\x00\x00\x14ftyp': 'm4a',   # 另一种 ftyp
    b'\x00\x00\x00\x1cftyp': 'm4a',
}

def is_audio_file(file_path: str) -> bool:
    """通过文件头判断是否为音频文件"""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(20)  # 读取足够字节
        for magic, audio_type in AUDIO_MAGIC.items():
            if header.startswith(magic):
                return True
        # 补充：WAV 的特殊判断（RIFF + WAVE）
        if header[:4] == b'RIFF' and header[8:12] == b'WAVE':
            return True
        return False
    except Exception:
        return False

def is_audio_bytes(data: bytes) -> bool:
    """根据字节数据判断是否为音频"""
    if not data:
        return False
    header = data[:20]
    for magic, _ in AUDIO_MAGIC.items():
        if header.startswith(magic):
            return True
    if header[:4] == b'RIFF' and header[8:12] == b'WAVE':
        return True
    return False

# ==================== 语音识别 ====================
def asr_audio(file_path: str) -> str:
    """
    使用 Whisper 进行离线语音识别
    返回识别出的文本字符串
    """
    try:
        import whisper
        # 使用 base 模型（平衡速度与准确率），可改为 'small' 或 'tiny'
        model = whisper.load_model("base")
        result = model.transcribe(file_path, language="zh")  # 指定中文，也可自动检测
        return result["text"].strip()
    except ImportError:
        # Whisper 未安装，回退到备选方案（如果有）
        # 这里选择返回空字符串并打印提示
        print("⚠️ 未安装 openai-whisper，无法进行语音识别。")
        return ""
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
                res = self._process_single_audio(str(p), detector)
                results.append(res)
            elif p.is_dir():
                for root, dirs, files in os.walk(p):
                    for file in files:
                        fp = Path(root) / file
                        res = self._process_single_audio(str(fp), detector)
                        results.append(res)
            else:
                return HTMLResponse(content="<div class='alert alert-danger'>既不是文件也不是文件夹</div>")

            return self._build_html_result(results)

        # ------ 方式2：上传文件 ------
        @app.post("/check/audio/upload", response_class=HTMLResponse)
        async def check_audio_upload(files: List[UploadFile] = File(...)):
            detector = LeakDetector()
            results = []
            for file in files:
                content = await file.read()
                if not content:
                    results.append({
                        'path': file.filename,
                        'leak_lines': [],
                        'file_type': 'empty',
                        'note': '文件为空'
                    })
                    continue

                # 判断是否为音频
                if not is_audio_bytes(content):
                    results.append({
                        'path': file.filename,
                        'leak_lines': [],
                        'file_type': 'unknown',
                        'note': '不是音频文件'
                    })
                    continue

                # 保存到临时文件供 Whisper 使用
                suffix = Path(file.filename).suffix or '.wav'
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    text = asr_audio(tmp_path)
                except Exception as e:
                    text = ""
                finally:
                    os.unlink(tmp_path)  # 清理临时文件

                leak_lines = detector.check_text(text) if text else []
                results.append({
                    'path': file.filename,
                    'leak_lines': leak_lines,
                    'file_type': 'audio',
                    'note': '' if text else '语音识别未能提取文字'
                })
            text_report = self._generate_text_report(results, mode="音频上传检查")
            main_mod = sys.modules.get('__main__')
            if main_mod:
                main_mod.LATEST_REPORT = text_report

            return self._build_html_result(results)



    # -------------------- 内部方法 --------------------
    def _process_single_audio(self, file_path: str, detector: LeakDetector) -> dict:
        """处理单个音频文件，返回结果字典"""
        if not is_audio_file(file_path):
            return {
                'path': file_path,
                'leak_lines': [],
                'file_type': 'unknown',
                'note': '不是音频文件'
            }

        # 语音识别
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
    # ==================== ★ 新增：纯文本报告生成 ====================
    @staticmethod
    def _generate_text_report(results: list, mode: str = "") -> str:
        from datetime import datetime
        lines = []
        lines.append("=" * 60)
        lines.append("          音频涉密数据检查报告")
        lines.append("=" * 60)
        lines.append(f"检查模式: {mode}")
        lines.append(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        total_audios = len(results)
        leak_audios = sum(1 for r in results if r['leak_lines'])
        lines.append(f"扫描音频数: {total_audios}")
        lines.append(f"发现涉密音频数: {leak_audios}")
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
        """生成结果HTML表格（与 image_checker 一致，仅修改标题为 '音频检查结果'）"""
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