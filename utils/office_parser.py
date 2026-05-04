import olefile
import zipfile
from io import BytesIO
import xml.etree.ElementTree as ET
import re
# ==================== Office 文件解析核心函数 ====================


def parse_docx(content: bytes) -> str:
    """
    解析 .docx 文件，提取所有文本内容
    """
    try:
        text_parts = []
        with zipfile.ZipFile(BytesIO(content)) as z:
            # docx 的正文在 word/document.xml
            if 'word/document.xml' in z.namelist():
                xml_content = z.read('word/document.xml')
                root = ET.fromstring(xml_content)

                # 提取所有 <w:t> 标签中的文本（段落文本）
                for t in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
                    if t.text:
                        text_parts.append(t.text)

                # 提取所有 <w:tab/> 标签，替换为制表符
                # 提取所有 <w:br/> 标签，替换为换行符

        return '\n'.join(text_parts)
    except Exception as e:
        print(f"  ⚠️ 解析 docx 失败: {e}")
        return ''


def parse_xlsx(content: bytes) -> str:
    """
    解析 .xlsx 文件，提取所有单元格文本
    """
    try:
        text_parts = []
        with zipfile.ZipFile(BytesIO(content)) as z:
            # 读取共享字符串表（shared strings）
            shared_strings = []
            if 'xl/sharedStrings.xml' in z.namelist():
                ss_xml = z.read('xl/sharedStrings.xml')
                ss_root = ET.fromstring(ss_xml)
                ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
                for si in ss_root.iter(f'{{{ns}}}si'):
                    # 单元格文本可能在 <t> 或 <r><t> 中
                    texts = []
                    for t in si.iter(f'{{{ns}}}t'):
                        if t.text:
                            texts.append(t.text)
                    shared_strings.append(''.join(texts))

            # 读取所有工作表
            for name in z.namelist():
                if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
                    sheet_xml = z.read(name)
                    sheet_root = ET.fromstring(sheet_xml)

                    # 遍历所有行
                    for row in sheet_root.iter(f'{{{ns}}}row'):
                        row_texts = []
                        for c in row.iter(f'{{{ns}}}c'):
                            # 获取单元格类型和值
                            cell_type = c.get('t', '')  # 's'=shared string, 'inlineStr'=内联, ''=数字
                            cell_ref = c.get('r', '')  # 单元格引用，如 A1

                            v_elem = c.find(f'{{{ns}}}v')
                            if v_elem is not None and v_elem.text:
                                if cell_type == 's':
                                    # 共享字符串：value 是索引
                                    idx = int(v_elem.text)
                                    if idx < len(shared_strings):
                                        row_texts.append(shared_strings[idx])
                                else:
                                    row_texts.append(v_elem.text)
                            else:
                                # 内联字符串
                                is_elem = c.find(f'{{{ns}}}is')
                                if is_elem is not None:
                                    inline_texts = []
                                    for t in is_elem.iter(f'{{{ns}}}t'):
                                        if t.text:
                                            inline_texts.append(t.text)
                                    row_texts.append(''.join(inline_texts))

                        if row_texts:
                            text_parts.append('\t'.join(row_texts))

        return '\n'.join(text_parts)
    except Exception as e:
        print(f"  ⚠️ 解析 xlsx 失败: {e}")
        return ''





def parse_pptx(content: bytes) -> str:
    """
    解析 .pptx 文件：
    - 保证 slide 顺序正确
    - 提取 shape / group / table / notes 文本
    - 保留段落与换行结构
    """
    try:
        with zipfile.ZipFile(BytesIO(content)) as z:
            ns = {
                'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
                'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
            }

            # ---------- 工具函数 ----------
            def extract_text_from_txBody(txBody):
                """提取 txBody 中的文本"""
                texts = []
                for para in txBody.iter(f'{{{ns["a"]}}}p'):
                    parts = []
                    for node in para:
                        tag = node.tag.split('}')[-1]

                        if tag == 'r':  # 普通文本
                            t = node.find(f'{{{ns["a"]}}}t')
                            if t is not None and t.text:
                                parts.append(t.text)

                        elif tag == 'br':  # 手动换行
                            parts.append('\n')

                        elif tag == 'fld':  # 字段
                            t = node.find(f'{{{ns["a"]}}}t')
                            if t is not None and t.text:
                                parts.append(t.text)

                    if parts:
                        texts.append(''.join(parts))
                return texts

            def extract_from_shape(sp):
                """递归处理 shape / group"""
                results = []

                # 普通文本框
                txBody = sp.find(f'.//{{{ns["p"]}}}txBody')
                if txBody is not None:
                    results.extend(extract_text_from_txBody(txBody))

                # 表格文本
                for tbl in sp.iter(f'{{{ns["a"]}}}tbl'):
                    for cell in tbl.iter(f'{{{ns["a"]}}}tc'):
                        txBody = cell.find(f'{{{ns["a"]}}}txBody')
                        if txBody is not None:
                            results.extend(extract_text_from_txBody(txBody))

                return results

            def natural_key(name):
                """按 slide1, slide2, slide10 正确排序"""
                return [
                    int(text) if text.isdigit() else text
                    for text in re.split(r'(\d+)', name)
                ]

            # ---------- 主流程 ----------
            slide_files = sorted(
                [n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')],
                key=natural_key
            )

            all_text = []

            for idx, slide_name in enumerate(slide_files, 1):
                try:
                    root = ET.fromstring(z.read(slide_name))
                except ET.ParseError:
                    continue

                slide_texts = []

                # 1️⃣ 处理所有 shape（包括 group 内的）
                for sp in root.iter(f'{{{ns["p"]}}}sp'):
                    slide_texts.extend(extract_from_shape(sp))

                # 2️⃣ 处理 group shape（嵌套）
                for grp in root.iter(f'{{{ns["p"]}}}grpSp'):
                    for sp in grp.iter(f'{{{ns["p"]}}}sp'):
                        slide_texts.extend(extract_from_shape(sp))

                # 3️⃣ 处理备注（notes）
                notes_name = slide_name.replace('slides/slide', 'notesSlides/notesSlide')
                if notes_name in z.namelist():
                    try:
                        notes_root = ET.fromstring(z.read(notes_name))
                        for sp in notes_root.iter(f'{{{ns["p"]}}}sp'):
                            slide_texts.extend(extract_from_shape(sp))
                    except:
                        pass

                # 去重 + 清理空行
                slide_texts = [t.strip() for t in slide_texts if t.strip()]

                if slide_texts:
                    all_text.append(f'===== Slide {idx} =====')
                    all_text.extend(slide_texts)

            return '\n'.join(all_text)

    except Exception as e:
        print(f"⚠️ 解析 pptx 失败: {e}")
        return ''


# ==================== 旧版 .doc 解析 ====================
def parse_doc(content: bytes) -> str:
    """
    解析旧版 .doc 文件，提取文本内容
    使用 olefile 从 WordDocument 流中提取 Unicode 文本
    """
    try:
        ole = olefile.OleFileIO(BytesIO(content))

        text_parts = []

        # Word 文档的文本存储在 WordDocument 流中，以 UTF-16LE 编码
        if ole.exists('WordDocument'):
            data = ole.openstream('WordDocument').read()

            # 用 UTF-16LE 解码，忽略错误
            raw_text = data.decode('utf-16le', errors='ignore')

            # 提取有效文本（过滤掉乱码和单个字符）
            lines = []
            for line in raw_text.split('\x00'):
                line = line.strip()
                # 保留长度 > 2 且包含可读字符的文本
                if len(line) > 2:
                    # 检查是否包含字母、数字或中文
                    if any(c.isalpha() or '\u4e00' <= c <= '\u9fff' for c in line):
                        lines.append(line)

            if lines:
                text_parts.extend(lines)

        # 也尝试从 0Table 或 1Table 流中提取
        for stream_name in ['0Table', '1Table']:
            if ole.exists(stream_name):
                try:
                    table_data = ole.openstream(stream_name).read()
                    decoded = table_data.decode('utf-16le', errors='ignore')
                    readable = re.findall(r'[\u4e00-\u9fff\w\s.,!?;:()【】、，。！？；：""''（）\-\n\r]{3,}', decoded)
                    text_parts.extend([t.strip() for t in readable if len(t.strip()) > 2])
                except:
                    pass

        ole.close()

        result = '\n'.join(text_parts) if text_parts else ''
        print(f"  ✅ 成功提取 doc 文本，共 {len(result)} 字符")
        return result

    except ImportError:
        print("  ⚠️ 需要安装 olefile: pip install olefile")
        return ''
    except Exception as e:
        print(f"  ⚠️ 解析 doc 失败: {e}")
        return ''


# ==================== 旧版 .xls 解析 ====================
def parse_xls(content: bytes) -> str:
    """
    解析旧版 .xls 文件，提取所有单元格文本
    使用 xlrd 库（版本 1.2.0）
    """
    try:
        import xlrd

        workbook = xlrd.open_workbook(file_contents=content)
        text_parts = []

        for sheet_idx in range(workbook.nsheets):
            sheet = workbook.sheet_by_index(sheet_idx)
            sheet_name = sheet.name
            text_parts.append(f"【工作表: {sheet_name}】")

            for row_idx in range(sheet.nrows):
                row_texts = []
                for col_idx in range(sheet.ncols):
                    cell = sheet.cell(row_idx, col_idx)
                    if cell.ctype != xlrd.XL_CELL_EMPTY and cell.value:
                        row_texts.append(str(cell.value))

                if row_texts:
                    text_parts.append('\t'.join(row_texts))

            text_parts.append('')  # 空行分隔

        result = '\n'.join(text_parts) if text_parts else ''
        # print(f"  ✅ 成功提取 xls 文本，共 {len(result)} 字符")
        return result

    except ImportError:
        print("  ⚠️ 需要安装 xlrd==1.2.0: pip install xlrd==1.2.0")
        return ''
    except Exception as e:
        print(f"  ⚠️ 解析 xls 失败: {e}")
        return ''


# ==================== 旧版 .ppt 解析 ====================

def parse_ppt(content: bytes) -> str:
    pptx_bytes = convert_ppt_to_pptx(content)
    return parse_pptx(pptx_bytes)


import subprocess
import tempfile
from pathlib import Path


def convert_ppt_to_pptx(content: bytes, timeout=20) -> bytes:
    """
    使用 LibreOffice 将 .ppt 转为 .pptx
    返回 pptx 的 bytes
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.ppt"
        output_dir = Path(tmpdir)

        # 写入 ppt
        with open(input_path, "wb") as f:
            f.write(content)

        # 调用 libreoffice
        cmd = [
            "soffice",  # Linux / Mac
            "--headless",
            "--convert-to", "pptx",
            "--outdir", str(output_dir),
            str(input_path)
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout
            )
        except FileNotFoundError:
            raise RuntimeError("未找到 LibreOffice（soffice），请先安装")

        if result.returncode != 0:
            raise RuntimeError(
                f"转换失败:\n{result.stderr.decode(errors='ignore')}"
            )

        # 找输出文件
        output_path = output_dir / "input.pptx"

        if not output_path.exists():
            raise RuntimeError("转换成功但未找到输出文件")

        with open(output_path, "rb") as f:
            return f.read()