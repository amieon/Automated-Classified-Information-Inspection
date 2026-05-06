# checkers/file_checker.py
import os
import sys
from pathlib import Path
from typing import List
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse
from .base_checker import BaseChecker
from utils.leak_detector import LeakDetector
import zipfile
from io import BytesIO
from utils.office_parser import parse_xlsx, parse_docx, parse_pptx, parse_pdf
from utils.office_parser import parse_xls, parse_doc, parse_ppt

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
TEXT_EXTENSIONS = {
    'txt', 'md', 'py', 'java', 'js', 'ts', 'html', 'css', 'json',
    'xml', 'yml', 'yaml', 'ini', 'cfg', 'conf', 'csv', 'log', 'rtf',
    'bat', 'sh', 'ps1', 'sql', 'rb', 'go', 'rs', 'cpp', 'c', 'h',
    'hpp', 'php', 'pl', 'lua', 'dockerfile', 'gitignore', 'env',
    'toml', 'cfg', 'ini', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'pdf'  # 若想把pdf也算作可读取的文本？实际pdf不能当纯文本，当然read_text_from_bytes会处理
}
# Office 文件魔数（文件头字节）
OFFICE_MAGIC = {
    b'PK\x03\x04': ['docx', 'xlsx', 'pptx'],  # ZIP 格式
}
# Word 文档的命名空间
WORD_NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
# Excel 的命名空间
EXCEL_NS = {
    's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
# PPT 的命名空间
PPT_NS = {
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
}


def is_text_content(data: bytes, filename: str = '') -> bool:
    """
    判断文件是否为可读文本文件或 Office 文档
    """
    if not data:
        return False

    # 检查 Office 文件魔数
    if data[:4] == b'PK\x03\x04':
        # 检查是否是 Office 文件（通过解压检查内容结构）
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                names = z.namelist()
                # 检查是否有 Office 文件标记
                if any(name in names for name in ['word/document.xml',
                                                  'xl/workbook.xml',
                                                  'ppt/presentation.xml']):
                    return True
                # 如果只有几个文件且不含 Office 标记，可能只是普通 ZIP
                # 这里保守判断：包含上述标记才算 Office 文件
        except:
            pass

    # 原有的文本检测逻辑
    # 如果文件名有明确后缀
    if filename:
        ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
        if ext in TEXT_EXTENSIONS:
            return True

    # 尝试检测是否为纯文本
    try:
        # 检查是否包含空字节（文本文件通常不含）
        if b'\x00' in data[:1000]:
            return False

        # 检查可打印字符比例
        printable = sum(1 for b in data[:2000] if 32 <= b <= 126 or b in (9, 10, 13))
        if len(data[:2000]) == 0:
            return True
        ratio = printable / len(data[:2000])
        return ratio >= 0.7
    except:
        return False



def guess_file_type(data: bytes, filename: str = '', is_bytes: bool = False) -> str:
    """
    根据文件头猜测类型
    :param data: 文件路径（字符串）或字节数据
    :param filename: 文件名
    :param is_bytes: 如果为True，file_source视为字节数据
    :return: 类型字符串 ('text', 'pdf', 'zip', 'html', 'xml', 'binary'等)
    """
    if isinstance(data, str):
        # 字符串视为路径，读取二进制内容再判断
        try:
            with open(data, 'rb') as f:
                data = f.read(512)
            is_bytes = True
        except Exception:
            return 'unknown'

    # Office 文件识别
    if data[:4] == b'PK\x03\x04':
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                names = z.namelist()
                if 'word/document.xml' in names:
                    return 'docx'
                elif 'xl/workbook.xml' in names:
                    return 'xlsx'
                elif 'ppt/presentation.xml' in names:
                    return 'pptx'
        except:
            pass

    # 原有的文件类型判断
    if is_bytes:
        header = data[:512]
    else:
        with open(data, 'rb') as f:
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
    # （保留你已有的逻辑）

    if filename:
        ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
        ext_map = {
            # 原始数据
            'txt': 'text', 'md': 'markdown', 'py': 'python', 'java': 'java',
            'js': 'javascript', 'ts': 'typescript', 'html': 'html', 'css': 'css',
            'json': 'json', 'xml': 'xml', 'yml': 'yaml', 'yaml': 'yaml',
            'ini': 'ini', 'cfg': 'config', 'conf': 'config', 'csv': 'csv',
            'log': 'log', 'doc': 'doc', 'docx': 'docx', 'xls': 'xls',
            'xlsx': 'xlsx', 'ppt': 'ppt', 'pptx': 'pptx', 'pdf': 'pdf',

            # 新增/完善部分
            'rtf': 'text',  # 富文本，通常作为文本处理
            'bat': 'batch',  # Windows 批处理
            'sh': 'bash',  # Shell 脚本
            'ps1': 'powershell',  # PowerShell 脚本
            'sql': 'sql',  # 结构化查询语言
            'rb': 'ruby',  # Ruby
            'go': 'go',  # Go
            'rs': 'rust',  # Rust
            'cpp': 'cpp',  # C++
            'c': 'c',  # C
            'h': 'c',  # C 头文件 (通常复用 C 的高亮)
            'hpp': 'cpp',  # C++ 头文件 (通常复用 C++ 的高亮)
            'php': 'php',  # PHP
            'pl': 'perl',  # Perl
            'lua': 'lua',  # Lua
            'dockerfile': 'dockerfile',  # Dockerfile
            'gitignore': 'gitignore',  # Git Ignore
            'env': 'properties',  # 环境变量文件 (通常类似 ini/properties)
            'toml': 'toml',  # TOML
        }
        if ext in ext_map:
            return ext_map[ext]

    return 'binary'


def read_text_from_bytes(data: bytes, filename: str = '') -> str:
    """
    从字节数据中读取文本，自动检测文件类型
    """
    # 获取文件扩展名
    ext = os.path.splitext(filename)[1].lower() if filename else ''

    # --- 新格式 Office (OpenXML) ---
    if data[:4] == b'PK\x03\x04':
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                names = z.namelist()

                if 'word/document.xml' in names:
                    #print(f"  📖 检测为 docx 文件")
                    return parse_docx(data)
                elif 'xl/workbook.xml' in names:
                    #print(f"  📖 检测为 xlsx 文件")
                    return parse_xlsx(data)
                elif 'ppt/presentation.xml' in names:
                    #print(f"  📖 检测为 pptx 文件")
                    return parse_pptx(data)
        except Exception as e:
            print(f"  ⚠️ Office 文件解析失败: {e}")

    if len(data) >= 5 and data[:5] == b'%PDF-':
        #print(f"  📖 检测为 pdf 文件")
        return parse_pdf(data)

    # --- 旧格式 Office (OLE2) ---
    # OLE2 文件的魔数是前8字节: D0 CF 11 E0 A1 B1 1A E1
    if len(data) >= 8 and data[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        #print(f"  📖 检测为旧版 Office 文件（根据扩展名: {ext}）")

        if ext == '.doc':
            #print("  🔄 使用旧版 doc 解析器")
            return parse_doc(data)
        elif ext == '.xls':
            #print("  🔄 使用旧版 xls 解析器")
            return parse_xls(data)
        elif ext == '.ppt':
            #print("  🔄 使用旧版 ppt 解析器")
            return parse_ppt(data)
        else:
            # 没有扩展名时，尝试自动检测
            # 通过 OLE 中的流名称判断
            try:
                import olefile
                ole = olefile.OleFileIO(BytesIO(data))
                stream_names = ole.listdir()
                all_streams = [item for sublist in stream_names for item in sublist]
                ole.close()

                if 'WordDocument' in all_streams:
                    print("  🔄 根据流检测为 doc")
                    return parse_doc(data)
                elif 'Workbook' in all_streams or 'Book' in all_streams:
                    print("  🔄 根据流检测为 xls")
                    return parse_xls(data)
                elif 'PowerPoint Document' in all_streams:
                    print("  🔄 根据流检测为 ppt")
                    return parse_ppt(data)
            except ImportError:
                print("  ⚠️ 需要安装 olefile: pip install olefile")
                return ''
            except Exception as e:
                print(f"  ⚠️ OLE 检测失败: {e}")
                return ''

    # --- 纯文本文件 ---
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        try:
            return data.decode('gbk')
        except:
            return data.decode('utf-8', errors='ignore')


def read_text_from_file(file_path: str) -> str:
    """
    根据文件类型读取文本内容（统一读取字节后转文本）
    支持：普通文本、JSON、XML、HTML、DOCX、XLSX、PPTX、PDF（如果有库的话）
    """
    if not os.path.exists(file_path):
        return ""

    try:
        # 统一以二进制模式读取，交给 read_text_from_bytes 去判断类型
        with open(file_path, 'rb') as f:
            data = f.read()

        if not data:
            return ""

        filename = os.path.basename(file_path)
        return read_text_from_bytes(data, filename)

    except Exception as e:
        print(f"  ⚠️ 读取文件失败 {file_path}: {e}")
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

        @app.post("/check/file/upload", response_class=HTMLResponse)
        async def check_file_upload(files: List[UploadFile] = File(...)):
            detector = LeakDetector()
            results = []
            for file in files:
                content = await file.read()
                text = read_text_from_bytes(content, file.filename)
                # ========== 新增调试 ==========
                # print(f"\n=== 调试: {file.filename} ===")
                # print(f"  detector.keywords = {detector.keywords}")
                # print(f"  text 前200字符: {text[:200]!r}")
                # lines = text.split('\n')
                # print(f"  行数: {len(lines)}")
                # for i, line in enumerate(lines, 1):
                #     for kw in detector.keywords:
                #         found = kw in line
                #         if found:
                #             print(f"  行{i}: 找到关键词 '{kw}'")
                #             break
                #     else:
                #         if i <= 3:  # 只打印前3行
                #             print(f"  行{i} 无匹配, 内容片段: {line[:80]!r}")
                # ==============================

                leak_lines = detector.check_text(text)
                #print(f"  leak_lines 最终返回: {leak_lines}")
                results.append({
                    'path': file.filename,
                    'leak_lines': leak_lines,
                    'file_type': guess_file_type(content, is_bytes=True),
                    'note': '' if text else '无法读取文本内容',

                })
            # ★ 新增：生成文本报告并写入全局变量
            text_report = self._generate_text_report(results, source_type="路径")
            main_module = sys.modules.get('__main__')
            if main_module:
                main_module.LATEST_REPORT = text_report
            # =========================
            return self._build_html_result(results)


    # ==================== ★ 新增：生成纯文本报告 ====================
    @staticmethod
    def _generate_text_report(results: list, source_type: str = "") -> str:
        """
        根据检查结果生成纯文本报告（用于下载）
        """
        import datetime
        lines = []
        lines.append("=" * 60)
        lines.append("           文件涉密数据检查报告")
        lines.append("=" * 60)
        lines.append(f"检查方式: {source_type}")
        lines.append(f"检查时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        total_files = len(results)
        total_leak_files = sum(1 for r in results if r['leak_lines'])
        lines.append(f"检查文件数: {total_files}")
        lines.append(f"发现涉密文件数: {total_leak_files}")
        lines.append("-" * 60)
        if total_leak_files == 0:
            lines.append("未发现涉密数据。")
        else:
            lines.append("详细结果：")
            for r in results:
                path = r['path']
                leak_lines = r['leak_lines']
                file_type = r.get('file_type', 'unknown')
                note = r.get('note', '')
                if leak_lines:
                    lines.append(f"\n【文件】{path}")
                    lines.append(f"  类型: {file_type}")
                    if note:
                        lines.append(f"  备注: {note}")
                    lines.append(f"  涉密信息 ({len(leak_lines)} 处):")
                    for line_no, keyword, content in leak_lines:
                        lines.append(f"    第{line_no}行 | 关键词 [{keyword}] → {content}")
        lines.append("=" * 60)
        lines.append("报告结束")
        return "\n".join(lines)
    @staticmethod
    def _build_html_result(results: list) -> str:
        import html as html_mod
        total_leak = sum(1 for r in results if r['leak_lines'])
        html = f"""
        <h3>✅ 文件检查结果</h3>
        <p>共检查 <strong>{len(results)}</strong> 个文件，发现 <strong>{total_leak}</strong> 个含涉密信息</p>
        <table class="table table-bordered">
            <thead>
                <tr><th>文件路径</th><th>类型</th><th>涉密行数</th><th>操作</th><th>备注</th></tr>
            </thead>
            <tbody>
        """

        def format_leak_lines(leak_lines: list) -> str:
            """
            将 leak_lines 格式化为可读字符串。
            输入: [ (行号, 关键字, 匹配内容) , ... ]
            输出: 多行字符串，每行格式如：第1行，关键字为：“机密”，具体内容：“["机密","秘密","绝密"...]”
            """
            lines = []
            for line_no, keyword, content in leak_lines:
                lines.append(f'第{line_no}行，关键字为：“{keyword}”，具体内容：“{content}”')
            return '\n'.join(lines)
        for i, r in enumerate(results):
            # 格式化涉密行详情
            lines_detail = format_leak_lines(r['leak_lines']) if r['leak_lines'] else "无"
            lines_str = "; ".join([f"第{l[0]}行" for l in r['leak_lines']]) if r['leak_lines'] else "无"

            note = r.get('note', '')

            # 按钮 — 点击弹出弹窗
            btn = (
                f'<button class="btn btn-sm btn-outline-info" '
                f'onclick="showModal(\'modal_{i}\')">'
                f'查看详情</button>'
            )

            # 弹窗 HTML（初始隐藏）
            modal = f"""
            <div id="modal_{i}" class="my-modal-overlay" style="display:none;" onclick="closeModal('modal_{i}')">
                <div class="my-modal-content" onclick="event.stopPropagation();">
                    <div class="my-modal-header">
                        <span class="my-modal-title">{html_mod.escape(r['path'])}</span>
                        <span class="my-modal-close" onclick="closeModal('modal_{i}')">&times;</span>
                    </div>
                    <div class="my-modal-body">
                        <p><strong>文件路径：</strong>{html_mod.escape(r['path'])}</p>
                        <p><strong>文件类型：</strong>{r['file_type']}</p>
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
                <td>{r['file_type']}</td>
                <td>{lines_str}</td>
                <td>{btn}{modal}</td>
                <td>{note}</td>
            </tr>
            """

        html += "</tbody></table>"

        # 追加弹窗所需的 CSS 和 JS（只需注入一次）
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