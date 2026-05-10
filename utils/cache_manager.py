"""
缓存管理模块 - 基于内容 MD5 的智能检测结果缓存
支持：网页 / 文件 / 图片 / 音频 / 数据库
策略：内容指纹 + 检测配置合并 → 单一 MD5 键，TTL 过期 + LRU 容量淘汰
"""
import hashlib
from typing import Optional, Any, Union

import diskcache as dc


class DetectionCache:
    """
    涉密检测结果缓存管理器

    核心设计：
    1. 所有缓存键只依赖“内容 MD5 + 检测配置指纹”，不再耦合文件路径或 URL。
    2. 检测配置通过 config_fingerprint() 预置，内部自动参与指纹计算。
    3. 统一外部接口：get_xxx(content) / set_xxx(content, result)，
       其中 content 为网页字符串或文件/图片/音频的原始字节。
    """

    def __init__(self, cache_dir: str = "./.detect_cache", size_limit_mb: int = 500):
        """
        Args:
            cache_dir:     缓存目录（可设为隐藏文件夹，方便加入 .gitignore）
            size_limit_mb: 最大容量（MB），超出自动 LRU 淘汰最久未用条目
        """
        self.cache = dc.Cache(cache_dir, size_limit=size_limit_mb * 1024 * 1024)
        self._cache_dir = cache_dir

        # 各数据源默认过期时间（秒）
        self.ttl_map = {
            "web":   86400,       # 网页：24小时
            "file":  604800,      # 文件：7天
            "image": 604800,      # 图片：7天
            "audio": 604800,      # 音频：7天
            "db":    3600,        # 数据库：1小时（变化频繁）
        }

        # 当前检测配置指纹（影响所有缓存键）
        self._config_raw: str = ""

    # ==================== 配置指纹设置 ====================

    def config_fingerprint(self, keywords: str = "", algorithm: str = "regex",
                           max_insert: int = 3) -> None:
        """
        设置检测配置指纹。任何配置参数变化 → 指纹不同 → 所有缓存自动失效。

        调用时机：每次检测前必须调用一次（通常在 checker 初始化或配置变更时）。
        """
        self._config_raw = f"{keywords}||{algorithm}||{max_insert}"

    # ==================== 内部指纹计算 ====================

    def _content_fingerprint(self, content: Union[str, bytes]) -> str:
        """
        计算“内容 + 检测配置”的组合 MD5，作为缓存唯一键。

        - 文本内容 (str) → 先 utf-8 编码再拼接配置字节
        - 二进制内容 (bytes) → 直接拼接配置字节
        """
        config_bytes = self._config_raw.encode("utf-8")
        if isinstance(content, str):
            data = content.encode("utf-8") + config_bytes
        else:
            data = content + config_bytes
        return hashlib.md5(data).hexdigest()

    # ==================== 统一外部接口 ====================

    # --- 网页 ---
    def get_web(self, content: str) -> Optional[Any]:
        """content: 网页 HTML 文本"""
        fp = self._content_fingerprint(content)
        return self.cache.get(f"web:{fp}")

    def set_web(self, content: str, result: Any) -> None:
        fp = self._content_fingerprint(content)
        self.cache.set(f"web:{fp}", result, expire=self.ttl_map["web"])

    # --- 文件（通用二进制文件）---
    def get_file(self, content: bytes) -> Optional[Any]:
        """content: 文件内容的完整字节"""
        fp = self._content_fingerprint(content)
        return self.cache.get(f"file:{fp}")

    def set_file(self, content: bytes, result: Any) -> None:
        fp = self._content_fingerprint(content)
        self.cache.set(f"file:{fp}", result, expire=self.ttl_map["file"])

    # --- 图片 ---
    def get_image(self, content: bytes) -> Optional[Any]:
        """content: 图片文件的字节数据"""
        fp = self._content_fingerprint(content)
        return self.cache.get(f"image:{fp}")

    def set_image(self, content: bytes, result: Any) -> None:
        fp = self._content_fingerprint(content)
        self.cache.set(f"image:{fp}", result, expire=self.ttl_map["image"])

    # --- 音频 ---
    def get_audio(self, content: bytes) -> Optional[Any]:
        """content: 音频文件的字节数据"""
        fp = self._content_fingerprint(content)
        return self.cache.get(f"audio:{fp}")

    def set_audio(self, content: bytes, result: Any) -> None:
        fp = self._content_fingerprint(content)
        self.cache.set(f"audio:{fp}", result, expire=self.ttl_map["audio"])

    # --- 数据库（特殊：由调用方提供唯一描述串）---
    def get_db(self, identifier: str) -> Optional[Any]:
        """
        identifier: 描述数据库检测范围的字符串，如 "db_name:table:行数:校验和"
        该串与检测配置合并后生成指纹。
        """
        fp = self._content_fingerprint(identifier)
        return self.cache.get(f"db:{fp}")

    def set_db(self, identifier: str, result: Any) -> None:
        fp = self._content_fingerprint(identifier)
        self.cache.set(f"db:{fp}", result, expire=self.ttl_map["db"])

    # ==================== 管理接口 ====================

    def stats(self) -> dict:
        """返回缓存统计信息"""
        return {
            "size_mb":       round(self.cache.volume() / 1024 / 1024, 2),
            "total_entries": len(self.cache),
            "cache_dir":     self._cache_dir,
        }

    def clear(self, source_type: Optional[str] = None) -> None:
        """
        清空缓存。

        Args:
            source_type: None 清空全部；"web" / "file" / "image" / "audio" / "db" 仅清对应类型
        """
        if source_type is None:
            self.cache.clear()
        else:
            prefix = f"{source_type}:"
            for key in list(self.cache):
                if key.startswith(prefix):
                    del self.cache[key]

    def close(self) -> None:
        self.cache.close()


# ==================== 全局单例 ====================
_cache_instance: Optional[DetectionCache] = None


def get_cache() -> DetectionCache:
    """获取全局缓存单例（线程安全）"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = DetectionCache()
    return _cache_instance