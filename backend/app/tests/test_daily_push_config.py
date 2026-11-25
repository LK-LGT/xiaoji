"""每日填报提醒推送的**配置契约**（2026-11-24）。

⚠️ **为什么要有这个测试**
------------------------
推送是**主动打扰用户**的操作，而它是否生效取决于**四层**条件
（开关 / 凭据 / 板块 / 口径），其中**两层在数据库里**。
⇒ 光看 ``.env`` 看不出全貌，光看代码也看不出配置对不对。

⇒ 这个测试把「配置契约」固化下来：
1. **配置项存在且类型正确** —— 防止改名/删掉导致推送静默失效；
2. **开关默认关闭** —— 生产环境不该一装上就天天刷屏；
3. **收件人由数据库口径决定，不在配置里** —— 这是 2026-11-23 的设计决定
   （当时把「哪些岗位要收提醒」从配置里删掉了）；
4. **调度器状态可观测** —— 出问题要能查。

⚠️ **不测「真的发出去了」** —— 那要真发飞书消息，
属于真机验收（见 ``docs/17-飞书对接执行流程.md`` §5.5.3）。
"""

from __future__ import annotations

import pytest

from backend.app.core.config import Settings, settings


class Test配置项契约:
    """① 配置项存在且类型正确。"""

    def test_每日推送开关存在且是布尔(self):
        assert isinstance(settings.feishu_daily_push_enabled, bool)

    def test_每日推送时刻存在且是字符串(self):
        assert isinstance(settings.feishu_daily_push_time, str)

    def test_推送时刻格式是HH_MM(self):
        """
        ⚠️ **为什么要校验格式**
        ``feishu_daily_push_scheduler._parse_push_time`` 对格式不对的
        静默退回 17:30 ⇒ 配置写错时**没有任何提示**，
        只是「推送时间跟我想的不一样」⇒ 很难排查。
        """
        raw = settings.feishu_daily_push_time.strip()
        assert len(raw) == 5 and raw[2] == ":", f"推送时刻格式应为 HH:MM，实际 {raw!r}"
        hour_text, minute_text = raw.split(":")
        assert hour_text.isdigit() and minute_text.isdigit(), f"推送时刻含非数字：{raw!r}"
        assert 0 <= int(hour_text) <= 23, f"推送小时越界：{raw!r}"
        assert 0 <= int(minute_text) <= 59, f"推送分钟越界：{raw!r}"

    def test_推送时刻解析器与配置一致(self):
        """
        ⚠️ **配对断言** —— 调度器真正用的解析函数，
        必须读出与配置**相同**的时刻。
        （否则配置改了但调度器还在用别的时刻。）
        """
        from backend.app.services.feishu_daily_push_scheduler import _parse_push_time

        hour, minute = _parse_push_time()
        expected = settings.feishu_daily_push_time.strip().split(":")
        assert (hour, minute) == (int(expected[0]), int(expected[1]))


class Test默认关闭:
    """② 开关默认关闭 —— 生产环境不该一装上就天天刷屏。"""

    def test_默认关闭(self):
        """
        ⚠️ **为什么查「类默认值」而不是「实例值」**
        测试进程里根 ``conftest`` 会把环境变量打开（pytest 要登录）
        ⇒ 实例值必然是 True ⇒ 断言实例值是**错的**。
        ⇒ 要断言的是**「不配环境变量时」的行为** ⇒ 查 ``model_fields``。
        """
        assert Settings.model_fields["feishu_daily_push_enabled"].default is False, (
            "feishu_daily_push_enabled 默认必须是 False（推送会打扰用户）"
        )

    def test_默认时刻是1730(self):
        assert Settings.model_fields["feishu_daily_push_time"].default == "17:30"


class Test收件人不在配置里:
    """③ 收件人由数据库口径决定，不在配置里。

    ⚠️ **为什么这条最重要**
    2026-11-23 把「哪些岗位要收填报提醒」从配置里**彻底移除** ——
    岗位名单写死在配置里 = 岗位一变就得改代码发版。
    用户原话：「哪个部门都有不一样的考核方式，肯定是不一样的功能呀」。

    ⇒ 现在口径在 ``pan_workload_policy`` 表（部门 × 岗位）。
    ⇒ **改口径不用改代码、不用发版。**

    ⇒ 这个测试防止有人「好心」把岗位名单又加回配置里。
    """

    def test_配置里没有岗位名单(self):
        """
        ⚠️ **怎么判定「没有」**
        配置字段名里**不得**出现能表示「岗位/角色名单」的词
        （position / role / job / editor / reporter …）。
        """
        forbidden = ("position", "role", "job", "editor", "reporter", "reviewer")
        offenders = [
            name
            for name in type(settings).model_fields
            if any(word in name.lower() for word in forbidden)
        ]
        assert not offenders, (
            f"配置里出现了疑似「收件人名单」字段：{offenders}\n"
            "⇒ 收件人口径属于 ``pan_workload_policy`` 表，不该硬编码在配置里。"
        )

    def test_口径来自数据库(self):
        """口径表存在且按「部门 × 岗位」建模。"""
        from backend.app.domain.pan_workload_models import PanWorkloadPolicy

        columns = {c.name for c in PanWorkloadPolicy.__table__.columns}
        assert "department_id" in columns, "口径表必须按部门隔离"
        assert "position" in columns, "口径表必须按岗位区分"
        assert "role" in columns, "口径表必须有 reporter/reviewer 角色"
        assert "metric_codes" in columns, "口径表必须能配指标（谁填什么）"


class Test调度器可观测:
    """④ 调度器状态可观测 —— 出问题要能查。"""

    def test_状态字段齐全(self):
        from backend.app.services.feishu_daily_push_scheduler import get_scheduler_status

        status = get_scheduler_status()
        for key in ("enabled", "running", "pushTime", "lastRunAt", "lastResult", "lastError"):
            assert key in status, f"调度器状态缺少 {key}（排障时会瞎）"

    def test_状态反映开关(self, monkeypatch):
        from backend.app.services.feishu_daily_push_scheduler import get_scheduler_status

        monkeypatch.setattr(settings, "feishu_daily_push_enabled", True)
        assert get_scheduler_status()["enabled"] is True
        monkeypatch.setattr(settings, "feishu_daily_push_enabled", False)
        assert get_scheduler_status()["enabled"] is False


class Test运维脚本存在:
    """⑤ 运维脚本要在 —— 否则改了配置没法自查。"""

    @pytest.mark.parametrize(
        "name",
        ["check_daily_push_config.py", "run_daily_push.py"],
    )
    def test_脚本存在(self, name):
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        path = root / "backend" / "scripts" / name
        assert path.exists(), f"缺少运维脚本 {name}"

    def test_体检脚本不发送消息(self):
        """
        ⚠️ **体检脚本绝不能发消息** ——
        它是「改完配置先跑一下」的默认动作，
        一旦它发消息 ⇒ 用户不敢跑 ⇒ 配置就没人敢改了。

        ⚠️ **为什么用 AST 而不是字符串匹配**
        --------------------------------
        脚本里**注释**提到 ``push_daily_metrics``（解释「手动触发不受开关限制」）
        是完全正常的 ⇒ 字符串匹配会**误报**。
        ⇒ 只看**真正的调用**。
        """
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        path = root / "backend" / "scripts" / "check_daily_push_config.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))

        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called.add(func.attr)

        assert "push_daily_metrics" not in called, (
            "体检脚本调用了 push_daily_metrics ⇒ 它会真的发消息"
        )
        assert "run_daily_push" not in called, (
            "体检脚本不该调用 run_daily_push（那是手动推送入口）"
        )

    def test_手动推送脚本默认不发送(self):
        """
        ⚠️ **默认必须是「演练」** ——
        必须显式加 ``--confirm`` 才发消息 ⇒ 防止误触。
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        source = (root / "backend" / "scripts" / "run_daily_push.py").read_text(encoding="utf-8")
        assert '"--confirm"' in source, "手动推送脚本必须有 --confirm 开关"
        assert "args.confirm" in source, "手动推送脚本必须按 --confirm 分支"
