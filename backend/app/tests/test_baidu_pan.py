"""百度网盘绩效校验 —— 验收测试（BP-1）。

覆盖
----
1. OAuth URL 拼装（scope 固定、redirect_uri）
2. 授权码换 token（**refresh_token 缺失时沿用旧的**）
3. ⚠️ **refresh_token 刷新后必须换新**（百度只用一次）
4. 文件列举分页（**必须用响应里的 cursor**，不能自己累加）
5. 错误码翻译（errno ≠ 0 必须抛错，不能当成功）
6. 计数规则（扩展名过滤 + 关键词排除 + 按天归集）
7. 窗口汇总（**默认上个自然月** —— 当月不完整）
8. 计数快照幂等（同一窗口覆盖，不重复计数）
9. 比对逻辑（**只提示不判定**）
10. **不自动改分** —— 比对结果里没有分数字段

⚠️ **本文件不联网** —— 百度交互全部用假 transport 模拟。
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.baidu_pan_models import (
    BaiduPanCount,
    BaiduPanSyncBatch,
    BaiduPanToken,
)
from backend.app.domain.models import Base, Department
from backend.app.services.baidu_pan_client import (
    BaiduPanAuthError,
    BaiduPanClient,
    BaiduPanError,
    PanFile,
    business_day,
    count_by_day,
    parse_period_from_path,
    sum_range,
)
from backend.app.services.baidu_pan_service import (
    BaiduPanService,
    compare_with_report,
)


# ─────────────────────────────────────────────────────────────────────────
# 假 transport
# ─────────────────────────────────────────────────────────────────────────


def make_transport(responses: list[dict | httpx.Response]) -> httpx.MockTransport:
    """按顺序返回预设响应。"""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            raise AssertionError(f"意外的第 {len(queue) + 1} 次请求：{request.url}")
        item = queue.pop(0)
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)

    return httpx.MockTransport(handler)


def make_client(responses: list[dict | httpx.Response]) -> BaiduPanClient:
    return BaiduPanClient(
        app_key="AK",
        app_secret="SK",
        redirect_uri="oob",
        http_client=httpx.Client(transport=make_transport(responses)),
    )


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Department(id="D", name="短视频"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────────────────
# 1. OAuth URL
# ─────────────────────────────────────────────────────────────────────────


def test_authorize_url_uses_fixed_scope():
    """⚠️ **scope 必须固定为 ``basic,netdisk``** —— 官方明确要求。"""
    client = make_client([])
    url = client.build_authorize_url()
    assert "response_type=code" in url
    assert "client_id=AK" in url
    assert "scope=basic%2Cnetdisk" in url
    assert "redirect_uri=oob" in url


def test_authorize_url_includes_state_when_given():
    client = make_client([])
    url = client.build_authorize_url(state="abc123")
    assert "state=abc123" in url


# ─────────────────────────────────────────────────────────────────────────
# 2. 授权码换 token
# ─────────────────────────────────────────────────────────────────────────


def test_exchange_code_returns_tokens():
    client = make_client(
        [
            {
                "access_token": "AT1",
                "refresh_token": "RT1",
                "expires_in": 2592000,
                "scope": "basic netdisk",
            }
        ]
    )
    tokens = client.exchange_code("CODE")
    assert tokens.access_token == "AT1"
    assert tokens.refresh_token == "RT1"
    assert tokens.scope == "basic netdisk"


def test_exchange_code_error_raises():
    client = make_client([{"error": "invalid_grant", "error_description": "过期"}])
    with pytest.raises(BaiduPanAuthError) as exc:
        client.exchange_code("BAD")
    assert "invalid_grant" in str(exc.value)


def test_exchange_code_without_access_token_raises():
    """百度没给 access_token ⇒ 报错，不能返回一个空 token 蒙混过关。"""
    client = make_client([{"refresh_token": "RT1"}])
    with pytest.raises(BaiduPanAuthError):
        client.exchange_code("CODE")


# ─────────────────────────────────────────────────────────────────────────
# 3. refresh_token 轮换（百度只用一次）
# ─────────────────────────────────────────────────────────────────────────


def test_refresh_returns_new_refresh_token():
    """⚠️ 刷新响应里的**新** refresh_token 必须能被拿到。"""
    client = make_client(
        [{"access_token": "AT2", "refresh_token": "RT2", "expires_in": 2592000}]
    )
    tokens = client.refresh("RT1")
    assert tokens.access_token == "AT2"
    assert tokens.refresh_token == "RT2"


def test_refresh_without_new_token_keeps_empty():
    """⚠️ 刷新响应**不含** refresh_token 时返回空串 —— 由上层沿用旧的。

    ⇒ 百度有的版本会返回新的、有的不会。上层必须判断：
    ``if tokens.refresh_token: 覆盖``。
    """
    client = make_client([{"access_token": "AT2", "expires_in": 2592000}])
    tokens = client.refresh("RT1")
    assert tokens.refresh_token == ""


def test_refresh_error_raises_auth_error():
    client = make_client([{"error": "invalid_grant"}])
    with pytest.raises(BaiduPanAuthError):
        client.refresh("DEAD")


# ─────────────────────────────────────────────────────────────────────────
# 4. 文件列举（cursor 分页）
# ─────────────────────────────────────────────────────────────────────────


def test_list_files_recursive_reads_root_dir():
    """根目录一次读到文件。"""
    client = make_client(
        [
            {
                "errno": 0,
                "list": [
                    {
                        "server_filename": "a.mp4",
                        "isdir": 0,
                        "path": "/x/a.mp4",
                        "server_ctime": 100,
                    },
                ],
            }
        ]
    )
    files = client.list_files_recursive("AT", "/x")
    assert [f.name for f in files] == ["a.mp4"]


def test_list_files_recursive_walks_into_subdirs():
    """⚠️ **自建递归** —— 逐层下钻收文件（实测 listall 读不了业务目录）。"""
    client = make_client(
        [
            # 第一层：目录
            {
                "errno": 0,
                "list": [
                    {"server_filename": "11月", "isdir": 1, "path": "/成品-小瑶/11月"},
                ],
            },
            # 第二层：日期目录
            {
                "errno": 0,
                "list": [
                    {
                        "server_filename": "11.19",
                        "isdir": 1,
                        "path": "/成品-小瑶/11月/11.19",
                    },
                ],
            },
            # 第三层：文件
            {
                "errno": 0,
                "list": [
                    {
                        "server_filename": "a.mp4",
                        "isdir": 0,
                        "path": "/成品-小瑶/11月/11.19/a.mp4",
                        "server_ctime": 100,
                    },
                ],
            },
        ]
    )
    files = client.list_files_recursive("AT", "/成品-小瑶")
    assert len(files) == 1
    assert files[0].name == "a.mp4"
    assert files[0].path == "/成品-小瑶/11月/11.19/a.mp4"


def test_list_files_recursive_survives_unreadable_subdir():
    """⚠️ **单个目录读不到不该让整轮失败** —— 记录后继续。"""
    client = make_client(
        [
            {
                "errno": 0,
                "list": [
                    {"server_filename": "坏目录", "isdir": 1, "path": "/x/坏目录"},
                    {
                        "server_filename": "好文件.mp4",
                        "isdir": 0,
                        "path": "/x/好文件.mp4",
                        "server_ctime": 100,
                    },
                ],
            },
            {"errno": 20020, "show_msg": "不在允许范围内"},
        ]
    )
    files = client.list_files_recursive("AT", "/x")
    assert [f.name for f in files] == ["好文件.mp4"]


def test_list_files_recursive_stops_on_has_more_zero():
    client = make_client(
        [{"errno": 0, "has_more": 0, "list": []}]
    )
    assert client.list_files_recursive("AT", "/apps/x") == []


def test_list_files_recursive_raises_on_errno():
    """⚠️ **errno ≠ 0 必须抛错** —— 百度一律返回 HTTP 200。

    只看 HTTP 状态码会把失败当成功 ⇒ 数据静默为空。
    """
    client = make_client([{"errno": 42213, "show_msg": "没有共享目录的权限"}])
    with pytest.raises(BaiduPanError) as exc:
        client.list_files_recursive("AT", "/apps/x")
    assert exc.value.errno == 42213
    assert "共享目录" in str(exc.value)


def test_list_files_recursive_reports_permission_error():
    client = make_client([{"errno": -7}])
    with pytest.raises(BaiduPanError) as exc:
        client.list_files_recursive("AT", "/apps/x")
    assert "无权访问" in str(exc.value)


# ─────────────────────────────────────────────────────────────────────────
# 5. 计数规则
# ─────────────────────────────────────────────────────────────────────────


def _file(name: str, ctime: int) -> PanFile:
    return PanFile(
        path=f"/apps/x/{name}",
        name=name,
        is_dir=False,
        size=100,
        ctime=ctime,
        mtime=ctime,
    )


def test_count_by_day_groups_by_ctime():
    day1 = int(time.mktime(time.strptime("2026-11-01", "%Y-%m-%d")))
    day2 = int(time.mktime(time.strptime("2026-11-02", "%Y-%m-%d")))
    files = [_file("a.mp4", day1), _file("b.mp4", day1), _file("c.mp4", day2)]
    counts = count_by_day(files, extensions="mp4")
    assert counts == {"2026-11-01": 2, "2026-11-02": 1}


def test_count_by_day_filters_extension():
    day1 = int(time.mktime(time.strptime("2026-11-01", "%Y-%m-%d")))
    files = [_file("a.mp4", day1), _file("b.txt", day1), _file("c.mov", day1)]
    counts = count_by_day(files, extensions="mp4,mov")
    assert counts == {"2026-11-01": 2}


def test_count_by_day_excludes_keywords():
    """⚠️ 「返工」「_v2」这类文件**不计入** —— 业务规则。"""
    day1 = int(time.mktime(time.strptime("2026-11-01", "%Y-%m-%d")))
    files = [
        _file("正常.mp4", day1),
        _file("返工.mp4", day1),
        _file("xxx_v2.mp4", day1),
        _file("废弃.mp4", day1),
    ]
    counts = count_by_day(files, extensions="mp4", exclude_keywords="返工,_v2,废弃")
    assert counts == {"2026-11-01": 1}


def test_count_by_day_skips_directories():
    day1 = int(time.mktime(time.strptime("2026-11-01", "%Y-%m-%d")))
    folder = PanFile(
        path="/apps/x/子目录",
        name="子目录",
        is_dir=True,
        size=0,
        ctime=day1,
        mtime=day1,
    )
    assert count_by_day([folder], extensions="mp4") == {}


def test_sum_range_inclusive():
    counts = {"2026-10-31": 5, "2026-11-01": 3, "2026-11-02": 4, "2026-11-03": 9}
    assert sum_range(counts, "2026-11-01", "2026-11-02") == 7


# ─────────────────────────────────────────────────────────────────────────
# 5b. 业务日期解析（⚠️ 最关键的业务逻辑）
# ─────────────────────────────────────────────────────────────────────────


def test_parse_period_from_path_month_day():
    """``/成品-小瑶/11月/11.19/xxx.mp4`` → 2026-11-19。"""
    year, month, day = parse_period_from_path(
        "/成品-小瑶/11月/11.19/xxx.mp4", default_year=2026
    )
    assert (year, month, day) == (2026, 11, 19)


def test_parse_period_from_path_full_year_month():
    """``/鹿13/2026年11月/...`` → 年月从目录名取。"""
    year, month, day = parse_period_from_path(
        "/鹿13/2026年11月/2026.11.19/a.mp4", default_year=2020
    )
    assert (year, month, day) == (2026, 11, 19)


def test_parse_period_from_path_month_only():
    """只有月份没有日期 ⇒ 归到当月 1 号（不假装是某一天）。"""
    year, month, day = parse_period_from_path(
        "/成品-小瑶/11月/a.mp4", default_year=2026
    )
    assert (year, month, day) == (2026, 11, 1)


def test_parse_period_from_path_no_match():
    """解析不出 ⇒ 全 0。"""
    assert parse_period_from_path("/随便/某个目录/a.mp4") == (0, 0, 0)


def test_business_day_uses_dir_not_ctime():
    """⚠️⚠️ **核心回归**：业务日期取自**目录名**，不是上传时间。

    实测（2026-11-20）：``11.19`` 目录里的文件 ctime 是 **2026-11-20**，
    11.5 目录里的是 **2026-11-06** ⇒ 按 ctime 归日会整体错位。
    """
    # 11.19 目录，文件却上传于 11.20
    item = PanFile(
        path="/成品-小瑶/11月/11.19/麻花上衣.mp4",
        name="麻花上衣.mp4",
        is_dir=False,
        size=1,
        ctime=int(time.mktime(time.strptime("2026-11-20", "%Y-%m-%d"))),
        mtime=0,
    )
    assert business_day(item, default_year=2026) == "2026-11-19"


def test_business_day_falls_back_to_ctime():
    """⚠️ 目录名解析不出 ⇒ **降级**用上传时间（并由调用方知晓这是降级）。"""
    item = PanFile(
        path="/随便/目录/a.mp4",
        name="a.mp4",
        is_dir=False,
        size=1,
        ctime=int(time.mktime(time.strptime("2026-11-05", "%Y-%m-%d"))),
        mtime=0,
    )
    assert business_day(item, default_year=2026) == "2026-11-05"


def test_count_by_day_uses_business_day():
    """⚠️ 计数按**业务日期**归集 —— 目录里的文件算到目录名那天。"""
    ctime_1120 = int(time.mktime(time.strptime("2026-11-20", "%Y-%m-%d")))
    files = [
        PanFile("/成品-小瑶/11月/11.19/a.mp4", "a.mp4", False, 1, ctime_1120, 0),
        PanFile("/成品-小瑶/11月/11.19/b.mp4", "b.mp4", False, 1, ctime_1120, 0),
        PanFile("/成品-小瑶/11月/11.5/c.mp4", "c.mp4", False, 1, ctime_1120, 0),
    ]
    counts = count_by_day(files, extensions="mp4")
    assert counts == {"2026-11-19": 2, "2026-11-05": 1}


# ─────────────────────────────────────────────────────────────────────────
# 6. 服务层
# ─────────────────────────────────────────────────────────────────────────


class FakePanClient(BaiduPanClient):
    """假的网盘客户端。"""

    def __init__(self, files=None, tokens=None, fail: Exception | None = None):
        self.files = files or []
        self.tokens = tokens
        self.fail = fail
        self.refreshed: list[str] = []
        self.closed = False

    def build_authorize_url(self, *, state: str = "", display: str = "tv") -> str:
        return "https://openapi.baidu.com/oauth/2.0/authorize?state=" + state

    def exchange_code(self, code: str):
        if self.tokens is not None:
            return self.tokens
        raise BaiduPanAuthError("未配置")

    def refresh(self, refresh_token: str):
        self.refreshed.append(refresh_token)
        from backend.app.services.baidu_pan_client import TokenSet

        return TokenSet(
            access_token="AT2",
            refresh_token="RT2",
            expires_in=2592000,
            scope="basic netdisk",
        )

    def list_files_recursive(self, access_token, path, **kwargs):
        if self.fail is not None:
            raise self.fail
        return list(self.files)

    def close(self):
        self.closed = True


def _seed_token(db, **kwargs):
    row = BaiduPanToken(
        id="default",
        app_key="AK",
        app_secret="SK",
        redirect_uri="oob",
        access_token=kwargs.get("access_token", "AT1"),
        refresh_token=kwargs.get("refresh_token", "RT1"),
        expires_at=kwargs.get("expires_at", int(time.time()) + 86400),
    )
    db.add(row)
    db.commit()
    return row


def test_authorize_stores_tokens(db):
    """授权后 token 落库 —— 不存就 30 天后断链。"""
    from backend.app.services.baidu_pan_client import TokenSet

    client = FakePanClient(tokens=TokenSet("AT1", "RT1", 2592000, "basic netdisk"))
    service = BaiduPanService(db, client)
    service.authorize("CODE")
    db.commit()

    row = db.get(BaiduPanToken, "default")
    assert row is not None
    assert row.access_token == "AT1"
    assert row.refresh_token == "RT1"
    assert row.expires_at > 0


def test_ensure_access_token_refreshes_and_saves_new_refresh_token(db):
    """⚠️⚠️ **核心回归**：刷新后必须存**新的** refresh_token。

    百度的 refresh_token 只能用一次 —— 不覆盖，下次刷新就报 110。
    """
    _seed_token(db, access_token="", expires_at=0)
    client = FakePanClient()
    service = BaiduPanService(db, client)

    token = service.ensure_access_token()
    db.commit()

    assert token == "AT2"
    assert client.refreshed == ["RT1"]
    row = db.get(BaiduPanToken, "default")
    assert row.refresh_token == "RT2", "刷新后必须换成新的 refresh_token"
    assert row.access_token == "AT2"


def test_ensure_access_token_keeps_old_refresh_token_when_not_returned(db):
    """⚠️ 刷新响应**不含**新 refresh_token 时 ⇒ 沿用旧的，不能清空。"""
    _seed_token(db, access_token="", expires_at=0)

    class NoNewTokenClient(FakePanClient):
        def refresh(self, refresh_token: str):
            from backend.app.services.baidu_pan_client import TokenSet

            return TokenSet(access_token="AT3", refresh_token="", expires_in=2592000)

    service = BaiduPanService(db, NoNewTokenClient())
    service.ensure_access_token()
    db.commit()

    row = db.get(BaiduPanToken, "default")
    assert row.refresh_token == "RT1", "响应没给新的就必须沿用旧的"
    assert row.access_token == "AT3"


def test_ensure_access_token_rejects_when_not_authorized(db):
    service = BaiduPanService(db, FakePanClient())
    with pytest.raises(BaiduPanAuthError):
        service.ensure_access_token()


def test_run_sync_counts_files_in_window(db):
    """取数 → 计数 → 落快照。"""
    _seed_token(db)
    day = int(time.mktime(time.strptime("2026-10-15", "%Y-%m-%d")))
    client = FakePanClient(
        files=[
            PanFile("/apps/x/a.mp4", "a.mp4", False, 1, day, day),
            PanFile("/apps/x/b.mp4", "b.mp4", False, 1, day, day),
        ]
    )
    service = BaiduPanService(db, client)
    batch = service.run_sync(window_start="2026-10-01", window_end="2026-10-31")
    db.commit()

    assert batch.status == "succeeded"
    assert batch.counted_files == 2
    snapshot = db.scalar(select(BaiduPanCount))
    assert snapshot is not None
    assert snapshot.total == 2
    assert snapshot.daily_counts == {"2026-10-15": 2}


def test_run_sync_default_window_is_last_month(db):
    """⚠️ 默认窗口 = **上个自然月** —— 当月不完整，会产生假异常。"""
    _seed_token(db)
    client = FakePanClient(files=[])
    service = BaiduPanService(db, client)
    batch = service.run_sync()
    db.commit()

    detail = batch.detail
    today = date.today()
    first_this = today.replace(day=1)
    last_end = first_this - timedelta(days=1)
    assert detail["window_end"] == last_end.isoformat()
    assert detail["window_start"] == last_end.replace(day=1).isoformat()


def test_run_sync_is_idempotent_for_same_window(db):
    """⚠️ 同一窗口重复取数 ⇒ **覆盖**快照，不追加（否则重复计数）。"""
    _seed_token(db)
    day = int(time.mktime(time.strptime("2026-10-15", "%Y-%m-%d")))
    client = FakePanClient(
        files=[PanFile("/apps/x/a.mp4", "a.mp4", False, 1, day, day)]
    )
    service = BaiduPanService(db, client)
    service.run_sync(window_start="2026-10-01", window_end="2026-10-31")
    db.commit()
    service.run_sync(window_start="2026-10-01", window_end="2026-10-31")
    db.commit()

    rows = list(db.scalars(select(BaiduPanCount)).all())
    assert len(rows) == 1, "同一窗口只能有一条快照"
    assert rows[0].total == 1


def test_run_sync_records_failure(db):
    """取数失败 ⇒ 批次记 failed，**不抛异常**。"""
    _seed_token(db)
    client = FakePanClient(fail=BaiduPanError("网盘炸了", errno=500))
    service = BaiduPanService(db, client)
    batch = service.run_sync()
    db.commit()

    assert batch.status == "failed"
    assert "网盘炸了" in batch.error_message


def test_probe_access_reports_permission_error(db):
    """⚠️ 授权后立刻验目录 —— 与其等定时任务才发现没勾目录。"""
    _seed_token(db)
    client = FakePanClient(fail=BaiduPanError("没有共享目录的权限", errno=42213))
    service = BaiduPanService(db, client)
    probe = service.probe_access()
    assert probe["ok"] is False
    assert probe["errno"] == 42213


def test_watch_path_uses_configured_business_dir(db, monkeypatch):
    """⚠️ **直接配网盘业务目录**，不加 ``/apps`` 前缀。

    实测（2026-11-20）：``file?method=list`` 能读任意授权目录，
    而 ``multimedia?method=listall`` 读不了业务目录（报 20020）。
    """
    from backend.app.core import config

    monkeypatch.setattr(config.settings, "baidu_pan_watch_dir", "/成品-小瑶")
    service = BaiduPanService(db, FakePanClient())
    assert service.watch_path() == "/成品-小瑶"


def test_watch_path_normalizes_missing_leading_slash(db, monkeypatch):
    from backend.app.core import config

    monkeypatch.setattr(config.settings, "baidu_pan_watch_dir", "成品-小瑶")
    service = BaiduPanService(db, FakePanClient())
    assert service.watch_path() == "/成品-小瑶"


def test_watch_path_falls_back_to_apps_dir(db, monkeypatch):
    """没配目录 ⇒ 退回平台生成的 ``/apps/{应用名}``。"""
    from backend.app.core import config

    monkeypatch.setattr(config.settings, "baidu_pan_watch_dir", "")
    monkeypatch.setattr(config.settings, "baidu_pan_app_name", "绩效校验")
    service = BaiduPanService(db, FakePanClient())
    assert service.watch_path() == "/apps/绩效校验"


# ─────────────────────────────────────────────────────────────────────────
# 7. 比对（只提示不判定）
# ─────────────────────────────────────────────────────────────────────────


def test_compare_match():
    result = compare_with_report(6, 6)
    assert result["status"] == "match"
    assert result["diff"] == 0.0


def test_compare_mismatch_reports_difference():
    result = compare_with_report(6, 5)
    assert result["status"] == "mismatch"
    assert result["diff"] == 1.0
    assert "核实" in result["message"]


def test_compare_without_actual():
    result = compare_with_report(6, None)
    assert result["status"] == "no_actual"


def test_compare_without_reported():
    result = compare_with_report(None, 5)
    assert result["status"] == "no_reported"


def test_compare_result_has_no_score_field():
    """⚠️⚠️ **回归：比对结果绝不能带分数** —— 数据源只提示，不打分。"""
    result = compare_with_report(6, 5)
    assert "score" not in result
    assert "final_score" not in result
    assert "suggested_score" not in result
    assert set(result.keys()) == {"status", "reported", "actual", "diff", "message"}
