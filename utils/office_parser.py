import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO
import re
import olefile

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
    解析 .pptx 文件，提取所有幻灯片中的文本（精确到段落和换行）
    """
    try:
        text_parts = []
        with zipfile.ZipFile(BytesIO(content)) as z:
            # 读取所有幻灯片文件
            for name in sorted(z.namelist()):
                if name.startswith('ppt/slides/slide') and name.endswith('.xml'):
                    slide_xml = z.read(name)
                    try:
                        root = ET.fromstring(slide_xml)
                    except ET.ParseError:
                        continue

                    # 命名空间映射（避免手动拼串）
                    ns = {
                        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
                        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
                        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
                    }

                    slide_texts = []
                    # 遍历所有形状（p:sp）
                    for sp in root.iter(f'{{{ns["p"]}}}sp'):
                        txBody = sp.find(f'{{{ns["p"]}}}txBody')
                        if txBody is None:
                            continue
                        # 遍历段落（a:p）
                        for para_elem in txBody.iter(f'{{{ns["a"]}}}p'):
                            para_parts = []
                            for child in para_elem:
                                local_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                                if local_tag == 'r':
                                    # 文本运行
                                    t = child.find(f'{{{ns["a"]}}}t')
                                    if t is not None and t.text:
                                        para_parts.append(t.text)
                                elif local_tag == 'br':
                                    # 手动换行
                                    para_parts.append('\n')
                                elif local_tag == 'fld':
                                    # 字段（如日期、页码等）
                                    t = child.find(f'{{{ns["a"]}}}t')
                                    if t is not None and t.text:
                                        para_parts.append(t.text)
                            if para_parts:
                                slide_texts.append(''.join(para_parts))

                    if slide_texts:
                        text_parts.append(f'[Slide {name}]')
                        text_parts.extend(slide_texts)

        return '\n'.join(text_parts)

    except Exception as e:
        print(f"  ⚠️ 解析 pptx 失败: {e}")
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
        print(f"  ✅ 成功提取 xls 文本，共 {len(result)} 字符")
        return result

    except ImportError:
        print("  ⚠️ 需要安装 xlrd==1.2.0: pip install xlrd==1.2.0")
        return ''
    except Exception as e:
        print(f"  ⚠️ 解析 xls 失败: {e}")
        return ''


# ==================== 旧版 .ppt 解析 ====================
def parse_ppt(content: bytes) -> str:
    """
    解析旧版 .ppt 文件，提取所有幻灯片文本
    使用 olefile 从 PowerPoint Document 流中提取文本
    """
    try:
        ole = olefile.OleFileIO(BytesIO(content))
        text_parts = []

        if ole.exists('PowerPoint Document'):
            data = ole.openstream('PowerPoint Document').read()

            # 方法1：提取 Unicode 文本（PPT 中的文本通常是 UTF-16LE 编码）
            # 查找连续的非空 Unicode 字符
            unicode_texts = []
            i = 0
            while i < len(data) - 1:
                # 尝试读取 UTF-16LE 字符
                char_code = data[i] | (data[i + 1] << 8)
                if 0x20 <= char_code <= 0x7E or 0x4E00 <= char_code <= 0x9FFF or char_code in (0x0D, 0x0A, 0x09):
                    # 可打印 ASCII、中文或换行/制表符
                    chunk = bytearray()
                    while i < len(data) - 1:
                        cc = data[i] | (data[i + 1] << 8)
                        if 0x20 <= cc <= 0x7E or 0x4E00 <= cc <= 0x9FFF or cc in (0x0D, 0x0A, 0x09):
                            chunk.extend([data[i], data[i + 1]])
                            i += 2
                        else:
                            break
                    if len(chunk) >= 4:  # 至少 2 个字符
                        try:
                            unicode_texts.append(chunk.decode('utf-16le'))
                        except:
                            pass
                else:
                    i += 2

            # 方法2：作为备用，尝试整体解码后用正则提取
            try:
                raw_text = data.decode('utf-16le', errors='ignore')
                # 提取长度 > 3 的可读文本
                readable = re.findall(r'[\u4e00-\u9fff\w\s.,!?;:()【】、，。！？；：""''（）\-\n\r]{4,}', raw_text)
                text_parts.extend([t.strip() for t in readable if len(t.strip()) > 2])
            except:
                pass

            # 合并方法1的结果
            for t in unicode_texts:
                t = t.strip()
                if len(t) > 2:
                    text_parts.append(t)

        ole.close()

        # 去重并合并
        seen = set()
        unique_texts = []
        for t in text_parts:
            if t not in seen:
                seen.add(t)
                unique_texts.append(t)

        result = '\n'.join(unique_texts) if unique_texts else ''
        print(f"  ✅ 成功提取 ppt 文本，共 {len(result)} 字符")
        return result

    except ImportError:
        print("  ⚠️ 需要安装 olefile: pip install olefile")
        return ''
    except Exception as e:
        print(f"  ⚠️ 解析 ppt 失败: {e}")
        return ''