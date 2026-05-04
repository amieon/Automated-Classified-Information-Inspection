# checkers/base_checker.py
from abc import ABC, abstractmethod
from fastapi import FastAPI

class BaseChecker(ABC):
    """所有检查模块的基类"""
    @abstractmethod
    def register_routes(self, app: FastAPI):
        """注册该模块的 API 路由"""
        pass