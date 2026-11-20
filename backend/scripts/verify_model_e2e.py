"""真模型端到端验证脚本（P3-2）。

打**真实 HTTP** 到运行中的 agent-service，用**真实模型**验三条链路：
1. ``/v1/agent/draft``   —— 评级建议 + 评语草稿（主管评分用）
2. ``/v1/agent/qa``      —— 问答（带工具调用）
3. ``/v1/agent/rating``  —— 单独评级建议

⚠️ **安全断言**：无论模型怎么发挥，输出里都**不能有分数字段**。

运行：
    python backend/scripts/verify_model_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_ENV = ROOT / "agent-service" / ".env"


def _load_env() -> None:
    """读 agent-service/.env。

    ⚠️ **.env 优先于环境变量** —— 与 agent-service 自身的加载顺序相反。

    原因：agent-service 的 ``_load_dotenv()`` 是「环境变量优先」
    （``if key not in os.environ``），而本脚本要连的是**正在运行的**那个服务。
    如果运行者的 shell 里残留了旧的 ``AGENT_SHARED_SECRET``
    （PowerShell 的环境变量会跨命令残留 —— 本仓库台账里专门记了这个坑），
    脚本就会用错的口令去连 → 401，而**真实原因完全指不到**。

    所以这里反过来：**文件里的值优先**，且把环境变量里的旧值清掉，
    保证脚本连的和服务加载的是同一份配置。
    """
    if not AGENT_ENV.exists():
        return
    for line in AGENT_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            # ⚠️ 覆盖（含覆盖环境变量里的旧值）—— 见上面的说明。
            os.environ[key] = value


_load_env()

BASE_URL = os.getenv("AGENT_SERVICE_URL", "http://127.0.0.1:8800")
SECRET = os.getenv("AGENT_SHARED_SECRET", "")

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        FAILURES.append(f"{label} —— {detail}")
        print(f"  [FAIL] {label} —— {detail}")


def post(path: str, payload: dict, timeout: int = 180) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {SECRET}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 后端会话（问答链路走后端，需要登录令牌）
# ---------------------------------------------------------------------------

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
ADMIN_USER = os.getenv("VERIFY_ADMIN_USER", "admin")
# ⚠️ 测试账号口令见 `backend/app/routes/auth.py::_TEST_USER_PLAINTEXT`
ADMIN_PASSWORD = os.getenv("VERIFY_ADMIN_PASSWORD", "admin123")


def _backend_post(path: str, payload: dict, *, token: str = "", timeout: int = 180) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{BACKEND_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _backend_login() -> str:
    """登录后端取会话令牌。"""
    data = _backend_post("/v1/auth/login", {
        "username": ADMIN_USER,
        "password": ADMIN_PASSWORD,
    })
    body = data.get("data", data)
    # 后端返回 camelCase（accessToken）
    return body.get("accessToken") or body.get("access_token") or body.get("token") or ""


def _discover_task(token: str) -> tuple[str, str]:
    """从后端找一个**真实存在**的考核任务（问答接口会 404 掉不存在的任务）。

    ⚠️ 不同端点的响应形状不一样：
    - 有的返回信封 ``{"code":0,"data":{"items":[...]}}``
    - 有的直接返回数组 ``[...]``
    所以两种都要处理。
    """
    for path in ("/v1/performance/scheme-records?limit=5",
                 "/v1/performance/records?limit=5",
                 "/v1/performance/tasks?limit=5"):
        try:
            request = urllib.request.Request(
                f"{BACKEND_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        body = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(body, dict):
            items = body.get("items") or body.get("records") or []
        elif isinstance(body, list):
            items = body
        else:
            continue
        for first in items:
            if not isinstance(first, dict):
                continue
            task_id = first.get("taskId") or first.get("task_id") or first.get("id") or ""
            period = first.get("period") or ""
            if task_id and period:
                return str(task_id), str(period)
    return "", ""


def main() -> int:
    print("=" * 70)
    print("真模型端到端验证（P3-2）")
    print(f"目标服务: {BASE_URL}")
    print(f"模型: {os.getenv('AGENT_MODEL_NAME', '?')}")
    print("=" * 70)

    if not SECRET:
        print("[FAIL] 未找到 AGENT_SHARED_SECRET")
        return 1

    # ------------------------------------------------------------------
    print("\n1. /v1/agent/draft —— 评级建议 + 评语草稿")
    draft_payload = {
        "task_id": "T_VERIFY_001",
        "employee": {
            "id": "E_VERIFY",
            "name": "张三",
            "department": "研发部",
            "position": "高级工程师",
        },
        "period": "202611",
        "metrics": [{
            "code": "quality",
            "dimension": "交付质量",
            "value": 120,
            "target": 100,
            "ratio": 1.2,
            "ratio_level": "A",
            "level_scale": [
                {"level": "C", "min": 0, "max": 0.8},
                {"level": "B", "min": 0.8, "max": 1.0},
                {"level": "A", "min": 1.0, "max": 999},
            ],
            "weight": 40,
            "evidence_ids": ["e1"],
        }],
        "evidence": [{
            "id": "e1",
            "kind": "plan_result",
            "summary": "本季度交付 3 个需求，全部一次验收通过，无线上事故。",
            "usage": "scoring",
        }],
        "attendance_anomalies": [],
        "rubric": [{
            "dimension": "交付质量",
            "scale": ["C", "B", "A"],
        }],
    }
    try:
        draft = post("/v1/agent/draft", draft_payload)
    except urllib.error.HTTPError as exc:
        print(f"  [FAIL] 请求失败 HTTP {exc.code}: {exc.read().decode('utf-8')[:300]}")
        return 1

    check("草稿接口未降级", not draft.get("degraded"),
          f"degraded=True, status={draft.get('agent_status')}")
    ratings = draft.get("ratings") or []
    check("给出了评级建议", len(ratings) > 0, f"ratings={ratings}")
    if ratings:
        first = ratings[0]
        check("评级建议含 dimension/level/reason",
              all(k in first for k in ("dimension", "level", "reason")),
              f"字段={list(first)}")
        check("等级落在 level_scale 内",
              first.get("level") in ("C", "B", "A"),
              f"level={first.get('level')}")
    comment = draft.get("comment_draft") or ""
    check("生成了评语草稿", len(comment.strip()) > 0, "comment_draft 为空")
    check("评语引用了证据 id", "e1" in (draft.get("citations") or []),
          f"citations={draft.get('citations')}")

    # ⚠️ 核心安全断言
    raw = json.dumps(draft, ensure_ascii=False)
    for forbidden in ("score", "total", "总分"):
        check(f"输出不含 '{forbidden}' 字段",
              f'"{forbidden}"' not in raw,
              "模型输出里出现了分数字段")
    check("响应顶层无 score 字段", "score" not in draft, f"keys={list(draft)}")

    print("\n   --- 模型产出的评级建议 ---")
    print(json.dumps(ratings, ensure_ascii=False, indent=2)[:900])
    print("\n   --- 模型产出的评语草稿 ---")
    print(comment[:600])

    # ------------------------------------------------------------------
    print("\n2. /v1/agent/rating —— 单独评级建议")
    try:
        rating = post("/v1/agent/rating", {
            "metrics": draft_payload["metrics"],
            "evidence": draft_payload["evidence"],
            "period": "202611",
        })
        check("评级接口未降级", not rating.get("degraded"), str(rating.get("degraded")))
        check("给出了评级建议", len(rating.get("ratings") or []) > 0, "ratings 为空")
        check("响应无 score 字段", "score" not in rating, f"keys={list(rating)}")
    except urllib.error.HTTPError as exc:
        check("评级接口可访问", False, f"HTTP {exc.code}")

    # ------------------------------------------------------------------
    print("\n3. /v1/qa/ask —— 后端 → 智能体 → 真模型（走完整链路）")
    print("   （问答需要真实任务 + 工具令牌；从后端登录取会话令牌）")
    try:
        token = _backend_login()
        check("后端登录成功", bool(token), "拿不到会话令牌")
        if token:
            task_id, period = _discover_task(token)
            check("找到可用的考核任务", bool(task_id), "库里没有可用任务")
            if task_id:
                qa = _backend_post("/v1/qa/ask", {
                    "question": "这个周期的指标达成情况如何？",
                    "task_id": task_id,
                    "period": period,
                }, token=token)
                answer = qa.get("answer") or ""
                check("问答有答案", len(answer.strip()) > 0, "answer 为空")
                check("问答未降级（真模型已接管）", not qa.get("degraded"),
                      f"degraded=True —— 原因：{qa.get('data_quality', {}).get('degrade_reason')}")
                check("答案里没有'暂不可用'这类降级文案",
                      "暂不可用" not in answer and "离线" not in answer,
                      f"answer={answer[:120]}")
                print("\n   --- 模型产出的答案 ---")
                print(answer[:700])
                print("\n   --- 数据完整度 ---")
                print(json.dumps(qa.get("data_quality", {}), ensure_ascii=False, indent=2)[:400])
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")[:300]
        check("问答链路可访问", False, f"HTTP {exc.code}: {body}")
    except Exception as exc:  # noqa: BLE001
        check("问答链路可访问", False, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    if FAILURES:
        print("\n失败明细：")
        for item in FAILURES:
            print(f"  - {item}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
