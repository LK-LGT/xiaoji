"""在实现方案文档里插入 P4 章节。

⚠️ **为什么用脚本而不是 Write 工具**
文档很长（1700+ 行），用脚本按锚点插入更可控，
也不会因为整篇重写而意外损坏其他章节。

⚠️ **编码**：显式 utf-8，绝不用 PowerShell 的 Add-Content。
"""

from __future__ import annotations

import sys
from pathlib import Path

DOC = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "短视频剪辑工作量统计-实现方案.md"
)

ANCHOR = "## 十、百度 API 做不到 / 有风险的部分（**结论汇总**）"

SECTION = """## 九·九、**P4 实施结果（2026-11-21 已完成）**

### P4.1 后端 REST API（`backend/app/routes/pan_workload.py`）

19 个端点，挂在 `/v1/baidu/workload`：

| 分类 | 端点 | 说明 |
|---|---|---|
| 查询 | `GET /summary` | 面板总览（按人汇总 + 异常 + 待办） |
| 查询 | `GET /daily` | 日汇总（按人 / 按月） |
| 查询 | `GET /files` | 文件明细（**审计根**） |
| 查询 | `GET /bindings` | 目录绑定列表 |
| 查询 | `GET /manual-metrics` | 人工指标列表 |
| 查询 | `GET /reviews` | 工单列表 |
| 查询 | `GET /reviews/{id}` | 工单详情（争议项 + 附件 + 事件流 + 链校验） |
| 查询 | `GET /reviews/{id}/chain` | 事件链校验 |
| 查询 | `GET /reviews/{id}/artifacts/{aid}` | 证据下载（**校验归属**） |
| 写 | `POST /bindings` | 配目录绑定（**目录不能绑给两个人**） |
| 写 | `POST /collect` | 手动触发采集（**HR/管理员**） |
| 写 | `POST /manual-metrics` | 人工填报（前端上传通路） |
| 写 | `POST /manual-metrics/{id}/artifacts` | 指标证据上传 |
| 写 | `POST /aggregate` | 月度聚合写 `MetricValue`（**HR/管理员**） |
| 写 | `POST /reviews` | 发起工单（**只能给自己发起**） |
| 写 | `POST /reviews/{id}/items` | 加争议项 |
| 写 | `POST /reviews/{id}/artifacts` | 上传证据 |
| 写 | `POST /reviews/{id}/items/{iid}/resolve` | 裁定争议项（**主管**） |
| 写 | `POST /reviews/{id}/resolve` | 裁定工单 + 写回日汇总（**主管**） |

#### 权限模型（★ 数据范围一律后端收敛）

| 角色 | 可看 | 可写 |
|---|---|---|
| 员工 | **仅自己** | 发起自己的工单、填自己的指标 |
| 主管 | 自己管的部门全员 | 裁定工单 / 争议项 |
| HR / 管理员 | **全部** | 绑定、触发采集、月度聚合 |

⚠️ **前端权限位只管「谁能打开页面」，不是安全措施** ——
后端 `_scope_employee_ids` / `_require_leader` / `_require_admin` 才是真正的闸门。

⚠️ **`list_daily` 的过滤是「两个条件都要生效」** ——
`allowed`（能看谁）与 `employee_id`（看谁）是 **AND** 关系。
写成 `elif` 会让主管传了 `employee_id` 就**绕过**范围限制。

### P4.2 前端（Vue 3 + Element Plus）

| 文件 | 职责 |
|---|---|
| `frontend/src/api/panWorkload.ts` | API 封装 + 类型定义 |
| `frontend/src/views/performance/PanWorkloadPanel.vue` | 主管视角：按人汇总 + 待裁定工单 + 人工指标 + 工单详情抽屉 |
| `frontend/src/views/performance/PanWorkloadEntry.vue` | 员工视角：我的工作量 + 人工填报 + 提异议抽屉 |
| `frontend/src/views/performance/PanWorkloadPage.vue` | 按权限组合上面两个 |
| `frontend/src/router/index.ts` | 路由 `/performance/pan-workload` |
| `frontend/src/navigation/workspaces.ts` | 菜单（**4 个工作空间各挂一份**：员工/直属领导/部门负责人/绩效管理） |

#### 权限位（新增）

| 权限位 | 角色 |
|---|---|
| `pan:workload:view` | employee / manager / department_leader / hr / admin |
| `pan:workload:submit` | employee |
| `pan:workload:resolve` | manager / department_leader / hr / admin |
| `pan:workload:manage` | hr / admin |

### P4.3 实测结论（浏览器实测，非仅编译通过）

| 场景 | 结果 |
|---|---|
| 主管看「按人汇总」 | ✅ 20 条 / 4 天 / 1 天裁定 / 1 天异议 |
| 主管裁定争议项 | ✅ 状态「成立」、最终权重 1、事件链 +1 条且**首尾相接** |
| 主管整体裁定 | ✅ 状态「已裁定（成立）」、裁定后 = 8 |
| 写回日汇总 | ✅ `total=6`（**原值未被覆盖**）+ `adjusted=8` + `status=confirmed` |
| 员工提异议 | ✅ 工单落库（主张 7 / 系统判定 5），日汇总 `auto` → `disputed` |
| 员工视角隔离 | ✅ 员工**看不到**主管面板，且只看到自己的数据 |
| 证据下载 | ✅ blob 方式（自动带 token，不把 token 拼进 URL） |

### P4.4 踩过的坑（⚠️ 值得记住）

| # | 坑 | 症状 | 正解 |
|---|---|---|---|
| **1** | **axios 响应拦截器已解包** | `http.get(...).then(r => r.data)` ⇒ `undefined` ⇒ **界面显示「暂无数据」，但不报错** | 用**双泛型** `http.get<T, T>(...)`，**不要**再取 `.data` |
| **2** | **前端路径不带 `/v1`** | vite 代理把 `/api/xxx` → `/v1/xxx`；写 `/v1/xxx` ⇒ `/v1/v1/xxx` ⇒ **404** | 前端 API 一律写 `/baidu/workload` |
| **3** | **服务层 snake_case 泄漏到 API** | 前端读 `eventCount` 拿到 `undefined` ⇒ 「0 条事件」、hash 空白，**不报错** | 路由层用 `_serialize_chain` 统一转 camelCase |
| **4** | **`Add-Content` 写中文** | PowerShell 按 ANSI(GBK) 写入 ⇒ 追加部分中文全变 mojibake | 用 `Write` 工具或 Python（显式 utf-8）；本仓库已加 `scripts/fix_p4_encoding.py` 兜底 |
| **5** | **FastAPI 依赖覆盖传 lambda** | `lambda: _db_iter(db)` ⇒ 下游拿到 generator ⇒ `session.execute` 报 `AttributeError` | override 必须是**生成器函数本身** |

### P4.5 测试结果

| 套件 | 项数 | 结果 |
|---|---|---|
| `test_pan_workload_api.py`（P4 新增） | 30 | ✅ |
| **BP-2 小计** | **172** | ✅ |
| **全量后端** | **2762 passed / 55 skipped** | ✅ **无回归** |
| 前端 `vue-tsc --noEmit` | 0 error | ✅ |
| 前端 `vite build` | ✅ built | ✅ |

⚠️ **权限测试要「能抓到越权」**
只断言「无权时返回 403」是**否定式断言** ——
把整个权限检查删掉也照样通过 ⇒ **空转**。
⇒ 必须同时断言「有权时能通过」
（如 `test_员工不能裁定工单` ↔ `test_主管能裁定工单`）。

"""


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    if "九·九、**P4 实施结果" in text:
        print("[SKIP] P4 章节已存在")
        return 0
    if ANCHOR not in text:
        print(f"[FAIL] 找不到锚点：{ANCHOR}")
        return 1
    text = text.replace(ANCHOR, SECTION + ANCHOR, 1)

    # 更新实施顺序表里的 P4 行
    old_p4 = "| **P4**（1 天） | 前端面板 + 复核抽屉 + 告警展示 + 未匹配清单 | 可交付给运营 |"
    new_p4 = (
        "| **P4**（1 天） | ✅ **已完成** —— REST API（19 端点）+ 权限收敛 + "
        "主管面板 + 员工填报 + 工单抽屉 + 浏览器实测（§九·九） | 可交付给运营 |"
    )
    if old_p4 in text:
        text = text.replace(old_p4, new_p4, 1)
    else:
        print("[WARN] 未找到 P4 进度行")

    DOC.write_text(text, encoding="utf-8")
    print("[OK] P4 章节已插入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
