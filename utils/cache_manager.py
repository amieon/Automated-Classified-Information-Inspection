"""
缓存管理模块 - 基于 diskcache 的智能检测结果缓存
支持：网页 / 文件 / 图片 / 音频 / 数据库
策略：内容MD5指纹 + TTL过期 + LRU容量淘汰
"""
import os
import hashlib
from typing import Optional, Any
from pathlib import Path

import diskcache as dc


class DetectionCache:
    """涉密检测结果缓存管理器"""

    def __init__(self, cache_dir: str = "./.detect_cache", size_limit_mb: int = 500):
        """
        Args:
            cache_dir:   缓存目录（隐藏文件夹，可放入 .gitignore）
            size_limit_mb: 最大容量（MB），超出自动 LRU 淘汰最久未用条目
        """
        self.cache = dc.Cache(cache_dir, size_limit=size_limit_mb * 1024 * 1024)
        self._cache_dir = cache_dir
        self.raw = ''
        # 各数据源默认过期时间（秒）
        self.ttl_map = {
            "web":   86400,       # 网页：24小时
            "file":  604800,      # 文件：7天
            "image": 604800,      # 图片：7天
            "audio": 604800,      # 音频：7天
            "db":    3600,        # 数据库：1小时（数据变化频繁）
        }

    # 在 DetectionCache 类中新增静态方法：

    def config_fingerprint(self, keywords: str = "", algorithm: str = "regex",
                           max_insert: int = 3):
        """计算检测配置指纹。配置任一参数变化 → 指纹不同 → 缓存自动失效"""
        self.raw = f"{keywords}||{algorithm}||{max_insert}"

    # 修改 get_file / set_file（以及其他数据源同理）：

    def get_file(self, file_path: str, config_fp: str = "") -> Optional[Any]:
        fp = self.file_md5(file_path)
        if fp is None:
            return None
        key = f"file:{config_fp}:{os.path.abspath(file_path)}:{fp}"
        return self.cache.get(key)

    def set_file(self, file_path: str, result: Any, config_fp: str = ""):
        fp = self.file_md5(file_path)
        if fp is None:
            return
        key = f"file:{config_fp}:{os.path.abspath(file_path)}:{fp}"
        self.cache.set(key, result, expire=self.ttl_map["file"])
    # ==================== 底层原子操作 ====================

    @staticmethod
    def compute_md5(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    @staticmethod
    def file_md5(file_path: str) -> Optional[str]:
        """计算文件内容 MD5，文件不存在返回 None"""
        try:
            with open(file_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except (FileNotFoundError, PermissionError, OSError):
            return None

    @staticmethod
    def text_md5(text: str) -> str:
        return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()

    def _make_key(self, source_type: str, identifier: str, fingerprint: str) -> str:
        """统一构造缓存键：类型:标识符:内容指纹"""
        return f"{source_type}:{identifier}:{fingerprint}"

    def get(self, source_type: str, identifier: str, fingerprint: str) -> Optional[Any]:
        """通用读取缓存，未命中返回 None"""
        return self.cache.get(self._make_key(source_type, identifier, fingerprint))

    def set(self, source_type: str, identifier: str, fingerprint: str,
            result: Any, ttl: int = None):
        """通用写入缓存，自动设置过期时间"""
        if ttl is None:
            ttl = self.ttl_map.get(source_type, 604800)
        self.cache.set(
            self._make_key(source_type, identifier, fingerprint),
            result,
            expire=ttl
        )

    # ==================== 便捷接口（各 checker 直接调用） ====================


    # --- 图片 ---
    def get_image(self, image_path: str, config_fp: str = "") -> Optional[Any]:
        fp = self.file_md5(image_path)
        if fp is None:
            return None
        key = f"image:{config_fp}:{os.path.abspath(image_path)}:{fp}"
        return self.cache.get(key)

    def set_image(self, image_path: str, result: Any, config_fp: str = ""):
        fp = self.file_md5(image_path)
        if fp is None:
            return
        key = f"image:{config_fp}:{os.path.abspath(image_path)}:{fp}"
        self.cache.set(key, result, expire=self.ttl_map["image"])

    # --- 音频 ---
    def get_audio(self, audio_path: str, config_fp: str = "") -> Optional[Any]:
        fp = self.file_md5(audio_path)
        if fp is None:
            return None
        key = f"audio:{config_fp}:{os.path.abspath(audio_path)}:{fp}"
        return self.cache.get(key)

    def set_audio(self, audio_path: str, result: Any, config_fp: str = ""):
        fp = self.file_md5(audio_path)
        if fp is None:
            return
        key = f"audio:{config_fp}:{os.path.abspath(audio_path)}:{fp}"
        self.cache.set(key, result, expire=self.ttl_map["audio"])

    # --- 网页 ---
    def get_web(self, url: str, html_content: str = None) -> Optional[Any]:
        """
        查网页缓存。
        - 如果只传 url，用 url 的 MD5 做指纹（轻量，适合不变页面）
        - 如果传 html_content，用内容 MD5 做指纹（精确，推荐）
        """
        if html_content:
            fp = self.text_md5(html_content + self.raw)
        else:
            fp = self.text_md5(url + self.raw)
        return self.get("web", url, fp)

    def set_web(self, url: str, result: Any, html_content: str = None):
        fp = self.text_md5(html_content + self.raw) if html_content else self.text_md5(url + self.raw)
        self.set("web", url, fp, result)

    # --- 数据库 ---
    def get_db(self, db_name: str, table_name: str, row_count: int,
               checksum: str) -> Optional[Any]:
        """数据库缓存：用表名+行数+校验和做指纹"""
        fp = self.text_md5(f"{table_name}:{row_count}:{checksum}")
        return self.get("db", f"{db_name}/{table_name}", fp)

    def set_db(self, db_name: str, table_name: str, row_count: int,
               checksum: str, result: Any):
        fp = self.text_md5(f"{table_name}:{row_count}:{checksum}")
        self.set("db", f"{db_name}/{table_name}", fp, result)

    # ==================== 管理接口 ====================

    def stats(self) -> dict:
        """返回缓存统计信息"""
        return {
            "size_mb":       round(self.cache.volume() / 1024 / 1024, 2),
            "total_entries": len(self.cache),
            "cache_dir":     self._cache_dir,
        }

    def clear(self, source_type: str = None):
        """
        清空缓存。
        - source_type=None：清空全部
        - source_type="web"：只清网页缓存
        """
        if source_type is None:
            self.cache.clear()
        else:
            prefix = f"{source_type}:"
            for key in list(self.cache):
                if key.startswith(prefix):
                    del self.cache[key]

    def close(self):
        self.cache.close()


# ==================== 全局单例 ====================
# 所有 checker 模块统一使用这一个实例
_cache_instance: Optional[DetectionCache] = None


def get_cache() -> DetectionCache:
    """获取全局缓存单例"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = DetectionCache()
    return _cache_instance