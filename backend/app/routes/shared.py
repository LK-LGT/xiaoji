"""共享的响应模型与请求体基类。"""

from typing import Any

from pydantic import BaseModel


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


class JsonBody(BaseModel):
    """宽松 JSON 请求体基类。

    ⚠️ 为什么不用 `data: dict`：
    本项目所用的 FastAPI 0.162 / Pydantic 2.14 组合**不会**把裸 `dict`
    注解自动解析为 JSON body —— 请求体会以字符串形式传入，触发 422
    （`dict_type` / `model_attributes_type` 校验失败）。
    必须显式声明 Pydantic 模型，或继承本类。

    用法::

        @router.post("/xxx")
        async def xxx(payload: JsonBody, ...):
            name = payload.get("name", "")
    """

    model_config = {"extra": "allow"}

    def get(self, key: str, default: Any = None) -> Any:
        """按 key 取值，兼容 dict 与对象两种写法。"""
        value = getattr(self, key, None)
        if value is None and key in self.model_extra:
            value = self.model_extra[key]
        return default if value is None else value

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def get_extra(self, key: str, default: Any = None) -> Any:
        """只读取未声明的多余字段。"""
        extra = self.model_extra or {}
        value = extra.get(key, default)
        return default if value is None else value
