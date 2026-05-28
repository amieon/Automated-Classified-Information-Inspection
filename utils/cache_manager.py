"""
缓存管理模块 - 基于内容 MD5 的智能检测结果缓存
支持：网页 / 文件 / 图片 / 音频 / 数据库
策略：内容指纹 + 检测配置合并 → 单一 MD5 键，TTL 过期 + LRU 容量淘汰

（v3 字典配置版：所有缓存操作接收 config 字典，彻底消除实例态竞争）
"""
import hashlib
import json
import threading
from typing import Optional, Any, Union

import diskcache as dc


class DetectionCache:
    """
    涉密检测结果缓存管理器

    核心设计：
    1. 所有缓存键只依赖“内容 MD5 + 检测配置”，不再耦合文件路径或 URL。
    2. 检测配置通过一个字典（例如 dict(keywords=..., algorithm=..., max_insert=...)）
       在每次 get/set 时传入，天然线程安全，无需实例级状态。
    3. 统一外部接口：get_xxx(content, config) / set_xxx(content, result, config)
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

    # ==================== 内部指纹计算 ====================

    def _content_fingerprint(self, content: Union[str, bytes], config: dict) -> str:
        """
        计算“内容 + 检测配置”的组合 MD5，作为缓存唯一键。
        config 必须包含 keywords, algorithm, max_insert，也可以有额外字段（如 ocr）。
        """
        # 对 config 键排序后序列化，确保无论插入顺序如何都得到相同字符串
        config_str = json.dumps(config, sort_keys=True, ensure_ascii=False)
        config_bytes = config_str.encode("utf-8")

        if isinstance(content, str):
            data = content.encode("utf-8") + config_bytes
        else:
            data = content + config_bytes
        return hashlib.md5(data).hexdigest()

    # ==================== 统一外部接口 ====================

    # --- 网页 ---
    def get_web(self, content: str, config: dict) -> Optional[Any]:
        fp = self._content_fingerprint(content, config)
        return self.cache.get(f"web:{fp}")

    def set_web(self, content: str, result: Any, config: dict) -> None:
        fp = self._content_fingerprint(content, config)
        self.cache.set(f"web:{fp}", result, expire=self.ttl_map["web"])

    # --- 文件（通用二进制文件）---
    def get_file(self, content: bytes, config: dict) -> Optional[Any]:
        fp = self._content_fingerprint(content, config)
        return self.cache.get(f"file:{fp}")

    def set_file(self, content: bytes, result: Any, config: dict) -> None:
        fp = self._content_fingerprint(content, config)
        self.cache.set(f"file:{fp}", result, expire=self.ttl_map["file"])

    # --- 图片 ---
    def get_image(self, content: bytes, config: dict) -> Optional[Any]:
        fp = self._content_fingerprint(content, config)
        return self.cache.get(f"image:{fp}")

    def set_image(self, content: bytes, result: Any, config: dict) -> None:
        fp = self._content_fingerprint(content, config)
        self.cache.set(f"image:{fp}", result, expire=self.ttl_map["image"])

    # --- 音频 ---
    def get_audio(self, content: bytes, config: dict) -> Optional[Any]:
        fp = self._content_fingerprint(content, config)
        return self.cache.get(f"audio:{fp}")

    def set_audio(self, content: bytes, result: Any, config: dict) -> None:
        fp = self._content_fingerprint(content, config)
        self.cache.set(f"audio:{fp}", result, expire=self.ttl_map["audio"])

    # --- 数据库（特殊：由调用方提供唯一描述串）---
    def get_db(self, identifier: str, config: dict) -> Optional[Any]:
        """identifier: 描述数据库检测范围的字符串，如 'db_name:table:行数:校验和'"""
        fp = self._content_fingerprint(identifier, config)
        return self.cache.get(f"db:{fp}")

    def set_db(self, identifier: str, result: Any, config: dict) -> None:
        fp = self._content_fingerprint(identifier, config)
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


# ==================== 全局单例（线程安全） ====================
_cache_instance: Optional[DetectionCache] = None
_instance_lock = threading.Lock()


def get_cache() -> DetectionCache:
    """获取全局缓存单例（双重检查加锁，线程安全）"""
    global _cache_instance
    if _cache_instance is None:
        with _instance_lock:
            if _cache_instance is None:
                _cache_instance = DetectionCache()
    return _cache_instance