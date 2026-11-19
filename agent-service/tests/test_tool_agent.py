"""不依赖网络的多轮调用、超时及降级测试。"""

import json
import threading
import time

from app.agents import QAAgent
from app.base import Completion, EchoModelClient, NullModelClient
from app.tools import ToolRegistry, ToolSpec


class ScriptClient:
    model = "script"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []
        self.tools = []

    def complete_with_tools(self, messages, tools):
        self.messages.append(messages)
        self.tools.append(tools)
        return next(self.responses), None


def call(name="get_metric_values", id="call_1", args=None):
    return Completion(tool_calls=[{"id": id, "name": name, "arguments": args or {}}])


def registry(handler=lambda _: {"metrics": []}):
    result = ToolRegistry()
    result.register(ToolSpec(name="get_metric_values", description="指标", parameters={
        "type": "object", "properties": {}, "additionalProperties": False,
    }, handler=handler))
    return result


def test_echo_cycle_and_existing_run_compatible():
    agent = QAAgent(EchoModelClient())
    result = agent.run_with_tools(registry(), question="本月如何", context={})
    assert not result.degraded and result.data["citations"] == []
    assert "离线" in result.data["answer"]
    assert not agent.run(question="本月如何", context={}).degraded


def test_tool_error_is_observation_and_can_recover():
    client = ScriptClient([call("unauthorized"), Completion(content={"answer": "资料不可用", "citations": []})])
    result = QAAgent(client).run_with_tools(registry(), question="?", context={})
    assert not result.degraded and result.warnings == ["TOOL_FORBIDDEN"]
    messages = client.messages[1]
    assert messages[-1]["tool_call_id"] == "call_1"
    assert json.loads(messages[-1]["content"])["error"] == "TOOL_FORBIDDEN"
    assert messages[-2]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_budgets_prevent_excess_calls():
    executed = []
    client = ScriptClient([Completion(tool_calls=[
        {"id": "a", "name": "get_metric_values", "arguments": {}},
        {"id": "b", "name": "get_metric_values", "arguments": {}},
    ])])
    agent = QAAgent(client)
    agent.max_tool_calls = 1
    result = agent.run_with_tools(registry(lambda args: executed.append(args) or {}), question="?", context={})
    assert result.degraded and result.warnings[-1] == "TOOL_CALL_LIMIT" and not executed
    agent = QAAgent(ScriptClient([call(), call(id="call_2")]))
    agent.max_steps = 2
    assert agent.run_with_tools(registry(), question="?", context={}).warnings[-1] == "STEP_LIMIT"


def test_duplicate_ids_and_bad_output_degrade():
    agent = QAAgent(ScriptClient([call(), call()]))
    assert agent.run_with_tools(registry(), question="?", context={}).warnings[-1] == "DUPLICATE_CALL_ID"
    agent = QAAgent(ScriptClient([Completion(content={"answer": "x", "score": 99})]))
    assert agent.run_with_tools(registry(), question="?", context={}).degraded
    assert QAAgent(NullModelClient()).run_with_tools(registry(), question="?", context={}).degraded


def test_model_and_tool_deadlines_return_without_waiting_for_worker():
    gate = threading.Event()
    class SlowClient:
        model = "slow"
        def complete_with_tools(self, messages, tools):
            gate.wait(2)
            return Completion(content={"answer": "late"}), None
    try:
        agent = QAAgent(SlowClient())
        agent.total_timeout_s = 0.03
        started = time.monotonic()
        result = agent.run_with_tools(registry(), question="?", context={})
        assert result.degraded and "TOTAL_TIMEOUT" in result.warnings
        assert time.monotonic() - started < 0.5
        agent = QAAgent(ScriptClient([call()]))
        agent.total_timeout_s = 0.03
        result = agent.run_with_tools(registry(lambda _: gate.wait(2)), question="?", context={})
        assert result.degraded
    finally:
        gate.set()


def test_request_whitelist_intersection_and_prompt_redaction():
    client = ScriptClient([Completion(content={"answer": "无工具", "citations": []})])
    result = QAAgent(client).run_with_tools(registry(), allow=set(), question="联系13812345678", context={})
    assert not result.degraded
    assert "13812345678" not in str(client.messages)
    assert client.tools == [[]]
