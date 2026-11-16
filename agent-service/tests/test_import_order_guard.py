"""R22 —— 护栏：agent-service 测试的 **secret 导入顺序**不可被自动排序破坏。

## 背景（一个极隐蔽的真实缺陷）

R22 把 ruff 门禁从「致命错误子集」收紧到配置里的全量规则集后，
``ruff --fix`` 跑了 ``I001``（unsorted-imports）**自动重排了导入**。

而 agent-service 的测试存在一条**隐式的导入顺序约束**：

- ``app/main.py`` 在**模块导入时**就读取并校验环境变量::

      _SERVICE_TOKEN = os.getenv("AGENT_SHARED_SECRET", "")   ← 导入即读
      _require_startup_secret()                              ← 导入即校验

- ``tests/_test_secret.py`` 负责 ``os.environ.setdefault("AGENT_SHARED_SECRET", ...)``。

所以**必须先导入 ``_test_secret``，再导入 ``app.main``**。

顺序一旦被 I001 颠倒：

    app.main 读到空 secret → 启动校验失败 / 请求 401 →
    响应里没有 fairness 字段 → 测试报 **KeyError: 'fairness'**

这个报错**完全指不到根因** —— 看起来像响应结构变了，
实际上是导入顺序被改了。排查它花了整整一轮。

## 本文件的作用

静态校验：凡是「同时用到 ``app.main`` 和 ``tests._test_secret``」的测试文件，
``_test_secret`` 的导入**必须**排在 ``app.main`` 之前。

这样即使有人再跑一次 ``ruff --fix --select I001``，
护栏也会立刻红掉，而不是等到线上/测试时炸出一个看不懂的 KeyError。
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def _import_line_index(text: str, module_suffix: str) -> int | None:
    """返回导入 ``module_suffix`` 的**第一条** import 语句的行号。

    只认真正的 import 语句（行首 ``from`` / ``import``），
    不认注释或字符串里的提及。
    """
    pattern = re.compile(rf"^\s*(?:from|import)\s+{re.escape(module_suffix)}\b")
    for idx, line in enumerate(text.splitlines()):
        if pattern.match(line):
            return idx
    return None


def _files_importing_app_main() -> list[Path]:
    out: list[Path] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if _import_line_index(text, "app.main") is not None:
            out.append(path)
    return out


def test_there_are_test_files_importing_app_main():
    """前提校验：确实有测试文件导入 ``app.main``（否则下面的护栏是空跑）。"""
    files = _files_importing_app_main()
    assert files, "没有测试导入 app.main —— 护栏可能已失效，请复核"


def test_secret_import_precedes_app_main_in_every_test():
    """⚠️ ``_test_secret`` 必须**先于** ``app.main`` 被导入。

    理由见模块 docstring：``app.main`` 导入即读 ``AGENT_SHARED_SECRET``，
    而 secret 是 ``tests/_test_secret.py`` 用 ``setdefault`` 写进环境的。
    顺序反了 → 空 secret → 401 → ``KeyError: 'fairness'``（报错完全指不到根因）。
    """
    offenders: list[str] = []
    for path in _files_importing_app_main():
        text = path.read_text(encoding="utf-8")
        secret_idx = _import_line_index(text, "tests._test_secret")
        app_main_idx = _import_line_index(text, "app.main")
        if secret_idx is None:
            offenders.append(f"{path.name}: 导入 app.main 但没导入 tests._test_secret")
            continue
        if secret_idx > app_main_idx:
            offenders.append(
                f"{path.name}: tests._test_secret (第 {secret_idx + 1} 行) "
                f"排在 app.main (第 {app_main_idx + 1} 行) **之后** —— 顺序反了"
            )

    assert not offenders, (
        "以下测试的导入顺序会让 app.main 读到空 AGENT_SHARED_SECRET：\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n请把 tests._test_secret 的导入移到 app.main 之前"
        "（并用 `# isort: split` 立界碑，防止 ruff I001 再重排）。"
    )


def test_import_order_guard_is_marked_with_isort_split():
    """⚠️ 关键文件必须用 ``# isort: split`` 立界碑。

    没有界碑的话，下一次 ``ruff --fix --select I001`` 会**再次**把
    ``app.main`` 提到 ``_test_secret`` 前面 —— 护栏会红，
    但如果没人注意，功能就悄悄坏了。

    ``# isort: split`` 告诉 ruff 的排序器：这两段 import 不要合并重排。
    """
    offenders: list[str] = []
    for path in _files_importing_app_main():
        text = path.read_text(encoding="utf-8")
        if "# isort: split" not in text:
            offenders.append(path.name)

    assert not offenders, (
        "以下测试缺少 `# isort: split` 界碑，ruff I001 可能再次颠倒导入顺序：\n"
        + "\n".join(f"  - {name}" for name in offenders)
    )


def test_app_main_reads_secret_at_import_time():
    """⚠️ 确认「导入即读 secret」这个前提仍然成立。

    这条护栏依赖 ``app/main.py`` 在模块级读取环境变量。
    如果哪天改成懒加载（函数内再读），上面的顺序约束就**不再必要**，
    这时应该把整套顺序护栏一并删掉 —— 而不是留着变成误导。

    保留这条测试，是为了让「前提变了」这件事**显式暴露**，而不是悄悄腐烂。
    """
    main_py = TESTS_DIR.parent / "app" / "main.py"
    assert main_py.exists(), f"找不到 {main_py}"

    text = main_py.read_text(encoding="utf-8")
    assert 'os.getenv("AGENT_SHARED_SECRET"' in text, (
        "app/main.py 不再在模块级读取 AGENT_SHARED_SECRET —— "
        "若改成了懒加载，请同步删除/调整 agent-service 的导入顺序护栏。"
    )
    assert "_require_startup_secret()" in text, (
        "app/main.py 不再在导入时校验 secret —— 导入顺序约束的前提已变，"
        "请同步复核 agent-service/tests 下的导入顺序护栏。"
    )
