# checkers/file_checker.py
import hashlib
import os
import tempfile
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse

from utils.cache_manager import get_cache
from utils.hidden_and_encrypted_checker import is_hidden_file, check_encryption
from utils.parallel import run_parallel
from .base_checker import BaseChecker
from detector.leak_detector import LeakDetector
import zipfile
from io import BytesIO
from utils.office_parser import parse_xlsx, parse_docx, parse_pptx, parse_pdf
from utils.office_parser import parse_xls, parse_doc, parse_ppt
from utils.report_exporter import publish_latest_report


def update_latest_report(text_report: str) -> None:
    publish_latest_report(text_report)

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
    '7z':   b'7z\xbc\xaf\x27\x1c\x00',
    'rar': b'Rar!\x1a\x07',
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
            '7z': '7z',
            'rar': 'rar',
        }
        if ext in ext_map:
            return ext_map[ext]

    return 'binary'


def safe_parse(parser, content: bytes, file_type: str) -> str:
    try:
        return parser(content)
    except Exception as e:
        print(f"  [WARN] Failed to parse {file_type} file: {e}")
        return ''


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
                    return safe_parse(parse_docx, data, 'docx')
                elif 'xl/workbook.xml' in names:
                    #print(f"  📖 检测为 xlsx 文件")
                    return safe_parse(parse_xlsx, data, 'xlsx')
                elif 'ppt/presentation.xml' in names:
                    #print(f"  📖 检测为 pptx 文件")
                    return safe_parse(parse_pptx, data, 'pptx')
        except Exception as e:
            print(f"  ⚠️ Office 文件解析失败: {e}")

    if len(data) >= 5 and data[:5] == b'%PDF-':
        #print(f"  📖 检测为 pdf 文件")
        return safe_parse(parse_pdf, data, 'pdf')

    # --- 旧格式 Office (OLE2) ---
    # OLE2 文件的魔数是前8字节: D0 CF 11 E0 A1 B1 1A E1
    if len(data) >= 8 and data[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        #print(f"  📖 检测为旧版 Office 文件（根据扩展名: {ext}）")

        if ext == '.doc':
            #print("  🔄 使用旧版 doc 解析器")
            return safe_parse(parse_doc, data, 'doc')
        elif ext == '.xls':
            #print("  🔄 使用旧版 xls 解析器")
            return safe_parse(parse_xls, data, 'xls')
        elif ext == '.ppt':
            #print("  🔄 使用旧版 ppt 解析器")
            return safe_parse(parse_ppt, data, 'ppt')
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
                    #print("  🔄 根据流检测为 doc")
                    return safe_parse(parse_doc, data, 'doc')
                elif 'Workbook' in all_streams or 'Book' in all_streams:
                    #print("  🔄 根据流检测为 xls")
                    return safe_parse(parse_xls, data, 'xls')
                elif 'PowerPoint Document' in all_streams:
                    #print("  🔄 根据流检测为 ppt")
                    return safe_parse(parse_ppt, data, 'ppt')
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


def process_single_file(
    file_path: str,
    keywords: str,
    algorithm: str,
    max_insert: int,
    cache=None,          # 缓存实例
    config_raw: str = "" # 检测配置指纹字符串
) -> Optional[dict]:
    """
    处理单个文件：读取、检测、获取属性。
    若提供缓存，则优先从缓存加载 leak_lines 和 file_type，跳过文本检测。
    """
    # 1. 读取文件全部二进制内容
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
    except Exception:
        content = b''
    # 2. 计算缓存 key 并尝试命中
    cached_result = None
    if cache and config_raw and content:
        fp = hashlib.md5(content + config_raw.encode()).hexdigest()
        key = f"file:{fp}"
        cached_result = cache.cache.get(key)
    # 3. 从缓存获取核心检测结果，或者执行实际检测
    if cached_result is not None:
        leak_lines = cached_result.get('leak_lines', [])
        file_type = cached_result.get('file_type', guess_file_type(content, is_bytes=True))
    else:
        # 执行正式检测
        detector = LeakDetector(keywords=keywords, algorithm=algorithm, max_insert=max_insert)
        text = read_text_from_bytes(content, os.path.basename(file_path))
        leak_lines = detector.check_text(text) if text else []
        file_type = guess_file_type(content, is_bytes=True)
        # 写入缓存（仅保存与内容相关的字段）
        if cache and config_raw and content:
            cache.cache.set(key, {
                'leak_lines': leak_lines,
                'file_type': file_type
            }, expire=cache.ttl_map["file"])
    # 4. 检查文件系统属性（这些不能缓存，每次都重新检测）
    is_hid = is_hidden_file(file_path)
    enc_info = check_encryption(file_path)
    note_parts = []
    if not content:
        note_parts.append('无法读取文件')
    if is_hid:
        note_parts.append('隐藏文件')
    if enc_info['is_encrypted']:
        note_parts.append('加密' if not enc_info.get('is_pseudo') else '伪加密')
    return {
        'path': str(file_path),
        'leak_lines': leak_lines,
        'file_type': file_type,
        'note': '，'.join(note_parts),
        'is_encrypted': enc_info['is_encrypted'],
        'is_hidden': is_hid,
        'is_pseudo': enc_info.get('is_pseudo', False),
    }

# ==================== FastAPI 路由注册 ====================
class FileCheckerModule(BaseChecker):
    def register_routes(self, app: FastAPI):
        # ------ 方式1：输入路径 ------
        @app.post("/check/file/path", response_class=HTMLResponse)
        async def check_file_path(
            path: str = Form(...),
            algorithm: str = Form("regex"),
            keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
            max_insert: int = Form(3)
        ):
            cache = get_cache()
            # 构建本次请求的配置指纹（不污染全局状态）
            config_raw = f"{keywords}||{algorithm}||{max_insert}"
            p = Path(path)
            if not p.exists():
                return HTMLResponse(content="<div class='alert alert-danger'>路径不存在</div>")
            results = []
            if p.is_file():
                # 单文件直接调用（带缓存）
                result = process_single_file(
                    str(p), keywords, algorithm, max_insert,
                    cache=cache, config_raw=config_raw
                )
                if result:
                    results.append(result)
            elif p.is_dir():
                # 收集所有文件路径
                file_list = []
                for root, dirs, files in os.walk(p):
                    for file in files:
                        file_list.append(str(Path(root) / file))
                # 并行检测，每个 worker 独立使用缓存
                results = run_parallel(
                    process_func=lambda fp: process_single_file(
                        fp, keywords, algorithm, max_insert,
                        cache=cache, config_raw=config_raw
                    ),
                    items=file_list,
                    max_workers=4,
                    executor_type="thread",
                    description="检查文件中"
                )
            else:
                return HTMLResponse(content="<div class='alert alert-danger'>既不是文件也不是文件夹</div>")
            text_report = self._generate_text_report(results, source_type="文件路径检查")
            update_latest_report(text_report)
            return self._build_html_result(results)
        # ------ 方式2：上传文件 ------
        @app.post("/check/file/upload", response_class=HTMLResponse)
        async def check_file_upload(
                files: List[UploadFile] = Form(...),
                algorithm: str = Form("regex"),
                keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
                max_insert: int = Form(3)
        ):
            cache = get_cache()
            config_raw = f"{keywords}||{algorithm}||{max_insert}"
            results = []

            for file in files:
                content = await file.read()

                # 临时文件用于加密检查
                is_encrypted = False
                is_pseudo = False
                if content:
                    suffix = os.path.splitext(file.filename)[1] if file.filename else ''
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name
                    try:
                        enc_info = check_encryption(tmp_path)
                        is_encrypted = enc_info['is_encrypted']
                        is_pseudo = enc_info.get('is_pseudo', False)
                    finally:
                        os.unlink(tmp_path)

                # 缓存检查
                cached_result = None
                if cache and config_raw and content:
                    fp = hashlib.md5(content + config_raw.encode()).hexdigest()
                    key = f"file:{fp}"
                    cached_result = cache.cache.get(key)

                if cached_result is not None:
                    leak_lines = cached_result.get('leak_lines', [])
                    file_type = cached_result.get('file_type', guess_file_type(content, is_bytes=True))
                    text = read_text_from_bytes(content, file.filename)
                else:
                    detector = LeakDetector(keywords=keywords, algorithm=algorithm, max_insert=max_insert)
                    text = read_text_from_bytes(content, file.filename)
                    leak_lines = detector.check_text(text) if text else []
                    file_type = guess_file_type(content, is_bytes=True)  # ← 修复：加上 is_bytes=True
                    if cache and config_raw and content:
                        cache.cache.set(key, {
                            'leak_lines': leak_lines,
                            'file_type': file_type
                        }, expire=cache.ttl_map["file"])

                # 备注
                note_parts = []
                if not content:
                    note_parts.append('空文件')
                if not text and content:
                    note_parts.append('无法读取文本内容')
                    if os.path.splitext(file.filename or '')[1].lower() == '.ppt':
                        note_parts.append('legacy .ppt requires LibreOffice soffice')
                if is_encrypted:
                    note_parts.append('加密' if not is_pseudo else '伪加密')

                results.append({
                    'path': file.filename,
                    'leak_lines': leak_lines,
                    'file_type': file_type,
                    'note': '，'.join(note_parts),
                    'is_encrypted': is_encrypted,
                    'is_hidden': False,
                    'is_pseudo': is_pseudo,
                })

            text_report = self._generate_text_report(results, source_type="文件上传检查")
            update_latest_report(text_report)
            return self._build_html_result(results)
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
            lines = []
            for line_no, keyword, content in leak_lines:
                lines.append(f'第{line_no}行，关键字为：“{keyword}”，具体内容：“{content}”')
            return '\n'.join(lines)

        for i, r in enumerate(results):
            lines_detail = format_leak_lines(r['leak_lines']) if r['leak_lines'] else "无"
            lines_str = "; ".join([f"第{l[0]}行" for l in r['leak_lines']]) if r['leak_lines'] else "无"
            note = r.get('note', '')
            # ★ 构建文件类型状态后缀
            type_status = []
            if r.get('is_encrypted'):
                if r.get('is_pseudo'):
                    type_status.append('伪加密')
                else:
                    type_status.append('加密')
            if r.get('is_hidden'):
                type_status.append('隐藏')
            status_suffix = f" ({', '.join(type_status)})" if type_status else ""
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
                        <p><strong>文件类型：</strong>{r['file_type']}{status_suffix}</p>
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
                <td>{r['file_type']}{status_suffix}</td>   <!-- 这里添加了状态后缀 -->
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
