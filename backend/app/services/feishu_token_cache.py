"""飞书 tenant_access_token 的**进程级共享缓存**（FS-7）。

⚠️ **为什么需要这一层**
------------------------------------
``FeishuClient`` 内部**已经**有 token 缓存
（``_access_token`` + ``_token_expires_at`` + 提前刷新），
但那个缓存是**实例级**的 —— 而项目里每个调用点都是
``FeishuClient(app_id=..., app_secret=...)`` **现建现用**：

    routes/feishu_bot.py        _client()          每个请求一个实例
    routes/feishu_auth.py       _client()          每个请求一个实例
    routes/feishu_approval.py   _client()          每个请求一个实例
    services/feishu_daily_push.py                  每次推送一个实例
    services/feishu_metric_sync.py                 每次同步一个实例

⇒ **实例级缓存等于没有缓存** —— 每个 API 调用都要先打一次
   ``/auth/v3/tenant_access_token/internal``。

⚠️ **代价不只是慢**
------------------------------------
飞书对 token 接口有频控。审批回流 + 组织同步 + 每日推送
叠加起来，一次业务动作可能连带打多次 token 接口 ⇒ 撞限流 ⇒
整条链路失败。**这正是 FS-7「token 缓存」要解决的问题。**

⚠️ **本模块只缓存「租户态」token**
------------------------------------
三类 token 语义完全不同，**绝不混用**（见 ``feishu_client.py`` RK3 说明）：

======================  ==========================================
token 类型              缓存策略
======================  ==========================================
tenant_access_token     ✅ 本模块缓存（进程级、应用身份）
app_access_token        ❌ 不缓存（换取 user token，用完即弃）
user_access_token       ❌ 不缓存（**属于某个用户**，权限随人变）
======================  ==========================================

⚠️ **安全：按凭据分槽**
------------------------------------
不同 ``app_id``/``app_secret`` 的 token **绝不共享** ——
否则 A 应用的 token 会被发到 B 应用去（跨应用越权）。
⇒ 槽位 key = ``(app_id, app_secret)`` 的哈希。

⚠️ **单飞（single-flight）**
------------------------------------
并发时多个线程可能同时发现「token 过期」⇒ 一起去拉 token。
飞书对同一应用的 token 接口并发拉取会返回**同一个 token**
（幂等），但仍会白白消耗频控配额。
⇒ 用 per-key 锁保证**同一时刻只有一个线程**在拉。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

#: 提前刷新余量（秒）—— 避免 token 在请求途中过期。
#:
#: ⚠️ 与 ``FeishuClient`` 实例内缓存保持同一余量，
#:    否则共享层给出的 token 可能比调用方预期的更早过期。
_REFRESH_MARGIN_SECONDS = 300

#: 本地 TTL 的**下限**（秒）—— 短命 token 也要留一点点操作余量。
_MIN_TTL_SECONDS = 30


@dataclass
class _TokenSlot:
    """一个应用凭据对应的缓存槽位。"""

    token: str | None = None
    expires_at: datetime | None = None


class TenantTokenCache:
    """进程级 tenant_access_token 缓存。

    ⚠️ **线程安全** —— 后端是多线程（FastAPI 的同步端点跑在线程池里），
    多个线程可能同时刷新同一个槽位。
    """

    def __init__(self) -> None:
        self._slots: dict[str, _TokenSlot] = {}
        #: 全局锁 —— 保护 ``_slots`` 字典本身的读写。
        self._lock = threading.Lock()
        #: per-key 锁 —— 保证同一应用**同时只有一个线程**在拉 token。
        self._key_locks: dict[str, threading.Lock] = {}

    @staticmethod
    def make_key(app_id: str, app_secret: str) -> str:
        """生成槽位 key。

        ⚠️ **用 app_secret 的哈希而不是明文** ——
        否则这个 key 会随进程内存一起被 dump 出来，
        等于把密钥抄了一遍。
        """
        digest = hashlib.sha256(
            f"{app_id}\x00{app_secret}".encode()
        ).hexdigest()
        return f"{app_id}:{digest[:16]}"

    def _get_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def peek(self, app_id: str, app_secret: str) -> str | None:
        """**只读**地取当前可用 token，不触发刷新。

        用途：调用方想在发请求前先看一眼，避免不必要的刷新。
        """
        key = self.make_key(app_id, app_secret)
        with self._lock:
            slot = self._slots.get(key)
            if slot is None or not slot.token or not slot.expires_at:
                return None
            if datetime.now(UTC) >= slot.expires_at:
                return None
            return slot.token

    def get(
        self,
        app_id: str,
        app_secret: str,
        fetch: Callable[[], tuple[str, int]],
    ) -> str:
        """取 token，必要时刷新。

        :param fetch: 无参可调用对象，返回 ``(token, expire_seconds)``。
            ⚠️ 只有**缓存未命中**时才会被调用。
        :raises Exception: ``fetch`` 抛出的异常会**原样向上传播** ——
            本模块不吞异常（缓存层不该改变错误语义）。
        """
        key = self.make_key(app_id, app_secret)
        now = datetime.now(UTC)

        # 快路径：命中就直接返回，不加锁（读多写少场景的常见优化）。
        with self._lock:
            slot = self._slots.get(key)
            if (
                slot is not None
                and slot.token
                and slot.expires_at
                and now < slot.expires_at
            ):
                return slot.token

        # 慢路径：加 per-key 锁，保证同一应用同时只有一个线程在拉。
        with self._get_lock(key):
            # ⚠️ **双检** —— 等锁期间可能已被别人刷新好了。
            now = datetime.now(UTC)
            with self._lock:
                slot = self._slots.get(key)
                if (
                    slot is not None
                    and slot.token
                    and slot.expires_at
                    and now < slot.expires_at
                ):
                    return slot.token

            token, expire_seconds = fetch()
            expires_at = datetime.now(UTC) + timedelta(
                seconds=float(
                    max(_MIN_TTL_SECONDS, expire_seconds - _REFRESH_MARGIN_SECONDS)
                )
            )
            with self._lock:
                slot = self._slots.get(key)
                if slot is None:
                    slot = _TokenSlot()
                    self._slots[key] = slot
                slot.token = token
                slot.expires_at = expires_at
            logger.info(
                "飞书 tenant token 已刷新（进程级缓存），有效期秒数=%s",
                expire_seconds,
            )
            return token

    def invalidate(self, app_id: str, app_secret: str) -> None:
        """作废缓存槽位。

        用途：收到飞书的 token 失效错误码时，强制下次重新拉。
        """
        key = self.make_key(app_id, app_secret)
        with self._lock:
            slot = self._slots.get(key)
            if slot is not None:
                slot.token = None
                slot.expires_at = None

    def clear(self) -> None:
        """清空全部缓存（测试用）。"""
        with self._lock:
            self._slots.clear()


#: 进程级单例 —— 整个后端进程共用一份。
#:
#: ⚠️ **为什么是模块级单例**
#:    token 的有效期以**小时**计，跨请求复用才有意义。
#:    做成 per-client 实例的话，就退化成 ``FeishuClient`` 里已有的
#:    实例级缓存 —— 形同虚设。
tenant_token_cache = TenantTokenCache()


__all__ = ["TenantTokenCache", "tenant_token_cache"]
