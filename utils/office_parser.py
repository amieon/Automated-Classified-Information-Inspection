import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO


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