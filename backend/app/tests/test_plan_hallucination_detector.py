"""计划场景幻觉检测器 —— pytest 门禁。

⚠️ **为什么必须有这个文件**：
压测的前提是「检测器准」。如果检测器瞎了，
「检出率 0%」就毫无意义 —— 可能只是没查到，而不是模型真的没问题。

所以把 ``check_plan_hallucination_detector.py`` 的自检**接进 pytest** ——
这样 CI 会自动跑它，检测器一旦退化就立刻红。

⚠️ **同时验证「检测器不是永远通过」** ——
``test_detector_is_not_vacuous`` 用**故意打坏的判据**验证自检能抓到退化。
只断言「自检全绿」的测试，本身可能是永远通过的。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "check_plan_hallucination_detector.py"
DETECTOR = ROOT / "backend" / "scripts" / "plan_hallucination_detector.py"


def _run_selfcheck() -> subprocess.CompletedProcess:
    """跑一遍检测器自检，返回结果。"""
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        shell=False,
    )


def test_detector_selfcheck_passes() -> None:
    """⚠️ 检测器自检必须全绿 —— 这是压测结论可信的前提。"""
    proc = _run_selfcheck()
    assert proc.returncode == 0, (
        "计划场景幻觉检测器自检未通过：\n"
        + proc.stdout[-3000:]
        + "\n"
        + proc.stderr[-2000:]
    )
    assert "0 失败" in proc.stdout, f"自检输出异常：\n{proc.stdout[-2000:]}"


def test_selfcheck_covers_every_defect_category() -> None:
    """⚠️ 自检必须覆盖**全部 8 类**缺陷 —— 漏测一类就等于没测。

    ⚠️ 为什么要按「类别」而不是按「用例数」检查：
    用例数可以被无意间调换（改一个用例、换一个用例，总数不变），
    但**类别集合**变了 —— 这是真正该被发现的退化。
    """
    sys.path.insert(0, str(SCRIPT.parent))
    from check_plan_hallucination_detector import CASES  # noqa: PLC0415

    covered: set[str] = set()
    for case in CASES:
        covered.update(case[3].keys())

    expected = {
        "assessment_number_leak",
        "score_leak",
        "fabricated_history",
        "fabricated_quotes",
        "fabricated_facts",
        "fabricated_numbers",
        "illegal_references",
        "fabricated_person_names",
    }
    missing = expected - covered
    assert not missing, (
        "以下缺陷类别没有自检用例覆盖：\n"
        + "\n".join(f"  - {m}" for m in sorted(missing))
        + "\n每类缺陷都要有「该报」和「不该报」两种用例 —— "
        "只有「该报」会误报，只有「不该报」会漏报。"
    )


def test_detector_is_not_vacuous() -> None:
    """⚠️ **检测器本身必须能被抓到退化**。

    这个测试**故意**打坏检测器的一个关键判据，
    然后确认自检会因此失败。

    ⚠️ 为什么必须这么做：
    一个「永远通过」的自检毫无价值 —— 它可能在检测器已经瞎了的时候
    依然报绿。必须证明「自检真的在检验检测器」。

    做法：复制检测器到临时目录，把「编造历史」的判据改成恒假，
    再用自检里的 ``CASES`` 去跑这个坏掉的副本，
    确认至少有一条用例失败。
    """
    sys.path.insert(0, str(SCRIPT.parent))
    from check_plan_hallucination_detector import CASES  # noqa: PLC0415
    from plan_hallucination_detector import detect_plan_hallucination  # noqa: PLC0415

    # ⚠️ 构造一个**故意失效**的判定：
    #    「编造历史」永远认为「有依据」→ 那些「该报」的用例必须失败。
    original = detect_plan_hallucination
    try:
        def _broken(items, findings=None, _orig=original, **kwargs):
            issues = _orig(items, findings, **kwargs)
            issues["fabricated_history"] = []
            return issues

        # 直接验证「坏掉的检测器」会被用例抓到
        failed = 0
        for case in CASES:
            label, items, findings, expect = case
            want = expect.get("fabricated_history")
            if want is None or want == 0:
                continue
            got = len(_broken(items, findings, **{
                "my_history": __import__(
                    "check_plan_hallucination_detector", fromlist=["MY_HISTORY"]
                ).MY_HISTORY,
                "dept_history": __import__(
                    "check_plan_hallucination_detector", fromlist=["DEPT_HISTORY"]
                ).DEPT_HISTORY,
                "legal_refs": ["plan-2026-10-001"],
            })["fabricated_history"])
            if got != want:
                failed += 1
        assert failed > 0, (
            "把「编造历史」判据打坏后，自检用例竟然仍然全过 —— "
            "说明这些用例**抓不到**该判据的退化，测试是空的。"
        )
    finally:
        del original


def test_detector_handles_empty_input() -> None:
    """⚠️ 空输入不得抛异常 —— 模型降级时会给空输出。"""
    sys.path.insert(0, str(SCRIPT.parent))
    from plan_hallucination_detector import detect_plan_hallucination  # noqa: PLC0415

    issues = detect_plan_hallucination([], [], my_history=None, dept_history=None)
    assert isinstance(issues, dict)
    assert all(isinstance(v, list) for v in issues.values())
    assert not any(issues.values()), "空输入不该报任何问题"


def test_detector_handles_malformed_items() -> None:
    """⚠️ 畸形结构不得抛异常 —— 模型可能返回非字典条目。"""
    sys.path.insert(0, str(SCRIPT.parent))
    from plan_hallucination_detector import detect_plan_hallucination  # noqa: PLC0415

    issues = detect_plan_hallucination(
        ["not a dict", None, 123],
        [{"kind": "duplicate"}, "not a dict"],
        my_history={"items": ["bad"], "plans": None},
        dept_history={"items": [None]},
    )
    assert isinstance(issues, dict)


def _load_plan_agent():
    """按**包**加载 ``agent-service`` 的 ``plan_agent`` 模块。

    ⚠️ **为什么不能用普通 import** ——
    pytest 会把 ``backend/`` 插到 ``sys.path[0]``，
    于是 ``import app.plan_agent`` 会解析到 ``backend/app/``（后端的应用包），
    而不是 ``agent-service/app/``（智能体服务的包）——
    两者**同名**，这是仓库根 conftest.py 注释里明确警告过的解析陷阱。

    ⚠️ **也不能用裸的 ``spec_from_file_location``** ——
    ``plan_agent.py`` 里有 ``from .tool_agent import ToolUsingAgent``
    这种**相对导入**，单文件加载没有包上下文会直接
    ``ImportError: attempted relative import with no known parent package``。

    正确做法：把 ``agent-service`` 整个目录当作一个**具名包**加载，
    用 ``submodule_search_locations`` 指定它的搜索位置，
    这样相对导入就能正常解析。
    """
    import importlib.util

    agent_service_dir = ROOT / "agent-service"

    # ⚠️ 先把 agent-service 挂到 sys.path，让包内**绝对导入**也能解析
    added = str(agent_service_dir) not in sys.path
    if added:
        sys.path.append(str(agent_service_dir))

    try:
        # 用一个**不会与真实包名冲突**的别名加载整个包
        spec = importlib.util.spec_from_file_location(
            "_agent_service_app",
            agent_service_dir / "app" / "__init__.py",
            submodule_search_locations=[str(agent_service_dir / "app")],
        )
        assert spec and spec.loader
        package = importlib.util.module_from_spec(spec)
        sys.modules["_agent_service_app"] = package
        spec.loader.exec_module(package)

        module_spec = importlib.util.spec_from_file_location(
            "_agent_service_app.plan_agent",
            agent_service_dir / "app" / "plan_agent.py",
        )
        assert module_spec and module_spec.loader
        module = importlib.util.module_from_spec(module_spec)
        sys.modules["_agent_service_app.plan_agent"] = module
        module_spec.loader.exec_module(module)
        return module
    finally:
        if added:
            sys.path.remove(str(agent_service_dir))


def test_plan_agent_prompt_forbids_assessment_numbers() -> None:
    """⚠️ 回归：计划助手的提示词必须**明确禁止**产出考核数字。

    ⚠️ 为什么这条要在测试里锁住：
    检测器能**发现**模型产出了考核数字，但如果提示词没禁止，
    那就是**设计缺陷** —— 靠事后拦截不如事前约束。
    """
    module = _load_plan_agent()

    # ⚠️ ``system_prompt`` 是**实例方法**，但它**不依赖** client ——
    #    所以直接用未绑定方法调用（``PlanAgent.system_prompt(None)``），
    #    不必构造一个真实 client（那会牵扯模型配置）。
    prompt = module.PlanAgent.system_prompt(None)

    assert "performanceWeight" in prompt, "提示词必须点名禁止 performanceWeight"
    assert "estimatedValue" in prompt, "提示词必须点名禁止 estimatedValue"
    # 必须有「不许编造工具没返回的历史计划」这条
    assert "历史计划" in prompt, "提示词必须提到历史计划"
    assert "发明" in prompt or "编造" in prompt, (
        "提示词必须明确禁止编造/发明工具没返回的历史计划"
    )


@pytest.mark.parametrize(
    "field",
    [
        "performanceWeight",
        "performance_weight",
        "estimatedValue",
        "estimated_value",
        "score",
        "completionRate",
    ],
)
def test_forbidden_item_fields_are_all_blocked(field: str) -> None:
    """⚠️ 每个禁止字段都要能被检出 —— 漏一个就等于没禁。"""
    sys.path.insert(0, str(SCRIPT.parent))
    from plan_hallucination_detector import detect_plan_hallucination  # noqa: PLC0415

    issues = detect_plan_hallucination(
        [{"task_name": "任务", field: 42}],
        [],
        my_history={},
        dept_history={},
    )
    assert issues["assessment_number_leak"], f"{field} 未被检出为考核数字泄漏"
