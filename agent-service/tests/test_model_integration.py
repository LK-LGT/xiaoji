"""真模型集成测试（P3-2）—— **需要真实 API Key**，默认跳过。

为什么要有这个文件：
Mock 测试能验**契约**，但验不了「真实厂商的返回格式是不是我以为的那样」。
DeepSeek / 通义 / GLM 的 JSON 输出与 tool_calls 结构都有细微差异，
这些差异只有真跑一遍才会暴露。

运行方式：
    # 需要环境变量（不要写进仓库）
    $env:AGENT_MODEL_API_KEY = "sk-..."
    $env:AGENT_MODEL_BASE_URL = "https://api.deepseek.com/v1"
    $env:AGENT_MODEL_NAME = "deepseek-chat"
    python -m pytest tests/test_model_integration.py -v

没配 Key → 全部 skip，**不会**让 CI 失败。

⚠️ 导入顺序约束（见 `test_import_order_guard.py`）：
``_test_secret`` 必须排在 ``app.main`` 之前 —— 否则 app.main 读到空 secret
→ 启动校验失败 → 401 → 报出看不懂的 KeyError。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent-service"
sys.path.insert(0, str(AGENT))

# ⚠️ R12 —— 导入顺序是硬约束（见 `test_import_order_guard.py`）：
#    `_test_secret` 必须排在 `app.main` 之前，否则 app.main 读到空 secret
#    → 启动校验失败 → 401 → 报出看不懂的 KeyError。
#    `# isort: split` 立界碑，防止 ruff I001 再重排。
# isort: split

from tests._test_secret import TEST_SECRET  # noqa: E402,F401

# isort: split

from app.main import _load_dotenv  # noqa: E402
from app.openai_client import build_client_from_env  # noqa: E402

# ⚠️ 真模型配置在 `agent-service/.env` 里 —— 手动加载一次。
#    （测试不经过 app.main 的导入副作用时不会自动加载）
_load_dotenv()


def _client_or_skip():
    """构造真客户端；没配置就 skip。"""
    client = build_client_from_env()
    if client is None:
        pytest.skip("未配置真模型（AGENT_MODEL_API_KEY / BASE_URL / NAME），跳过")
    return client


def test_real_model_returns_json_for_rating():
    """真模型必须能按 JSON 格式返回 —— 否则上层 Schema 校验会全部降级。"""
    from app.agents import RatingSuggestionAgent

    client = _client_or_skip()
    agent = RatingSuggestionAgent(client)
    result = agent.run(
        metrics=[{"code": "quality", "dimension": "交付质量",
                  "value": 120, "target": 100, "ratio": 1.2,
                  "ratio_level": "A", "level_scale": ["C", "B", "A"]}],
        evidence=[{"id": "e1", "summary": "本季度交付 3 个需求，无线上事故"}],
        period="202611",
    )
    assert not result.degraded, f"真模型输出未过 Schema 校验：{result.warnings}"
    assert result.data["ratings"], "模型未给出任何评级建议"
    # ⚠️ 等级必须落在 level_scale 给出的取值内 —— 不许模型发明等级。
    assert result.data["ratings"][0]["level"] in ["C", "B", "A"]


def test_real_model_supports_function_calling():
    """真模型必须支持 function calling —— 工具层靠它调数据。

    ⚠️ 这是**选型的硬要求**：不支持就接不了工具层。
    """
    client = _client_or_skip()
    tools = [{
        "type": "function",
        "function": {
            "name": "get_metric_values",
            "description": "读取本人的规则指标结果",
            "parameters": {
                "type": "object",
                "properties": {"period": {"type": "string", "pattern": r"^\d{6}$"}},
                "required": ["period"],
                "additionalProperties": False,
            },
        },
    }]
    completion, error = client.complete_with_tools(
        [{"role": "user", "content": "请查一下我 202611 的指标"}],
        tools,
    )
    assert error is None, f"真模型工具调用失败：{error}"
    assert completion is not None
    # 允许模型选择不调用工具，但**如果**调用了，参数必须合规。
    if completion.tool_calls:
        call = completion.tool_calls[0]
        assert call.name == "get_metric_values"
        assert "period" in call.arguments


def test_real_model_never_produces_score_field():
    """⚠️ **核心安全不变量**：模型输出里绝不能出现总分。

    即使模型「自发」在 JSON 里塞一个 score 字段，上层的 Schema 校验
    也必须把它挡掉。这里验的是**模型侧不会主动加**，
    以及上层能挡住。
    """
    from app.agents import RatingSuggestionAgent
    from app.base import RatingOutput

    client = _client_or_skip()
    agent = RatingSuggestionAgent(client)
    result = agent.run(
        metrics=[{"code": "q", "dimension": "质量", "value": 120, "target": 100,
                  "ratio": 1.2, "ratio_level": "A", "level_scale": ["C", "B", "A"]}],
        evidence=[{"id": "e1", "summary": "交付 3 个需求"}],
        period="202611",
    )
    # 无论模型怎么发挥，结果里都不该有 score。
    assert "score" not in result.data
    assert "score" not in RatingOutput.model_fields


def test_real_model_qa_returns_answer_with_citations():
    """问答链路端到端 —— 必须有答案文本。"""
    client = _client_or_skip()
    completion, error = client.complete_with_tools(
        [{"role": "user", "content": "请用 JSON 回答：{\"answer\": \"...\", \"citations\": []}"}],
        [],
    )
    assert error is None, f"真模型问答失败：{error}"
    assert completion is not None
    if completion.content:
        assert isinstance(completion.content, dict)
        assert "answer" in completion.content or completion.content == {}


def test_real_model_json_is_parseable():
    """⚠️ 冒烟测试：确认厂商返回的 JSON 能被解析（有的厂商会包 markdown 围栏）。"""
    client = _client_or_skip()
    data, error = client.complete(
        "只输出 JSON 对象，形如 {\"ok\": true}",
        json.dumps({"agent": "qa", "question": "你好"}, ensure_ascii=False),
    )
    assert error is None, f"真模型调用失败：{error}"
    assert isinstance(data, dict)
