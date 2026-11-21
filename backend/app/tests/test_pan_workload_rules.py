"""网盘工作量规则引擎测试（BP-2 / P0）。

⚠️ **测试用例全部来自 2026-11-21 真实网盘实测**
    见 ``docs/短视频剪辑工作量统计-实现方案.md`` §〇·一·二
    ⇒ 这些不是假想场景，是**真实存在过的文件名与路径**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.services.baidu_pan_client import PanFile  # noqa: E402
from backend.app.services.pan_workload_rules import (  # noqa: E402
    DEFAULT_MARKER_RULES,
    FileVerdict,
    judge_file,
    match_markers,
    resolve_business_date,
    resolve_marker_rules,
    resolve_owner,
    strip_version_suffix,
    summarize_daily,
    validate_bindings,
)


def _mk(name: str, path: str, *, size: int = 1000, md5: str = "") -> PanFile:
    return PanFile(path=path, name=name, is_dir=False, size=size, ctime=0, md5=md5)


# ─────────────────────────────────────────────────────────────
# 铁律 1：只数 mp4，png/jpg 是封面
# ─────────────────────────────────────────────────────────────

class TestExtensionFilter:
    """实测 D-1：mp4 与 png 是同一条内容的两种格式。"""

    def test_png_封面被排除(self):
        f = _mk(
            "卡其色裤子10.4(10.19).png",
            "/成品-小雨/10月/10.19/卡其色裤子10.4(10.19).png",
            size=10_911_268,
            md5="66559c042l",
        )
        v = judge_file(f)
        assert not v.counted
        assert v.exclude_reason == "ext_not_allowed:png"

    def test_mp4_计入(self):
        f = _mk(
            "卡其色裤子10.4(10.19).mp4",
            "/成品-小雨/10月/10.19/卡其色裤子10.4(10.19).mp4",
            size=84_000_000,
            md5="16f471d84n",
        )
        v = judge_file(f)
        assert v.counted
        assert v.count_weight == 1

    def test_jpg_封面被排除(self):
        f = _mk(
            "10月17日(1)-封面.jpg",
            "/成品-小雨/10月/10.17/10月17日(1)-封面.jpg",
        )
        v = judge_file(f)
        assert not v.counted
        assert v.exclude_reason.startswith("ext_not_allowed:")

    def test_mov_计入(self):
        f = _mk("a.mov", "/成品-小瑶/11月/11.19/a.mov")
        assert judge_file(f).counted


# ─────────────────────────────────────────────────────────────
# 铁律 2：-N 不是双条标记
# ─────────────────────────────────────────────────────────────

class TestVersionSuffixIsNotMarker:
    """实测 D-3：27 组 ``X-N`` 与 ``X`` 的 md5 全部不同。"""

    def test_智能体减2_不是双条(self):
        f = _mk(
            "11.13 智能体-2.mp4",
            "/成品-小瑶/11月/11.13/11.13 智能体-2.mp4",
            size=123_225_174,
            md5="448272088n0d",
        )
        v = judge_file(f)
        assert v.counted
        # ★ 命中「智能体」⇒ 2 条，但**不是** 4 条
        assert v.count_weight == 2
        assert v.markers.names == ["智能体"]

    def test_智能体_本体也是2条(self):
        f = _mk(
            "11.13 智能体.mp4",
            "/成品-小瑶/11月/11.13/11.13 智能体.mp4",
            size=239_347_446,
            md5="7e75a9e4asb4",
        )
        v = judge_file(f)
        assert v.count_weight == 2

    def test_两个文件合计4条而非5条(self):
        """``智能体``(2) + ``智能体-2``(2) = 4 条，不是 1+2+2=5。"""
        a = judge_file(_mk("11.13 智能体.mp4", "/成品-小瑶/11月/11.13/11.13 智能体.mp4"))
        b = judge_file(_mk("11.13 智能体-2.mp4", "/成品-小瑶/11月/11.13/11.13 智能体-2.mp4"))
        assert a.count_weight + b.count_weight == 4

    def test_序号变体不被当标记(self):
        """``10.26-3.mp4`` 里没有标记 ⇒ 权重 1。"""
        f = _mk("10.26-3.mp4", "/成品-小雨/10月/10.26/10.26-3.mp4")
        v = judge_file(f)
        assert v.counted
        assert v.count_weight == 1
        assert v.markers.names == []

    def test_全角括号序号不算双条(self):
        f = _mk("10.22（6）.mp4", "/成品-小雨/10月/10.22/10.22（6）.mp4")
        assert judge_file(f).count_weight == 1

    def test_strip_version_suffix(self):
        assert strip_version_suffix("11.13 智能体-2.mp4") == "11.13 智能体.mp4"
        assert strip_version_suffix("11.13 智能体.mp4") == "11.13 智能体.mp4"
        assert strip_version_suffix("10.22（6）.mp4") == "10.22.mp4"


# ─────────────────────────────────────────────────────────────
# 标记识别（业务口径：智能体 / OOTD 各算 2 条）
# ─────────────────────────────────────────────────────────────

class TestMarkers:
    def test_智能体_2条(self):
        hit = match_markers("11.13 智能体.mp4")
        assert hit.weight == 2
        assert hit.names == ["智能体"]

    def test_ootd_2条(self):
        hit = match_markers("11.17 OOTD.mp4")
        assert hit.weight == 2
        assert hit.names == ["OOTD"]

    def test_ootd_大小写不敏感(self):
        for name in ("OOTD.mp4", "ootd.mp4", "Ootd.mp4", "oOtD.mp4"):
            assert match_markers(name).weight == 2, name

    def test_普通文件_1条(self):
        hit = match_markers("11.13 波点上衣.mp4")
        assert hit.weight == 1
        assert hit.names == []

    def test_多标记取最高不叠加(self):
        """同时含智能体和 OOTD ⇒ 2 条，不是 4 条。"""
        hit = match_markers("11.13 智能体 OOTD.mp4")
        assert hit.weight == 2
        assert set(hit.names) == {"智能体", "OOTD"}

    def test_无标记文件(self):
        assert match_markers("10月22日 (2).mp4").weight == 1


# ─────────────────────────────────────────────────────────────
# 铁律 3：业务日期以目录为准
# ─────────────────────────────────────────────────────────────

class TestBusinessDate:
    def test_目录日期(self):
        day, _ = resolve_business_date("/成品-小瑶/11月/11.19/x.mp4", "x.mp4")
        assert day.endswith("-11-19")

    def test_文件名冲突_以目录为准(self):
        """实测：``/11.14/11.17-智能体.mp4`` ⇒ 目录的 11.14。"""
        day, anomalies = resolve_business_date(
            "/成品-小雨/11月/11.14/11.17-智能体.mp4", "11.17-智能体.mp4"
        )
        assert day.endswith("-11-14")
        assert "name_date_conflict" in anomalies

    def test_文件名一致_无异常(self):
        _, anomalies = resolve_business_date(
            "/成品-小瑶/11月/11.13/11.13 智能体.mp4", "11.13 智能体.mp4"
        )
        assert "name_date_conflict" not in anomalies

    def test_日期解析失败_标异常不丢弃(self):
        day, anomalies = resolve_business_date("/成品-小瑶/杂项/x.mp4", "x.mp4")
        assert day == ""
        assert "date_unparsed" in anomalies

    def test_中文日期文件名也识别为冲突(self):
        _, anomalies = resolve_business_date(
            "/成品-小雨/10月/10.17/10月17日 (1).mp4", "10月17日 (1).mp4"
        )
        assert "name_date_conflict" not in anomalies


# ─────────────────────────────────────────────────────────────
# 过滤规则
# ─────────────────────────────────────────────────────────────

class TestFilters:
    def test_关键词排除(self):
        f = _mk("返工-成片.mp4", "/成品-小瑶/11月/11.19/返工-成片.mp4")
        v = judge_file(f)
        assert not v.counted
        assert v.exclude_reason.startswith("keyword:")

    def test_草稿目录排除(self):
        f = _mk("成片.mp4", "/成品-小瑶/草稿/11.19/成片.mp4")
        v = judge_file(f)
        assert not v.counted
        assert v.exclude_reason.startswith("draft_dir:")

    def test_普通目录不被误伤(self):
        """⚠️ 目录名含「素材」子串的正常目录不应被排除。

        实测目录是 ``成品-小瑶`` / ``11月`` / ``11.19`` —— 不含关键词。
        """
        f = _mk("成片.mp4", "/成品-小瑶/11月/11.19/成片.mp4")
        assert judge_file(f).counted


# ─────────────────────────────────────────────────────────────
# 归属判定（实测：oper_id 无区分度，只用路径）
# ─────────────────────────────────────────────────────────────

class TestResolveOwner:
    BINDINGS = [
        {"folder_path": "/成品-小瑶", "employee_id": "emp_yao", "active": True},
        {"folder_path": "/成品-小雨", "employee_id": "emp_yu", "active": True},
    ]

    def test_路径命中(self):
        emp, kind, _ = resolve_owner("/成品-小瑶/11月/11.19/x.mp4", self.BINDINGS)
        assert (emp, kind) == ("emp_yao", "path")

    def test_另一人(self):
        emp, kind, _ = resolve_owner("/成品-小雨/11月/11.14/x.mp4", self.BINDINGS)
        assert (emp, kind) == ("emp_yu", "path")

    def test_未命中_不丢弃(self):
        emp, kind, _ = resolve_owner("/口播成品/5.28/x.mp4", self.BINDINGS)
        assert emp is None
        assert kind == "unbound"

    def test_歧义_不自动取第一条(self):
        """``/成品`` 与 ``/成品-小瑶`` 同时存在 ⇒ ambiguous。"""
        bindings = [
            {"folder_path": "/成品", "employee_id": "emp_a", "active": True},
            {"folder_path": "/成品-小瑶", "employee_id": "emp_b", "active": True},
        ]
        emp, kind, note = resolve_owner("/成品-小瑶/11月/11.19/x.mp4", bindings)
        assert emp is None
        assert kind == "ambiguous"
        assert note

    def test_停用的绑定不参与(self):
        bindings = [
            {"folder_path": "/成品-小瑶", "employee_id": "emp_yao", "active": False},
        ]
        emp, kind, _ = resolve_owner("/成品-小瑶/11月/11.19/x.mp4", bindings)
        assert emp is None
        assert kind == "unbound"


class TestValidateBindings:
    def test_互为前缀_报错(self):
        bindings = [
            {"folder_path": "/成品", "employee_id": "a", "active": True},
            {"folder_path": "/成品-小瑶", "employee_id": "b", "active": True},
        ]
        errors = validate_bindings(bindings)
        assert errors
        assert "前缀冲突" in errors[0]

    def test_同路径不同员工_报错(self):
        bindings = [
            {"folder_path": "/成品-小瑶", "employee_id": "a", "active": True},
            {"folder_path": "/成品-小瑶", "employee_id": "b", "active": True},
        ]
        errors = validate_bindings(bindings)
        assert errors
        assert "两个不同员工" in errors[0]

    def test_正常配置_通过(self):
        bindings = [
            {"folder_path": "/成品-小瑶", "employee_id": "a", "active": True},
            {"folder_path": "/成品-小雨", "employee_id": "b", "active": True},
        ]
        assert validate_bindings(bindings) == []


# ─────────────────────────────────────────────────────────────
# 按人可配的标记规则（实测两人命名风格不同）
# ─────────────────────────────────────────────────────────────

class TestPerFolderRules:
    RULES = {
        "default": DEFAULT_MARKER_RULES,
        "by_folder": {
            "/成品-小雨": {
                "智能体": {"weight": 3, "patterns": ["智能体"], "enabled": True},
            }
        },
    }

    def test_最长前缀优先(self):
        rules, source = resolve_marker_rules(self.RULES, "/成品-小雨/11月")
        assert source == "by_folder"
        assert rules["智能体"]["weight"] == 3

    def test_无覆盖用默认(self):
        rules, source = resolve_marker_rules(self.RULES, "/成品-小瑶/11月")
        assert source == "default"
        assert rules["智能体"]["weight"] == 2

    def test_规则为空用内置默认(self):
        rules, source = resolve_marker_rules(None, "/成品-小瑶")
        assert source == "default"
        assert rules["智能体"]["weight"] == 2


# ─────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────

class TestSummarize:
    def test_按日汇总(self):
        verdicts = [
            ("a", judge_file(_mk("11.13 智能体.mp4", "/成品-小瑶/11月/11.13/11.13 智能体.mp4"))),
            ("a", judge_file(_mk("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4"))),
            ("a", judge_file(_mk("11.14 OOTD.mp4", "/成品-小瑶/11月/11.14/11.14 OOTD.mp4"))),
        ]
        daily = summarize_daily(verdicts)
        assert daily["2026-11-13"] == 3   # 智能体(2) + 普通(1)
        assert daily["2026-11-14"] == 2   # OOTD(2)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
