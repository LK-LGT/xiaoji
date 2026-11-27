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

    def has(self, key: str) -> bool:
        """判断字段**是否存在**（含未声明的多余字段）。

        ⚠️ **为什么不能用 `key in payload`**
        ``JsonBody`` 继承自 Pydantic ``BaseModel``，而 ``BaseModel``
        **没有** ``__contains__`` ⇒ ``in`` 会退化成对 ``__iter__``
        （产出 ``(key, value)`` 二元组）的成员判断
        ⇒ **恒为 False** ⇒ 依赖它做「字段是否提交」的分支
        会**永远走 else 分支**。

        ⚠️ 这不是理论问题：``routes/v1/system_users.py`` 的
        ``update_user`` 曾用 ``if "deptId" in payload`` 判断是否改部门
        ⇒ **部门/直属负责人/角色永远改不了**，前端却弹「已保存」。
        """
        if key in (self.model_fields or {}):
            return True
        return key in (self.model_extra or {})

    def get_extra(self, key: str, default: Any = None) -> Any:
        """只读取未声明的多余字段。"""
        extra = self.model_extra or {}
        value = extra.get(key, default)
        return default if value is None else value
