"""百度网盘开放平台客户端 —— 取数校验用（BP-1）。

⚠️ **为什么单独一个文件**
百度网盘的 OAuth 与飞书**完全不同**：
- 飞书用 ``tenant_access_token``（应用身份，30 分钟）
- 百度用 ``access_token`` + ``refresh_token``（**用户**身份，30 天 / 10 年）

而且百度有两个**极容易踩**的坑，都在本文件里处理了：

    坑 1  ``refresh_token`` **只能用一次**
         刷新后必须用响应里返回的**新** refresh_token 覆盖旧的，
         否则下次刷新必然失败（错误码 110）。

    坑 2  递归接口 ``multimedia?method=listall`` **只能访问
         ``/apps/{appname}``**（2026-06-03 后创建的应用）。
         而 ``file?method=list`` 不受限但**不递归**。

⇒ 所以本文件的 ``list_files_recursive`` 走 ``multimedia``，
   路径必须落在 ``/apps/`` 下。

⚠️ **本文件不联网** —— 由 ``test_baidu_pan.py`` 用假 transport 覆盖。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

#: 百度 OAuth 授权端点。
OAUTH_AUTHORIZE_URL = "https://openapi.baidu.com/oauth/2.0/authorize"
#: 百度 OAuth 换 token / 刷新 token 端点。
OAUTH_TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"
#: 网盘开放接口基址。
XPAN_BASE = "https://pan.baidu.com/rest/2.0/xpan"

#: 授权时**固定**的 scope —— 官方明确要求，不多不少。
OAUTH_SCOPE = "basic,netdisk"

#: ``oob`` 模式 —— 不需要公网域名，授权页直接显示授权码。
REDIRECT_URI_OOB = "oob"

#: 递归下钻的最大深度。
#: ⚠️ 业务目录形如 ``/成品-小瑶/10月/10.31`` —— 3 层就够。
#: 深度越大 = 请求数越多（百度未审核应用限 10 次/小时）。
DEFAULT_MAX_DEPTH = 4

#: 单次列表接口翻页的安全上限（防止死循环）。
max_files_guard = 100000


class BaiduPanError(Exception):
    """百度网盘接口错误。

    ⚠️ **业务错误码不在 HTTP 状态码里** —— 百度一律返回 200，
    错误藏在响应体的 ``errno`` 字段。必须检查 ``errno``，
    只看 HTTP 状态码会把失败当成功。
    """

    def __init__(self, message: str, *, errno: int = 0, path: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.errno = errno
        self.path = path


class BaiduPanAuthError(BaiduPanError):
    """授权 / token 相关错误（需要重新走授权流程）。"""


#: 错误码 → 人话。用于把百度那串数字翻译成可读原因。
ERRNO_MESSAGES: dict[int, str] = {
    -7: "文件或目录无权访问（目录不在授权范围内？）",
    -9: "文件或目录不存在",
    111: "参数错误",
    110: "refresh_token 无效或已被使用，请重新授权",
    20011: "超过应用最大可使用用户数",
    20012: "接口调用超出频率或次数限制",
    20013: "没有该接口的访问权限（应用未通过上线审核？）",
    31034: "命中频控，请退避后重试",
    31066: "文件不存在",
    42213: "没有共享目录的权限（授权时需勾选该目录）",
}


@dataclass(frozen=True)
class TokenSet:
    """一组 token。"""

    access_token: str
    refresh_token: str
    expires_in: int
    scope: str = ""

    @property
    def expires_at(self) -> float:
        return time.time() + max(0, self.expires_in - 300)


@dataclass(frozen=True)
class PanFile:
    """网盘里的一个文件。"""

    path: str
    name: str
    is_dir: bool
    size: int
    ctime: int
    mtime: int = 0
    category: int = 0
    fs_id: int = 0
    #: 网盘返回的云端哈希（**非文件真实 MD5**），用于内容去重锚点。
    #: ⚠️ 实测（2026-11-21）：同名文件的 md5 各不相同 ⇒ 是不同内容，
    #:    正是靠它区分「同一文件重复上传」与「不同版本」。
    md5: str = ""
    #: 操作者用户 ID。⚠️ **共享目录下无区分度**（实测 232/232 全是公司账号 uk），
    #:    不可用于拆人，仅作审计留痕。
    oper_id: str = ""

    @property
    def ext(self) -> str:
        """小写扩展名（不含点）。"""
        _, _, suffix = self.name.rpartition(".")
        return suffix.lower() if suffix and suffix != self.name else ""


class BaiduPanClient:
    """百度网盘开放平台客户端。

    ⚠️ **token 由调用方持有** —— 本类**不存** token，
    只负责「用 token 调接口」和「用 code/refresh 换 token」。
    存储与刷新调度由上层（``baidu_pan_service``）负责。
    """

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        redirect_uri: str = REDIRECT_URI_OOB,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri or REDIRECT_URI_OOB
        self._http = http_client or httpx.Client(
            timeout=30.0, follow_redirects=False, trust_env=False
        )
        self._owns_http = http_client is None

    def close(self) -> None:
        if self._owns_http:
            try:
                self._http.close()
            except Exception:  # pragma: no cover
                pass

    # ─────────────────────────────────────────────────────────────
    # OAuth
    # ─────────────────────────────────────────────────────────────

    def build_authorize_url(self, *, state: str = "", display: str = "tv") -> str:
        """拼授权页地址 —— 让用户去勾选网盘目录。

        ⚠️ **目录是在这个页面勾选的** —— 勾了哪些，
        应用之后就能读哪些（会挂到 ``/apps/{appname}/`` 下）。
        这是「自动拉取」的唯一入口。
        """
        params = {
            "response_type": "code",
            "client_id": self.app_key,
            "redirect_uri": self.redirect_uri,
            "scope": OAUTH_SCOPE,
            "display": display,
        }
        if state:
            params["state"] = state
        return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> TokenSet:
        """用授权码换 token。

        ⚠️ **code 只能用一次，10 分钟过期** —— 失败不要重试，要重新授权。
        """
        params = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.app_key,
            "client_secret": self.app_secret,
            "redirect_uri": self.redirect_uri,
        }
        data = self._oauth_request(params)
        if "error" in data:
            raise BaiduPanAuthError(
                f"百度授权失败：{data.get('error')} {data.get('error_description', '')}".strip()
            )
        return self._parse_token(data)

    def refresh(self, refresh_token: str) -> TokenSet:
        """用 refresh_token 换新 token。

        ⚠️⚠️ **百度官方明确：refresh_token 只支持使用一次**。
        用掉之后必须**用响应里返回的新 refresh_token** 覆盖旧的，
        否则下次刷新会报 ``110 invalid refresh_token``。

        ⇒ 调用方**必须**把返回的新 refresh_token 存起来。
        """
        params = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.app_key,
            "client_secret": self.app_secret,
        }
        data = self._oauth_request(params)
        if "error" in data:
            raise BaiduPanAuthError(
                f"百度 token 刷新失败：{data.get('error')} "
                f"{data.get('error_description', '')}".strip()
            )
        return self._parse_token(data)

    def _oauth_request(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            resp = self._http.get(OAUTH_TOKEN_URL, params=params)
        except httpx.HTTPError as exc:
            raise BaiduPanAuthError(f"请求百度 OAuth 接口失败：{exc}") from exc
        try:
            return resp.json()
        except ValueError as exc:
            raise BaiduPanAuthError(
                f"百度 OAuth 接口返回非 JSON（HTTP {resp.status_code}）"
            ) from exc

    @staticmethod
    def _parse_token(data: dict[str, Any]) -> TokenSet:
        access = str(data.get("access_token") or "")
        if not access:
            raise BaiduPanAuthError("百度未返回 access_token")
        return TokenSet(
            access_token=access,
            # ⚠️ 刷新响应**可能不含**新 refresh_token —— 这种情况要沿用旧的。
            refresh_token=str(data.get("refresh_token") or ""),
            expires_in=int(data.get("expires_in") or 0),
            scope=str(data.get("scope") or ""),
        )

    # ─────────────────────────────────────────────────────────────
    # 文件列举
    # ─────────────────────────────────────────────────────────────

    def list_files_recursive(
        self,
        access_token: str,
        path: str,
        *,
        limit: int = 1000,
        max_pages: int = 200,
    ) -> list[PanFile]:
        """递归列出目录下的所有文件。

        ⚠️⚠️ **为什么自己下钻而不用 ``multimedia?method=listall``**
        ------------------------------------------------------------------
        百度官方说 ``listall`` 只允许访问 ``/apps/{appname}``，
        **但实测（2026-11-20）**：

            - 用户授权后，``file?method=list`` **能读用户网盘的任意授权目录**
              （含 ``/成品-小瑶`` 这种业务目录）
            - 而 ``multimedia?method=listall`` 反而**读不了**这些路径
              （报 ``20020 该路径不在允许的访问范围内``）

        ⇒ **唯一可行的递归方式就是自己用 ``file?method=list`` 逐层下钻。**
        官方那条 ``/apps`` 限制只对那几个"文件操作类"接口生效，
        对"列表类"接口的实际约束比文档严。

        ⚠️ **代价：请求数 = 目录数**。所以：
        - 目录很多时要注意频控（百度未审核应用 10 次/小时）
        - 用 ``max_depth`` 限制下钻深度
        """
        files: list[PanFile] = []
        self._walk(access_token, path, files, depth=0, max_depth=DEFAULT_MAX_DEPTH)
        return files

    def _walk(
        self,
        access_token: str,
        path: str,
        out: list[PanFile],
        *,
        depth: int,
        max_depth: int,
    ) -> None:
        """自建递归 —— 逐层列目录，遇到文件就收进 ``out``。"""
        if depth > max_depth:
            return
        try:
            items = self.list_dir(access_token, path)
        except BaiduPanError as exc:
            # ⚠️ **根目录读不到 ⇒ 整轮失败**（配置错了或没授权）——
            #    必须让它冒泡，否则「扫了 0 个文件」会被当成「目录是空的」。
            if depth == 0:
                raise
            # ⚠️ **子目录读不到不该让整轮失败** ——
            #    记录后继续（可能是权限外目录或已删除）。
            logger.warning("百度网盘目录读取失败 %s: %s", path, exc.message)
            return
        for item in items:
            if item.is_dir:
                self._walk(access_token, item.path, out, depth=depth + 1, max_depth=max_depth)
            else:
                out.append(item)

    def _listall_page(
        self, access_token: str, path: str, *, start: int, limit: int
    ) -> dict[str, Any]:
        params = {
            "method": "listall",
            "access_token": access_token,
            "path": path,
            "recursion": 1,
            "order": "time",
            "desc": 1,
            "start": start,
            "limit": limit,
        }
        data = self._xpan_request("/multimedia", params=params, path=path)
        raw = data.get("list") or []
        files: list[PanFile] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("server_filename") or "")
            files.append(
                PanFile(
                    path=str(item.get("path") or ""),
                    name=name,
                    is_dir=bool(int(item.get("isdir") or 0)),
                    size=int(item.get("size") or 0),
                    ctime=int(item.get("server_ctime") or 0),
                    mtime=int(item.get("server_mtime") or 0),
                    category=int(item.get("category") or 0),
                    fs_id=int(item.get("fs_id") or 0),
                    md5=str(item.get("md5") or ""),
                    oper_id=str(item.get("oper_id") or ""),
                )
            )
        return {
            "files": files,
            "has_more": bool(int(data.get("has_more") or 0)),
            # ⚠️ 官方要求用 cursor，不要自己累加。
            "cursor": int(data["cursor"]) if data.get("cursor") is not None else None,
        }

    def list_dir(
        self, access_token: str, dir_path: str, *, limit: int = 1000
    ) -> list[PanFile]:
        """列出**当前层**目录（不递归）。

        ⚠️ ``file?method=list`` **不受** ``/apps`` 限制 ——
        但也**不递归**，只返回这一层。

        ⚠️ **分页**：这个接口用 ``start`` + ``limit`` 翻页，
        返回里没有 ``has_more``，靠**取满 limit 条**判断还有没有下一页。
        """
        files: list[PanFile] = []
        start = 0
        while True:
            page = self._list_dir_page(access_token, dir_path, start=start, limit=limit)
            files.extend(page)
            if len(page) < limit:
                break
            start += len(page)
            if start >= max_files_guard:
                break
        return files

    def _list_dir_page(
        self, access_token: str, dir_path: str, *, start: int, limit: int
    ) -> list[PanFile]:
        params = {
            "method": "list",
            "access_token": access_token,
            "dir": dir_path,
            "order": "time",
            "desc": 1,
            "start": start,
            "limit": limit,
            "folder": 0,
        }
        data = self._xpan_request("/file", params=params, path=dir_path)
        files: list[PanFile] = []
        for item in data.get("list") or []:
            if not isinstance(item, dict):
                continue
            files.append(
                PanFile(
                    path=str(item.get("path") or ""),
                    name=str(item.get("server_filename") or ""),
                    is_dir=bool(int(item.get("isdir") or 0)),
                    size=int(item.get("size") or 0),
                    ctime=int(item.get("server_ctime") or 0),
                    mtime=int(item.get("server_mtime") or 0),
                    category=int(item.get("category") or 0),
                    fs_id=int(item.get("fs_id") or 0),
                    md5=str(item.get("md5") or ""),
                    oper_id=str(item.get("oper_id") or ""),
                )
            )
        return files

    def _xpan_request(
        self, endpoint: str, *, params: dict[str, Any], path: str
    ) -> dict[str, Any]:
        url = f"{XPAN_BASE}{endpoint}"
        try:
            resp = self._http.get(url, params=params)
        except httpx.HTTPError as exc:
            raise BaiduPanError(f"请求百度网盘接口失败：{exc}", path=path) from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduPanError(
                f"百度网盘接口返回非 JSON（HTTP {resp.status_code}）", path=path
            ) from exc
        errno = int(data.get("errno") or 0)
        if errno != 0:
            reason = ERRNO_MESSAGES.get(errno, data.get("show_msg") or "")
            raise BaiduPanError(
                f"百度网盘接口报错 {errno}：{reason}", errno=errno, path=path
            )
        return data


# ═══════════════════════════════════════════════════════════════════════
# 计数规则
# ═══════════════════════════════════════════════════════════════════════


def count_by_day(
    files: list[PanFile],
    *,
    extensions: str = "",
    exclude_keywords: str = "",
) -> dict[str, int]:
    """按天统计文件数。

    Args:
        files: 网盘文件列表。
        extensions: 只统计这些扩展名（逗号分隔，小写）。留空统计全部。
        exclude_keywords: 文件名含这些关键词的**不计入**（逗号分隔）。

    Returns:
        ``{"YYYY-MM-DD": 数量}``。按 ``server_ctime``（**上传时间**）归日。
    """
    allowed = {x.strip().lower() for x in (extensions or "").split(",") if x.strip()}
    excluded = [x.strip().lower() for x in (exclude_keywords or "").split(",") if x.strip()]

    counts: dict[str, int] = {}
    for item in files:
        if item.is_dir:
            continue
        if allowed and item.ext not in allowed:
            continue
        name = item.name.lower()
        if any(keyword in name for keyword in excluded):
            continue
        day = business_day(item)
        if not day:
            continue
        counts[day] = counts.get(day, 0) + 1
    return counts


# ═══════════════════════════════════════════════════════════════════════
# 业务日期解析
# ═══════════════════════════════════════════════════════════════════════


#: 目录名 → 月份。支持 ``10月`` / ``2026年11月`` / ``2026-11``。
_MONTH_PATTERNS = (
    re.compile(r"^(\d{4})年(\d{1,2})月$"),
    re.compile(r"^(\d{4})[-_](\d{1,2})$"),
    re.compile(r"^(\d{1,2})月$"),
)

#: 目录名 → 日。支持 ``11.19`` / ``11-19`` / ``11_19`` / ``2026.11.19``。
_DAY_PATTERNS = (
    re.compile(r"^(\d{4})[.\-_](\d{1,2})[.\-_](\d{1,2})$"),
    re.compile(r"^(\d{1,2})[.\-_](\d{1,2})$"),
)


def parse_period_from_path(path: str, *, default_year: int = 0) -> tuple[int, int, int]:
    """从**路径里的目录名**解析出 (年, 月, 日)。

    ⚠️⚠️ **为什么必须用目录名，不能用文件上传时间**
    ------------------------------------------------------
    实测（2026-11-20，用户网盘 ``/成品-小瑶/11月/11.19``）：

        ``11.19`` 目录里的文件，上传时间是 **2026-11-20**；
        ``11.5``  目录里的文件，上传时间是 **2026-11-06**。

    ⇒ **上传时间 ≠ 制作日期**（往往晚一天甚至更久）。

    若按 ``server_ctime`` 归日，整个统计会**整体错位** ——
    11.19 做的片子会被算到 11.20 头上。

    ⇒ 业务日期必须从**目录名**（``11月`` / ``11.19``）解析。

    Args:
        path: 文件的完整路径，如 ``/成品-小瑶/11月/11.19/xxx.mp4``。
        default_year: 路径里没有年份时用它补全（默认 0 = 用当前年）。

    Returns:
        ``(year, month, day)``。解析不出就返回 ``(0, 0, 0)``。
    """
    parts = [p for p in (path or "").split("/") if p]
    year = 0
    month = 0
    day = 0

    # 先扫一遍：找月份目录与日期目录
    for part in parts:
        for pattern in _MONTH_PATTERNS:
            match = pattern.match(part)
            if match:
                if len(match.groups()) == 2:
                    year = int(match.group(1))
                    month = int(match.group(2))
                else:
                    month = int(match.group(1))
                break
        for pattern in _DAY_PATTERNS:
            match = pattern.match(part)
            if match:
                if len(match.groups()) == 3:
                    # ``2026.11.19`` —— 年月日俱全
                    year = int(match.group(1))
                    month = int(match.group(2))
                    day = int(match.group(3))
                else:
                    # ⚠️ ``11.19`` 是「11 月 19 日」—— 中文习惯是**月在前**。
                    #   （写成 day=group1 会把 11.19 读成 11 月 11 日。）
                    month = int(match.group(1))
                    day = int(match.group(2))
                break

    if not month:
        return (0, 0, 0)
    if not year:
        year = default_year or time.localtime().tm_year
    if not day:
        # 只有月份 —— 用当月最后一天？**不**，用 1 号更安全：
        # 归到「当月」这个粒度上，不假装是某一天。
        day = 1
    return (year, month, day)


def business_day(item: PanFile, *, default_year: int = 0) -> str:
    """取文件的**业务日期**（YYYY-MM-DD）。

    优先从路径目录名解析；解析不出才退回上传时间。

    ⚠️ 退回上传时间是**降级** —— 实测里它比业务日期晚，
    统计会错位。所以调用方应该优先保证目录名规范。
    """
    year, month, day = parse_period_from_path(item.path, default_year=default_year)
    if year and month and day:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    if item.ctime > 0:
        return time.strftime("%Y-%m-%d", time.localtime(item.ctime))
    return ""


def sum_range(counts: dict[str, int], start: str, end: str) -> int:
    """把 ``[start, end]`` 区间（YYYY-MM-DD，**含端点**）的日计数加总。"""
    total = 0
    for day, value in counts.items():
        if start <= day <= end:
            total += value
    return total
