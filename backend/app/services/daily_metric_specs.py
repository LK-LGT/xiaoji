"""★ **每日填报的指标定义** —— 谁填什么，一处定义（2026-11-23）。

⚠️ **为什么要有这个模块**
------------------------
2026-11-23 业务确认：**角色倒转**。

    之前：剪辑填「完播率 / 视频留存率」（两个比率）
    现在：**剪辑填数量**（普通视频 / OOTD / 智能体 各多少条）+ 备注 + 截图
          **主管填比率**（完播率 / 视频留存率）

⇒ 同一块业务里，**不同角色填不同的指标** ——
这正是「部门 × 岗位 口径」的意义（见 ``pan_workload_policy``）。

⚠️ **为什么不能硬编码在推送服务里**
----------------------------------
指标集合会变（加一类视频、改一条口径）⇒ 硬编码要改代码发版。
⇒ 落在 ``pan_workload_policy.metric_codes`` 里，按岗位配。

⚠️ **指标的展示元数据放哪**
--------------------------
「普通视频」叫什么中文名、卡片里提示什么、占几行 ——
这些是**展示口径**，跟着指标码走 ⇒ 集中在这里定义。

⚠️ **量纲必须区分**
------------------
数量（条）与比率（%）**值域不同** ⇒
``pan_manual_metric_service.COUNT_METRIC_CODES`` 是权威清单。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    """一个指标的展示元数据。"""

    #: 指标码（与 ``pan_manual_metric.metric_code`` 一致）
    code: str
    #: 中文名（卡片标签）
    label: str
    #: 值域类型：``count``（条数）/ ``rate``（比率）
    kind: str
    #: 输入框提示
    placeholder: str
    #: 是否必填
    required: bool = True


#: ★ **剪辑要填的三条数量指标**（2026-11-23 业务确认）
#:
#: ⚠️ **顺序 = 卡片表格的列顺序** ⇒ 普通视频 → OOTD → 智能体。
COUNT_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        code="video_normal_count",
        label="普通视频",
        kind="count",
        placeholder="例如：8",
    ),
    MetricSpec(
        code="video_ootd_count",
        label="OOTD",
        kind="count",
        placeholder="例如：2",
    ),
    MetricSpec(
        code="video_agent_count",
        label="智能体",
        kind="count",
        placeholder="例如：1",
    ),
)

#: ★ **主管要填的两个比率指标**
#:
#: ⚠️ **不是剪辑填** —— 主管看到后台数据后填效果指标。
RATE_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        code="video_completion_rate",
        label="完播率",
        kind="rate",
        placeholder="例如：35%",
    ),
    MetricSpec(
        code="video_retention_rate",
        label="视频留存率",
        kind="rate",
        placeholder="例如：62%",
    ),
)

#: 全部指标定义（按 code 索引）
ALL_METRICS: dict[str, MetricSpec] = {
    spec.code: spec for spec in (*COUNT_METRICS, *RATE_METRICS)
}

#: 备注字段名（卡片表单里的 input name）
REMARK_FIELD = "remark"

#: ★ **剪辑的填报项** —— 三条数量 + 备注
#:
#: ⚠️ **为什么截图不在这里**
#: 飞书卡片 JSON 2.0 **不支持文件上传控件**（2026-11-23 真机实测：
#: ``input_type="file"`` → ``invalid input type``；
#: ``file_input`` / ``file_upload`` tag → ``not support tag``）。
#: ⇒ 截图只能走**另一条通路**：剪辑在飞书里**直接发图**到聊天，
#: 系统自动存成证据附件（已有通路：``feishu_manual_metric.store_evidence``）。
EDITOR_FIELDS: tuple[MetricSpec, ...] = COUNT_METRICS

#: ★ **主管的填报项** —— 两个比率
LEADER_FIELDS: tuple[MetricSpec, ...] = RATE_METRICS


def specs_for_codes(codes: tuple[str, ...]) -> list[MetricSpec]:
    """按指标码取展示元数据（**忽略认不出的码**）。

    ⚠️ **忽略而不是报错** —— 口径表里可能配了已下线的指标码，
    不该让整张卡发不出去。
    """
    return [ALL_METRICS[code] for code in codes if code in ALL_METRICS]


def default_codes(specs: tuple[MetricSpec, ...]) -> tuple[str, ...]:
    """取一组指标的全部指标码。"""
    return tuple(spec.code for spec in specs)


__all__ = [
    "ALL_METRICS",
    "COUNT_METRICS",
    "EDITOR_FIELDS",
    "LEADER_FIELDS",
    "RATE_METRICS",
    "REMARK_FIELD",
    "MetricSpec",
    "default_codes",
    "specs_for_codes",
]
