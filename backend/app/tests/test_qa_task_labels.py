"""`GET /v1/qa/tasks` 的任务标签（label）区分性回归。

⚠️ 这个测试锁定的是一个**真实发生过的产品缺陷**：

    真实库里存在 23 个**同名**任务（都叫「2026 年 11 月月度考核」）。
    原来的接口只返回 `name`，前端下拉框渲染出来就是一排一模一样的选项 ——
    用户完全无从选择，问答功能等于不可用。

    现在后端会生成带区分后缀的 `label`。本测试锁定两个契约：
      ① 同名任务的 label **互不相同**（唯一性 —— 第一优先级）
      ② 名字本来就唯一的任务，label **保持原样**（不要平白变长变丑）
"""

from backend.app.routes.qa import _decorate_labels


def _item(task_id: str, name: str, status: str = "created", created_at: str = "2026-11-14T19:21:51") -> dict:
    return {
        "task_id": task_id,
        "name": name,
        "period": "202611",
        "status": status,
        "scope": "self",
        "allowed_tools": [],
        "has_snapshot": False,
        "created_at": created_at,
    }


def test_unique_task_name_keeps_label_untouched():
    """名字唯一的任务 → label 就是名字本身。"""
    items = [_item("QA_DEMO", "2026 年 11 月月度考核（问答验证）")]
    _decorate_labels(items, None)
    assert items[0]["label"] == "2026 年 11 月月度考核（问答验证）"


def test_same_name_tasks_get_mutually_distinct_labels():
    """同名任务 → label 必须互不相同。"""
    items = [
        _item("TASK_20261114192151_202611", "2026 年 11 月月度考核"),
        _item("TASK_20261114192131_202611", "2026 年 11 月月度考核"),
        _item("TASK_20261114192124_202611", "2026 年 11 月月度考核"),
    ]
    _decorate_labels(items, None)
    labels = [item["label"] for item in items]
    assert len(set(labels)) == len(labels), f"label 必须互不相同，实际: {labels}"
    # 名字仍保留在 label 里（用户认得出这是什么）
    for label in labels:
        assert label.startswith("2026 年 11 月月度考核")


def test_labels_stay_distinct_even_with_identical_metadata():
    """⚠️ 最恶劣的情况：同名 + 同状态 + **同一秒**创建 + 同 ID 前缀。

    真实库里的 ID 形如 `TASK_<时间戳>_<周期>` —— 前 6 位恒为 `TASK_2`，
    完全无区分度。所以区分性**不能**依赖 ID 截断或时间戳。
    """
    items = [
        _item(f"TASK_20261114192151_20261{i}", "2026 年 11 月月度考核",
              status="created", created_at="2026-11-14T19:21:51")
        for i in range(5)
    ]
    _decorate_labels(items, None)
    labels = [item["label"] for item in items]
    assert len(set(labels)) == len(labels), f"label 必须互不相同，实际: {labels}"


def test_label_contains_status_and_department_hints():
    """区分后缀里应带上状态，便于用户一眼看出「哪个是本期」。"""
    items = [
        _item("T1", "同名任务", status="in_progress"),
        _item("T2", "同名任务", status="created"),
    ]
    _decorate_labels(items, None)
    assert "进行中" in items[0]["label"]
    assert "已创建" in items[1]["label"]


def test_empty_items_is_noop():
    items: list[dict] = []
    _decorate_labels(items, None)
    assert items == []
