"""Step 6 计划助手智能体 —— 输出契约、降级、工具编排、脱敏。

重点验证三条红线：
1. 模型产不出考核数字 —— 权重/预估价值必须不在输出里。
2. 工具调用受白名单约束 —— 模型调不了不该调的工具。
3. 降级不阻塞员工 —— 模型挂了就返回空草稿 + 提示。
"""

import json

import pytest
from app.base import Completion, EchoModelClient, NullModelClient
from app.plan import PlanContext, PlanRequest, build_plan_tool_registry, run_plan
from app.plan_agent import PlanAgent, PlanFindingOutput, PlanItemOutput, PlanOutput
from app.tools import ToolRegistry, ToolSpec


def make_request(**overrides):
    payload = {
        "mode": "generate",
        "intent_text": "帮我生成本月的支付系统优化计划",
        "context": {"scope": "self", "plan_type": "month", "period": "202611"},
        "tool_token": "test-token",
        "allowed_tools": ["get_my_plan_history"],
    }
    payload.update(overrides)
    return PlanRequest(**payload)


# ---------------------------------------------------------------------------
# ⚠️⚠️ period 契约回归（2026-11-21）
#
# 这个 bug 让**月/周/日三条链路全部 422** → 后端降级成空草稿 →
# 用户看到「AI 建议暂不可用」，前端却一点提示都没有。
#
# ⚠️ 它能活下来的原因：**测试造数用 `202611`**（唯一能通过旧 pattern 的格式），
# 而业务层传的是前端原样的 `2026-11` / `2026-11-17`。
# 测试造数和业务调用**用的不是同一个格式**，断言照过。
#
# 所以这里必须**显式**把业务层真实传的三个格式钉住。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("period", [
    "2026-11",      # 月计划 planMonth
    "2026-11-17",   # 周计划 weekStart / 日计划 planDate
    "202611",       # 紧凑格式（历史用法）
    "20261117",     # 紧凑格式
])
def test_period_accepts_every_format_the_business_layer_sends(period):
    """业务层真实传的每个 period 格式都必须被接受 —— 不能 422。"""
    context = PlanContext(scope="self", plan_type="month", period=period)
    assert context.period


def test_period_accepts_empty():
    """空周期是合法值（默认）—— 不能被拒。"""
    assert PlanContext(period="").period == ""


@pytest.mark.parametrize("plan_type,period", [
    ("month", "2026-11"),
    ("week", "2026-11-17"),
    ("day", "2026-11-17"),
])
def test_request_accepts_business_layer_period_formats(plan_type, period):
    """整请求必须能构造成功 —— 端点层的 422 就是这么来的。"""
    request = PlanRequest(
        mode="generate",
        intent_text="推进订单中心重构",
        context={"scope": "self", "plan_type": plan_type, "period": period},
        tool_token="test-token",
        allowed_tools=["get_my_plan_history"],
    )
    assert request.context.period


def test_period_is_normalized_to_dashed_format_for_the_model():
    """⚠️ 归一化必须产出**模型不会误读**的格式。

    实测：紧凑格式 `20261117` 被模型读成复数 `periods=20261117`，
    并据此**推断出错误的计划起止日期**（11-17 推成 11-17~11-20）。
    ⇒ 周期是要给人读也要给模型读的字段，必须用带横线的格式。
    """
    assert PlanContext(period="202611").period == "2026-11"
    assert PlanContext(period="20261117").period == "2026-11-17"
    assert PlanContext(period="2026-11").period == "2026-11"
    assert PlanContext(period="2026-11-17").period == "2026-11-17"


def test_period_rejects_garbage():
    """格式不对的周期必须被拒 —— 不能静默吞掉变成空串。"""
    with pytest.raises(Exception):
        PlanContext(period="not-a-period")
    with pytest.raises(Exception):
        PlanContext(period="2026-1-1-1")


# ---------------------------------------------------------------------------
# 输出契约
# ---------------------------------------------------------------------------


def test_output_schema_has_no_weight_or_value():
    """输出契约里不能有 performanceWeight / estimatedValue。

    理由：这两个字段决定考核权重和业务预估值。
    模型产出它们 = 模型参与了考核算分。
    """
    fields = set(PlanOutput.model_fields)
    assert "items" in fields
    assert "findings" in fields
    assert "performanceWeight" not in fields
    assert "estimatedValue" not in fields
    assert "score" not in fields

    item_fields = set(PlanItemOutput.model_fields)
    assert "performance_weight" not in item_fields
    assert "estimated_value" not in item_fields
    assert item_fields == {
        "task_name", "task_content", "deliverable", "quality",
        "estimated_start_date", "estimated_end_date",
    }


def test_output_schema_forbids_extra_fields():
    """extra=forbid —— 模型不能通过加字段扩展业务契约。"""
    assert PlanOutput.model_config.get("extra") == "forbid"


def test_finding_kind_is_constrained():
    """体检结论的 kind 只能是枚举内的值。"""
    valid = set(PlanFindingOutput.model_fields["kind"].annotation.__args__)
    assert "duplicate" in valid
    assert "missing_deliverable" in valid
    with pytest.raises(Exception):
        PlanFindingOutput(kind="arbitrary_thing")


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------


def test_prompt_forbids_weight_and_score():
    """提示词必须显式禁止模型产出考核数字。"""
    prompt = PlanAgent(EchoModelClient()).system_prompt()
    assert "performanceWeight" in prompt
    assert "estimatedValue" in prompt
    assert "绝不" in prompt


def test_prompt_states_semantic_dedup_is_non_blocking():
    """判重是语义判重 + 只提示不阻断 —— 必须写进提示词。"""
    prompt = PlanAgent(EchoModelClient()).system_prompt()
    assert "语义判重" in prompt
    assert "只提示，不阻断" in prompt


def test_prompt_warns_tools_are_untrusted():
    """工具数据是不可信材料 —— 提示词必须声明。"""
    prompt = PlanAgent(EchoModelClient()).system_prompt()
    assert "不可信材料" in prompt


# ---------------------------------------------------------------------------
# 降级
# ---------------------------------------------------------------------------


def test_null_model_falls_back_without_raising():
    """模型全挂 -> 降级成空草稿 + 提示，不抛异常。"""
    agent = PlanAgent(NullModelClient())
    result = agent.run(mode="generate", intent_text="生成计划", context={})
    assert result.degraded is True
    assert result.data["items"] == []
    assert result.data["warnings"]


def test_check_mode_fallback_gives_hint_not_fake_findings():
    """检查模式降级 -> 给一条提示，不给假结论。"""
    agent = PlanAgent(NullModelClient())
    result = agent.run(mode="check", intent_text="检查计划", context={})
    assert result.degraded is True
    findings = result.data["findings"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "other"
    assert "暂不可用" in findings[0]["message"]


def test_fallback_mentions_manual_entry():
    """降级提示要告诉用户手动填写 —— 不能让员工干等。"""
    agent = PlanAgent(NullModelClient())
    result = agent.run(mode="generate", intent_text="生成计划", context={})
    assert any("手动" in w for w in result.data["warnings"])


# ---------------------------------------------------------------------------
# 工具注册
# ---------------------------------------------------------------------------


def test_registry_has_both_plan_tools():
    """注册表里应有 2 个计划工具。"""
    registry = build_plan_tool_registry("token")
    names = {spec["function"]["name"] for spec in registry.schema(
        {"get_my_plan_history", "get_department_plan_history"}
    )}
    assert names == {"get_my_plan_history", "get_department_plan_history"}


def test_tool_params_have_no_identity_fields():
    """工具参数里不能有 employee_id / user_id 等身份字段。

    查谁的数据由令牌决定，不由参数决定。
    """
    registry = build_plan_tool_registry("token")
    for spec in registry.schema({"get_my_plan_history", "get_department_plan_history"}):
        properties = spec["function"]["parameters"]["properties"]
        for forbidden in ("employee_id", "target_employee_id", "user_id", "department_id"):
            assert forbidden not in properties


def test_tool_params_forbid_extra_properties():
    """工具参数必须禁止额外字段。"""
    registry = build_plan_tool_registry("token")
    for spec in registry.schema({"get_my_plan_history", "get_department_plan_history"}):
        assert spec["function"]["parameters"]["additionalProperties"] is False


def test_registry_uses_plan_tools_prefix():
    """计划工具必须打到 /v1/plan-tools —— 不是考核域的 /v1/agent-tools。"""
    registry = build_plan_tool_registry("token")
    spec = registry.get("get_my_plan_history")
    assert spec is not None
    assert spec.handler.prefix == "/v1/plan-tools"


def test_registry_rejects_identity_params():
    """注册含身份字段的工具会被拒 —— 这是硬防线。"""
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(ToolSpec(
            name="bad_tool", description="bad",
            parameters={
                "type": "object",
                "properties": {"employee_id": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=lambda args: {},
        ))


# ---------------------------------------------------------------------------
# 端到端（echo 模式）
# ---------------------------------------------------------------------------


def test_run_plan_echo_returns_valid_shape():
    """echo 模式 -> 返回形状合法的草稿（不是降级）。"""
    response = run_plan(make_request(), EchoModelClient())
    assert response.degraded is False
    assert isinstance(response.items, list)
    for item in response.items:
        assert "task_name" in item
        assert "performance_weight" not in item


def test_run_plan_check_mode_returns_findings():
    """检查模式 -> 返回 findings。"""
    response = run_plan(make_request(mode="check"), EchoModelClient())
    assert response.degraded is False
    assert len(response.findings) >= 1


def _client_returning(content):
    """构造一个直接返回指定内容的客户端。"""

    class FixedClient(EchoModelClient):
        def complete_with_tools(self, messages, tools, schema=None):
            payload = None
            for message in messages:
                if message.get("role") == "user":
                    payload = json.loads(message.get("content") or "{}")
                    break
            has_tool = any(m.get("role") == "tool" for m in messages)
            if payload and has_tool:
                return Completion(content=content), None
            return super().complete_with_tools(messages, tools, schema=schema)

    return FixedClient()


def test_run_plan_sanitizes_score_leak():
    """模型把绩效分 95 写进计划条目 -> 必须被清掉。

    理由：计划会变成考核指标，分数泄漏到计划里
    等于把考核结论提前写进计划。
    """
    client = _client_returning({
        "items": [{
            "task_name": "支付优化",
            "task_content": "个人绩效分 95，达成率 100%",
            "deliverable": "上线报告",
            "quality": "",
            "estimated_start_date": "",
            "estimated_end_date": "",
        }],
        "findings": [], "warnings": [], "missing_context": [],
    })
    response = run_plan(make_request(), client)
    assert response.degraded is False
    content = response.items[0]["task_content"]
    assert "95" not in content
    assert "100%" in content


def test_run_plan_keeps_business_text_intact():
    """清洗不能误伤正常业务文本。"""
    client = _client_returning({
        "items": [{
            "task_name": "支付链路稳定性优化",
            "task_content": "完成支付链路的稳定性改造，达成率 100%",
            "deliverable": "上线报告",
            "quality": "P0 故障为 0",
            "estimated_start_date": "2026-11-01",
            "estimated_end_date": "2026-11-30",
        }],
        "findings": [], "warnings": [], "missing_context": [],
    })
    response = run_plan(make_request(), client)
    content = response.items[0]["task_content"]
    assert "支付链路" in content
    assert "100%" in content
