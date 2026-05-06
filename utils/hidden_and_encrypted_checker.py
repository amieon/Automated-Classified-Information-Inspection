# ==================== 隐藏文件检测 ====================
import ctypes
import os
import sys


def is_hidden_file(filepath: str) -> bool:
    """判断文件或文件夹是否为隐藏文件（Windows 隐藏属性 / Linux 点开头）"""
    if sys.platform != 'win32':
        return os.path.basename(filepath).startswith('.')
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(filepath)
        if attrs == -1:
            return False
        # FILE_ATTRIBUTE_HIDDEN = 2
        return bool(attrs & 2)
    except Exception:
        return False

# ==================== ZIP伪加密检测模块 ====================
def is_zip_pseudo_encrypted(filepath: str) -> dict:
    """
    正确检测 ZIP 是否加密/伪加密（支持多文件、AES 加密）
    """
    import struct

    with open(filepath, 'rb') as f:
        data = f.read()

    local_header_sig = b'\x50\x4b\x03\x04'
    central_header_sig = b'\x50\x4b\x01\x02'

    # 遍历所有本地文件头，如果任一文件加密则标记为加密
    any_encrypted_local = False
    offset = 0
    while True:
        idx = data.find(local_header_sig, offset)
        if idx == -1 or idx + 30 > len(data):
            break

        flag = struct.unpack('<H', data[idx+6:idx+8])[0]
        # bit0: 传统加密, bit6: AES 加密
        is_encrypted = (flag & 0x01) != 0 or (flag & 0x40) != 0
        if is_encrypted:
            any_encrypted_local = True

        # 跳到下一个文件头
        comp_size = struct.unpack('<I', data[idx+18:idx+22])[0]
        file_name_len = struct.unpack('<H', data[idx+26:idx+28])[0]
        extra_len = struct.unpack('<H', data[idx+28:idx+30])[0]
        offset = idx + 30 + file_name_len + extra_len + comp_size

    # 遍历所有中央目录头
    any_encrypted_central = False
    offset = 0
    while True:
        idx_c = data.find(central_header_sig, offset)
        if idx_c == -1 or idx_c + 46 > len(data):
            break

        flag = struct.unpack('<H', data[idx_c+6:idx_c+8])[0]
        is_encrypted = (flag & 0x01) != 0 or (flag & 0x40) != 0
        if is_encrypted:
            any_encrypted_central = True

        file_name_len = struct.unpack('<H', data[idx_c+28:idx_c+30])[0]
        extra_len = struct.unpack('<H', data[idx_c+30:idx_c+32])[0]
        comment_len = struct.unpack('<H', data[idx_c+32:idx_c+34])[0]
        offset = idx_c + 46 + file_name_len + extra_len + comment_len

    # 判断加密/伪加密
    if any_encrypted_central and not any_encrypted_local:
        return {'encrypted': True, 'pseudo': True}   # 中央目录加密位=1，本地=0 → 伪加密
    elif any_encrypted_central or any_encrypted_local:
        return {'encrypted': True, 'pseudo': False}  # 任一真实加密
    else:
        return {'encrypted': False, 'pseudo': False} # 均未加密

def is_7z_encrypted(filepath: str) -> bool:
    try:
        import py7zr
        with py7zr.SevenZipFile(filepath, 'r') as sz:
            # 尝试读取任何一个文件的属性
            for info in sz.list():
                if info.is_encrypted:
                    return True
            return False
    except py7zr.exceptions.PasswordRequired:
        return True
    except:
        return False

def is_rar_encrypted(filepath: str) -> bool:
    try:
        import rarfile
        with rarfile.RarFile(filepath) as rf:
            for info in rf.infolist():
                if info.needs_password():
                    return True
            return False
    except rarfile.RarCannotExec:
        # 提示用户安装 unrar
        print("⚠️ 需要 unrar 支持，请安装 unrar 或将其添加到 PATH")
        return False
    except:
        return False

def check_encryption(filepath: str) -> dict:
    """
    综合检测文件是否为压缩包加密，返回状态信息
    """
    ext = os.path.splitext(filepath)[1].lower()
    result = {'is_encrypted': False, 'is_pseudo': False, 'type': None}
    if ext == '.zip':
        #print(filepath)
        info = is_zip_pseudo_encrypted(filepath)
        #print(info)
        result['is_encrypted'] = info['encrypted']
        result['is_pseudo'] = info['pseudo']
        result['type'] = 'ZIP'
    elif ext == '.7z':
        result['is_encrypted'] = is_7z_encrypted(filepath)
        result['type'] = '7z'
    elif ext == '.rar':
        result['is_encrypted'] = is_rar_encrypted(filepath)
        result['type'] = 'RAR'
    else:
        result['type'] = 'other'
    return result