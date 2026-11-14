"""共享的响应模型。"""

from typing import Any, Optional


class ResponseModel:
    """统一响应格式。"""
    def __init__(self, code: int = 0, data: Any = None, message: str = ""):
        self.code = code
        self.data = data or {}
        self.message = message
    
    def __dict__(self):
        return {
            "code": self.code,
            "data": self.data,
            "message": self.message,
        }
    
    def model_dump(self):
        return self.__dict__()
