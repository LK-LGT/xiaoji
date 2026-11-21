"""计划场景幻觉检测器的自检 —— 先证明「检测器本身能抓到」。

⚠️ 压测的前提是：**检测器准**。
如果检测器抓不到已知编造，那「检出率 0%」就毫无意义
（可能只是检测器瞎了）。

这个文件用**人工构造的已知案例**验证检测器：
每个案例的期望结果写在 ``expect`` 里。

⚠️ **为什么要单独一个文件**，而不是复用
``check_hallucination_detector.py``：
计划场景的判定口径**完全不同**（见 ``plan_hallucination_detector.py``
的模块说明）—— 数据源、输出形状、数字语义、红线种类都不一样。
共用一个文件会让两套口径互相污染。

## 运行

    python backend/scripts/check_plan_hallucination_detector.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from plan_hallucination_detector import detect_plan_hallucination  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 测试数据 —— 模拟「工具真实返回的历史计划」
# ─────────────────────────────────────────────────────────────────────────────

#: 我的历史计划（工具 ``get_my_plan_history`` 的真实返回形状）
MY_HISTORY = {
    "available": True,
    "planType": "month",
    "range": {"from": "2026-05", "to": "2026-11"},
    "planCount": 2,
    "itemCount": 3,
    "plans": [
        {"planMonth": "2026-10", "title": "十月工作计划", "status": "APPROVED",
         "projectName": "订单中心", "positionName": "后端工程师"},
        {"planMonth": "2026-08", "title": "八月工作计划", "status": "APPROVED",
         "projectName": "订单中心", "positionName": "后端工程师"},
    ],
    "items": [
        {"planMonth": "2026-10", "workType": "TASK", "taskName": "订单模块重构",
         "taskContent": "拆分订单中心聚合根，拆分支付与库存子域",
         "deliverable": "可独立发布的订单子服务", "quality": "单元测试覆盖率不低于 80%",
         "estimatedStartDate": "2026-10-08", "estimatedEndDate": "2026-10-31",
         "status": "APPROVED"},
        {"planMonth": "2026-10", "workType": "TASK", "taskName": "需求评审",
         "taskContent": "组织订单中心需求评审会并输出评审纪要",
         "deliverable": "评审纪要文档", "quality": "覆盖全部验收标准",
         "estimatedStartDate": "2026-10-09", "estimatedEndDate": "2026-10-12",
         "status": "APPROVED"},
        {"planMonth": "2026-08", "workType": "TASK", "taskName": "库存对账",
         "taskContent": "梳理库存与账面差异并输出对账报表",
         "deliverable": "对账报表", "quality": "差异项全部可追溯",
         "estimatedStartDate": "2026-08-05", "estimatedEndDate": "2026-08-20",
         "status": "APPROVED"},
    ],
    "note": "",
}

#: 部门历史计划（工具 ``get_department_plan_history`` 的真实返回形状）
#:
#: ⚠️ **人名已脱敏** —— 真实姓名被替换成「同事A/B/C」。
#:    这是判定「编造人名」的基准。
DEPT_HISTORY = {
    "available": True,
    "planCount": 1,
    "itemCount": 1,
    "plans": [{"planMonth": "2026-09", "title": "九月工作计划", "status": "APPROVED"}],
    "items": [
        {"planMonth": "2026-09", "workType": "TASK", "taskName": "支付网关接入",
         "taskContent": "同事A 负责支付网关接入与联调",
         "deliverable": "支付网关上线", "quality": "支付成功率不低于 99%",
         "estimatedStartDate": "2026-09-01", "estimatedEndDate": "2026-09-28",
         "status": "APPROVED"},
    ],
    "note": "",
}

LEGAL_REFS = ["plan-2026-10-001"]

# ─────────────────────────────────────────────────────────────────────────────
# 用例
# ─────────────────────────────────────────────────────────────────────────────
#
# 结构：(说明, items, findings, 期望)
# 期望里只写**要断言的键** —— 没写的键不检查。

CASES = [
    # ─────────────────────────────────────────────────────────────
    # 1. 考核数字泄漏（计划场景头号红线）
    # ─────────────────────────────────────────────────────────────
    (
        "模型填了 performanceWeight → 必须报（结构字段）",
        [{"task_name": "订单模块重构", "performanceWeight": 30}],
        [],
        {"assessment_number_leak": 1},
    ),
    (
        "模型填了 estimatedValue → 必须报（结构字段）",
        [{"task_name": "订单模块重构", "estimatedValue": 100000}],
        [],
        {"assessment_number_leak": 1},
    ),
    (
        "模型填了 score → 必须报（结构字段）",
        [{"task_name": "订单模块重构", "score": 95}],
        [],
        {"assessment_number_leak": 1},
    ),
    (
        "模型把权重写成 0 → 不算泄漏（后端就是固定 0）",
        [{"task_name": "订单模块重构", "performanceWeight": 0}],
        [],
        {"assessment_number_leak": 0},
    ),
    (
        "模型文本里写「权重为 30 分」→ 必须报（文本表述）",
        [{"task_name": "订单模块重构", "task_content": "该任务权重为 30 分"}],
        [],
        {"assessment_number_leak": 1},
    ),
    (
        "模型文本里写「完成率 80%」→ 必须报（计划还没执行，不该有完成率）",
        [{"task_name": "订单模块重构", "task_content": "预期完成率 80%"}],
        [],
        {"assessment_number_leak": 1},
    ),
    (
        "模型文本里写「预估价值 10 万元」→ 必须报",
        [{"task_name": "订单模块重构", "task_content": "预估价值 10 万元"}],
        [],
        {"assessment_number_leak": 1},
    ),
    (
        "正常计划条目不含任何考核数字 → 不该报",
        [{"task_name": "订单模块重构", "task_content": "拆分订单中心聚合根",
          "deliverable": "可独立发布的订单子服务", "quality": "单元测试覆盖率不低于 80%"}],
        [],
        {"assessment_number_leak": 0},
    ),
    (
        # ⚠️ 「质量标准里写覆盖率 80%」是**合法**的 ——
        #    「80%」是质量目标，不是考核数字，且原文里就有。
        "质量标准里的覆盖率数字不算考核数字泄漏",
        [{"task_name": "订单模块重构", "quality": "单元测试覆盖率不低于 80%"}],
        [],
        {"assessment_number_leak": 0, "fabricated_numbers": 0},
    ),
    # ─────────────────────────────────────────────────────────────
    # 2. 分数泄漏
    # ─────────────────────────────────────────────────────────────
    (
        "文本里出现「绩效分 95」→ 必须报",
        [{"task_name": "订单模块重构", "task_content": "参考以往，个人绩效分 95"}],
        [],
        {"score_leak": 1},
    ),
    (
        "文本里出现「考核得分 88 分」→ 必须报",
        [{"task_name": "订单模块重构", "task_content": "该任务考核得分 88 分"}],
        [],
        {"score_leak": 1},
    ),
    (
        "提到等级不算泄漏",
        [{"task_name": "订单模块重构", "task_content": "建议评级为 A 级"}],
        [],
        {"score_leak": 0},
    ),
    (
        # ⚠️ 总结场景里「质量得分：实际 88 分」是**指标名称**，不报。
        #    计划场景同理 —— 但计划里没有指标，所以这里用别的措辞。
        "任务描述里的「得分点」不算分数泄漏",
        [{"task_name": "订单模块重构", "task_content": "按得分点逐条验收"}],
        [],
        {"score_leak": 0},
    ),
    # ─────────────────────────────────────────────────────────────
    # 3. 编造历史计划
    # ─────────────────────────────────────────────────────────────
    (
        "模型说「之前做过支付网关接入」→ 必须报（我的历史里没有）",
        [{"task_name": "支付网关接入", "task_content": "沿用之前做过的支付网关接入经验"}],
        [],
        {"fabricated_history": 1},
    ),
    (
        "模型说「上个月做过订单模块重构」→ 不该报（原文有）",
        [{"task_name": "继续优化", "task_content": "延续上个月做过的订单模块重构"}],
        [],
        {"fabricated_history": 0},
    ),
    (
        "模型说「历史计划里有需求评审」→ 不该报（原文有）",
        [{"task_name": "继续优化", "task_content": "参考历史计划中的需求评审环节"}],
        [],
        {"fabricated_history": 0},
    ),
    (
        # ⚠️ 只有一个锚点 → 不足以证明有依据 → 判编造
        "模型说「以前做过数据迁移」→ 必须报（原文只有「库存对账」等）",
        [{"task_name": "数据迁移", "task_content": "复用以前做过的数据迁移方案"}],
        [],
        {"fabricated_history": 1},
    ),
    (
        # ⚠️ 普通任务描述里没有历史语境词 → 不该被当成「声称做过」
        "普通任务描述（无历史语境）不算编造历史",
        [{"task_name": "数据迁移", "task_content": "实现订单数据迁移工具"}],
        [],
        {"fabricated_history": 0},
    ),
    (
        # \u26a0\ufe0f\u26a0\ufe0f **\u5b9e\u6d4b\u8e29\u5751\uff082026-11-21 \u771f\u673a\u538b\u6d4b\uff09**\u2014\u2014
        #    \u6a21\u578b\u5728 findings \u91cc\u5982\u5b9e\u6c47\u62a5\u300c\u6ca1\u6709\u67e5\u5230\u5386\u53f2\u8ba1\u5212\u300d
        #    \u8fd9\u662f **\u5b88\u89c4\u77e9\u7684\u8868\u73b0**\uff08\u5de5\u5177\u8fd4\u56de\u7a7a\uff0c\u6a21\u578b\u5982\u5b9e\u8bf4\u7a7a\uff09\uff0c
        #    \u4f46\u56e0\u4e3a\u5e26\u300c\u67e5\u8be2\u300d\u8bcd\u800c\u88ab\u5f53\u6210\u300c\u58f0\u79f0\u505a\u8fc7\u300d\u2192\u8bef\u62a5\u3002
        "\u5982\u5b9e\u6c47\u62a5\u300c\u6ca1\u6709\u67e5\u5230\u300d\u4e0d\u7b97\u7f16\u9020\u5386\u53f2",
        [{"task_name": "\u9700\u6c42\u8bc4\u5ba1", "task_content": "\u7ec4\u7ec7\u9700\u6c42\u8bc4\u5ba1\u4f1a"}],
        [{"kind": "other", "severity": "info", "target_index": -1,
          "message": "\u6ca1\u6709\u67e5\u5230\u5386\u53f2\u8ba1\u5212", "suggestion": "\u8bf7\u8865\u5145"}],
        {"fabricated_history": 0},
    ),
    (
        # \u26a0\ufe0f **findings \u91cc\u7684\u300c\u4f53\u68c0\u7ed3\u8bba\u300d\u4e0d\u662f\u5386\u53f2\u58f0\u79f0** \u2014\u2014
        #    \u5b9e\u6d4b\u8bef\u62a5\uff1a\u300c\u6c89\u6dc0\u8bc4\u5ba1\u68c0\u67e5\u9879\u4e0e\u6a21\u677f\u300d
        #    \u300c\u8f93\u51fa\u4ea4\u4ed8\u7269\u300d\u300c\u8d28\u91cf\u62bd\u67e5\u300d\u300c\u9886\u57df\u8fb9\u754c\u5212\u5206\u300d
        #    \u2014\u2014 \u5b83\u4eec\u662f findings \u7684 message/suggestion\uff0c\u4e0d\u662f\u300c\u6211\u505a\u8fc7\u4ec0\u4e48\u300d\u3002
        "findings \u91cc\u7684\u4f53\u68c0\u7ed3\u8bba\u4e0d\u7b97\u7f16\u9020\u5386\u53f2",
        [],
        [{"kind": "other", "severity": "info", "target_index": 0,
          "message": "\u6c89\u6dc0\u8bc4\u5ba1\u68c0\u67e5\u9879\u4e0e\u6a21\u677f",
          "suggestion": "\u8f93\u51fa\u4ea4\u4ed8\u7269"}],
        {"fabricated_history": 0},
    ),
    (
        # \u26a0\ufe0f **\u300c\u6211\u4e4b\u524d\u505a\u8fc7 X\u300d\u5fc5\u987b\u62a5** \u2014\u2014 \u8fd9\u662f\u6700\u5177\u4e25\u91cd\u7684\u4f8b\u5b50\uff0c
        #    \u4e0d\u80fd\u88ab\u65b0\u7684\u5f3a\u8bed\u5883\u8bcd\u8868\u8bef\u4f24\u3002
        "\u300c\u6211\u4e4b\u524d\u505a\u8fc7\u652f\u4ed8\u7f51\u5173\u63a5\u5165\u300d\u5fc5\u987b\u62a5",
        [{"task_name": "\u652f\u4ed8\u7f51\u5173", "task_content": "\u6211\u4e4b\u524d\u505a\u8fc7\u652f\u4ed8\u7f51\u5173\u63a5\u5165"}],
        [],
        {"fabricated_history": 1},
    ),
    (
        # \u26a0\ufe0f \u201c\u6cbf\u7528\u4e4b\u524d\u7684\u505a\u6cd5\u201d\u4e5f\u662f\u58f0\u79f0\u2014\u2014
        "\u300c\u6cbf\u7528\u4e4b\u524d\u7684\u505a\u6cd5\u300d\u5fc5\u987b\u62a5\uff08\u539f\u6587\u6ca1\u6709\u8fd9\u4e2a\u505a\u6cd5\uff09",
        [{"task_name": "\u65b0\u4efb\u52a1", "task_content": "\u6cbf\u7528\u4e4b\u524d\u7684\u505a\u6cd5"}],
        [],
        {"fabricated_history": 1},
    ),
    # ─────────────────────────────────────────────────────────────
    # 4. 编造引文
    # ─────────────────────────────────────────────────────────────
    (
        "引号里的原文确实存在 → 不该报",
        [{"task_name": "订单模块重构", "task_content": "延续「拆分订单中心聚合根」的做法"}],
        [],
        {"fabricated_quotes": 0},
    ),
    (
        "引号里是原文没有的 → 必须报",
        [{"task_name": "新任务", "task_content": "延续「完成支付网关全链路重构」的做法"}],
        [],
        {"fabricated_quotes": 1},
    ),
    (
        "模型自己概括的流程（带破折号）→ 不该报",
        [{"task_name": "新任务", "task_content": "沿用「拆分—重构—联调」的方式推进"}],
        [],
        {"fabricated_quotes": 0},
    ),
    # ─────────────────────────────────────────────────────────────
    # 5. 编造数字 —— 计划场景的日期是重灾区
    # ─────────────────────────────────────────────────────────────
    (
        "ISO 日期 → 不该报",
        [{"task_name": "订单模块重构", "estimated_start_date": "2026-11-20",
          "estimated_end_date": "2026-12-15"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        "中文日期 → 不该报",
        [{"task_name": "订单模块重构", "task_content": "2026年11月20日启动，12月15日交付"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        "无年份日期 → 不该报",
        [{"task_name": "订单模块重构", "task_content": "11月20日启动"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        "月粒度（2026-11）→ 不该报",
        [{"task_name": "订单模块重构", "task_content": "参考 2026-11 的计划安排"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        "周期码 202610 → 不该报",
        [{"task_name": "订单模块重构", "task_content": "计划周期 202610 的条目"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        "「第 3 项」序数 → 不该报",
        [{"task_name": "订单模块重构", "task_content": "对应第 3 项交付要求"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        "日期区间 → 不该报",
        [{"task_name": "订单模块重构", "task_content": "2026-11-20 至 2026-12-15 期间推进"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        "真正编造的数字 → 必须报",
        [{"task_name": "订单模块重构", "task_content": "预计处理 87 笔订单"}],
        [],
        {"fabricated_numbers": 1},
    ),
    (
        "原文里有的数字 → 不该报（80% 覆盖率）",
        [{"task_name": "订单模块重构", "quality": "单元测试覆盖率不低于 80%"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        # ⚠️ 区间里的数字（档位边界 / 区间描述）→ 不该报
        "区间数字 → 不该报",
        [{"task_name": "订单模块重构", "quality": "覆盖率控制在 60% 到 80% 之间"}],
        [],
        {"fabricated_numbers": 0},
    ),
    # ─────────────────────────────────────────────────────────────
    # 6. 编造事实（荣誉/奖项）
    # ─────────────────────────────────────────────────────────────
    (
        "模型编造「年度优秀员工」→ 必须报",
        [{"task_name": "订单模块重构", "task_content": "该任务让我获得了年度优秀员工称号"}],
        [],
        {"fabricated_facts": 1},
    ),
    (
        # ⚠️ 「晋升」和「加薪」是**两处独立**的编造（不重叠、不相同）
        #    —— 计数必须按「独立编造点」算，不是按「命中模式数」算。
        "模型编造「晋升/加薪」→ 报 2 处",
        [{"task_name": "订单模块重构", "task_content": "该项目带来了晋升与加薪"}],
        [],
        {"fabricated_facts": 2},
    ),
    # ─────────────────────────────────────────────────────────────
    # 7. 非法引用
    # ─────────────────────────────────────────────────────────────
    (
        "引用了不存在的 UUID → 必须报",
        [{"task_name": "订单模块重构", "task_content": "参考 00000000-0000-4000-8000-000000000000"}],
        [],
        {"illegal_references": 1},
    ),
    (
        "引用了合法 id → 不该报",
        [{"task_name": "订单模块重构", "task_content": "参考 plan-2026-10-001 这条记录"}],
        [],
        {"illegal_references": 0},
    ),
    # ─────────────────────────────────────────────────────────────
    # 8. 编造人名（计划场景特有）
    # ─────────────────────────────────────────────────────────────
    (
        "模型输出了真实人名（工具已脱敏）→ 必须报",
        [{"task_name": "支付网关接入", "task_content": "参考同事李明的做法"}],
        [],
        {"fabricated_person_names": 1},
    ),
    (
        "模型输出的是脱敏占位符 → 不该报",
        [{"task_name": "支付网关接入", "task_content": "参考同事A 的做法"}],
        [],
        {"fabricated_person_names": 0},
    ),
    (
        # ⚠️ 常见词里带姓氏字 → 不能误判成人名
        "「方案」这类常见词不能误判成人名",
        [{"task_name": "订单模块重构", "task_content": "本次方案需要评审"}],
        [],
        {"fabricated_person_names": 0},
    ),
    # ─────────────────────────────────────────────────────────────
    # 9. 交叉：红线组合
    # ─────────────────────────────────────────────────────────────
    (
        "同时踩多条红线 → 每类各计一次",
        [{"task_name": "支付网关接入", "task_content": "延续「完成支付网关全链路重构」，权重 30 分",
          "performanceWeight": 30}],
        [],
        {"assessment_number_leak": 2, "fabricated_quotes": 1},
    ),
    (
        "findings 里的文本也要扫",
        [],
        [{"kind": "duplicate", "severity": "warning", "target_index": 0,
          "message": "与历史计划中的「需求评审」重复", "suggestion": "建议合并"}],
        {"fabricated_history": 0, "fabricated_numbers": 0},
    ),
    (
        "findings 里编造历史 → 必须报",
        [],
        [{"kind": "duplicate", "severity": "warning", "target_index": 0,
          "message": "与之前做过的支付网关接入重复", "suggestion": "建议合并"}],
        {"fabricated_history": 1},
    ),
    (
        # ⚠️ 结构字段 kind/severity/target_index 不是事实性断言 → 不该报
        "findings 的枚举值与索引不该被当成数字断言",
        [],
        [{"kind": "duplicate", "severity": "info", "target_index": 2,
          "message": "建议补充验收标准", "suggestion": "写明交付物"}],
        {"fabricated_numbers": 0, "assessment_number_leak": 0},
    ),
    (
        # \u26a0\ufe0f\u26a0\ufe0f **\u8bbe\u8ba1\u8fc7\u6e21\u65b9\u6848 \u2192 \u4e0d\u662f\u300c\u8bbe\u8ba1\u8fc7\u300d** \u2014\u2014
        #    \u4e2d\u6587\u6ca1\u6709\u7a7a\u683c\uff0c\u300c\u8bbe\u8ba1\u300d\u4e0e\u300c\u8fc7\u300d\u4e2d\u95f4\u9694\u4e86\u5f88\u591a\u5b57\uff0c
        #    \u5374\u88ab\u5f53\u6210\u300c\u8bbe\u8ba1\u8fc7\u300d\uff08\u5b9e\u6d4b\u8e29\u5751\uff09\u3002
        #    \u4e2d\u6587\u7684\u300c\u505a\u8fc7\u300d\u5fc5\u987b\u662f**\u7d27\u90bb**\u7684\uff0c\u4e0d\u80fd\u8de8\u8bcd\u3002
        "\u300c\u8bbe\u8ba1\u8fc7\u6e21\u65b9\u6848\u300d\u4e0d\u7b97\u300c\u8bbe\u8ba1\u8fc7\u300d",
        [{"task_name": "\u652f\u4ed8\u62c6\u5206", "task_content": "\u8bbe\u8ba1\u8fc7\u6e21\u671f\u7684\u53cc\u5199\u4e0e\u7070\u5ea6\u5207\u6362\u65b9\u6848"}],
        [],
        {"fabricated_history": 0},
    ),
    (
        # \u26a0\ufe0f **\u6a21\u578b\u5f15\u7528\u81ea\u5df1\u521a\u8d77\u8349\u7684\u6761\u76ee\u540d** \u4e0d\u7b97\u7f16\u9020\u5f15\u6587 \u2014\u2014
        #    \u5b9e\u6d4b\u8e29\u5751\uff1a\u6a21\u578b\u5728 findings \u91cc\u5f15\u7528\u81ea\u5df1\u521a\u5199\u7684\u6761\u76ee\u540d\u3002
        "findings \u5f15\u7528\u81ea\u5df1\u7684\u6761\u76ee\u540d\u4e0d\u7b97\u7f16\u9020\u5f15\u6587",
        [{"task_name": "\u62c6\u5206\u843d\u5730\uff0b\u9884\u53d1\u9a8c\u8bc1", "task_content": "\u5b8c\u6210\u62c6\u5206"}],
        [{"kind": "other", "severity": "info", "target_index": 0,
          "message": "\u300c\u62c6\u5206\u843d\u5730\uff0b\u9884\u53d1\u9a8c\u8bc1\u300d\u4e0e\u7b2c 1 \u9879\u91cd\u53e0",
          "suggestion": "\u5efa\u8bae\u5408\u5e76"}],
        {"fabricated_quotes": 0},
    ),
    (
        # \u26a0\ufe0f **\u300c\u751f\u6210 5 \u6761\u8ba1\u5212\u300d\u4e0d\u7b97\u7f16\u9020\u6570\u5b57** \u2014\u2014
        #    \u6a21\u578b\u5728\u63cf\u8ff0**\u81ea\u5df1\u8f93\u51fa\u4e86\u51e0\u6761**\u3002
        "\u300c\u751f\u6210 5 \u6761\u8ba1\u5212\u300d\u4e0d\u7b97\u7f16\u9020\u6570\u5b57",
        [{"task_name": "\u8ba1\u5212", "task_content": "\u672c\u6b21\u751f\u6210 5 \u6761\u8ba1\u5212\uff0c\u5171 6 \u4e2a\u6761\u76ee"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        # \u26a0\ufe0f \u4f46\u662f**\u771f\u7684\u4e1a\u52a1\u6570\u636e\u6570\u5b57**\u4ecd\u7136\u8981\u62a5 \u2014\u2014
        #    \u5141\u8bb8\u96c6\u5408\u4e0d\u80fd\u653e\u5f97\u592a\u5bbd\uff0c\u5426\u5219\u771f\u7f16\u9020\u4e5f\u62a5\u4e0d\u51fa\u6765\u3002
        "\u771f\u7684\u4e1a\u52a1\u6570\u636e\u6570\u5b57\u4ecd\u987b\u62a5",
        [{"task_name": "\u8ba1\u5212", "task_content": "\u9884\u8ba1\u5904\u7406 87 \u7b14\u8ba2\u5355"}],
        [],
        {"fabricated_numbers": 1},
    ),
    (
        # \u26a0\ufe0f **\u300c\u4f1a\u540e 1 \u4e2a\u5de5\u4f5c\u65e5\u5185\u53d1\u51fa\u300d\u4e0d\u7b97\u7f16\u9020\u6570\u5b57** \u2014\u2014
        #    \u65f6\u95f4\u627f\u8bfa\u91cc\u7684\u6570\u5b57\u662f**\u89c4\u8303\u7684\u4e00\u90e8\u5206**\uff0c\u4e0d\u662f\u7f16\u9020\u7684\u5386\u53f2\u4e1a\u52a1\u6570\u636e\u3002
        "\u300c\u4f1a\u540e 1 \u4e2a\u5de5\u4f5c\u65e5\u5185\u53d1\u51fa\u300d\u4e0d\u7b97\u7f16\u9020\u6570\u5b57",
        [{"task_name": "\u8bc4\u5ba1", "quality": "\u4f1a\u540e1\u4e2a\u5de5\u4f5c\u65e5\u5185\u53d1\u51fa\u7eaa\u8981\u5e76\u83b7\u5f97\u8bc4\u5ba1\u53c2\u4e0e\u65b9\u786e\u8ba4"}],
        [],
        {"fabricated_numbers": 0},
    ),
    (
        # \u26a0\ufe0f **\u6a21\u578b\u8d77\u8349\u7684\u5185\u5bb9\u4e0d\u7b97\u7f16\u9020\u5f15\u6587** \u2014\u2014
        #    \u5f15\u53f7\u5fc5\u987b\u5904\u5728\u300c\u5f15\u7528/\u5bf9\u6bd4\u300d\u8bed\u5883\u91cc\u624d\u7b97\u3002
        "\u300c\u6309\u672c\u6708\u9700\u6c42\u6392\u671f\u300d\u4e0d\u7b97\u7f16\u9020\u5f15\u6587",
        [{"task_name": "\u8bc4\u5ba1", "task_content": "\u5b89\u6392\u8bc4\u5ba1"}],
        [{"kind": "other", "severity": "info", "target_index": 0,
          "message": "\u5efa\u8bae\u6309\u300c\u6309\u672c\u6708\u9700\u6c42\u6392\u671f\u300d\u6267\u884c", "suggestion": "\u8865\u5145"}],
        {"fabricated_quotes": 0},
    ),
    (
        # \u26a0\ufe0f \u4f46\u662f**\u771f\u7684\u5f15\u7528\u4e86\u4e0d\u5b58\u5728\u7684\u539f\u6587\u300d\u4ecd\u987b\u62a5 \u2014\u2014
        #    \u5f15\u7528\u8bed\u5883\u91cc\u7684\u5f15\u53f7\u5185\u5bb9\u627e\u4e0d\u5230\u539f\u6587 \u2192 \u7f16\u9020\u5f15\u6587\u3002
        "\u300c\u53c2\u8003\u5386\u53f2\u8ba1\u5212\u4e2d\u7684\u4e0d\u5b58\u5728\u9879\u300d\u5fc5\u987b\u62a5",
        [{"task_name": "\u65b0\u4efb\u52a1", "task_content": "\u53c2\u8003\u5386\u53f2\u8ba1\u5212\u4e2d\u7684\u300c\u5b8c\u5168\u4e0d\u5b58\u5728\u7684\u4e8b\u9879\u300d"}],
        [],
        {"fabricated_quotes": 1},
    ),
    (
        # \u26a0\ufe0f **\u300c\u4e0e\u4e0a\u6708\u8ba1\u5212\u91cd\u590d\u300d\u4e0d\u7b97\u7f16\u9020\u5f15\u6587** \u2014\u2014
        #    \u5b9e\u6d4b\u8e29\u5751\uff1a\u6a21\u578b\u5728\u63cf\u8ff0**\u5224\u91cd\u52a8\u4f5c**\uff0c\u4e0d\u662f\u5728\u5f15\u7528\u4e00\u6bb5\u539f\u6587\u3002
        #    \u300c\u4e0a\u6708\u300d\u8fd9\u4e2a\u5bbd\u6cdb\u7684\u65f6\u95f4\u8bcd\u4e0d\u80fd\u5355\u72ec\u6784\u6210\u300c\u5f15\u7528\u300d\u3002
        "\u300c\u4e0e\u4e0a\u6708\u8ba1\u5212\u91cd\u590d\u300d\u4e0d\u7b97\u7f16\u9020\u5f15\u6587",
        [{"task_name": "\u8bc4\u5ba1", "task_content": "\u7ec4\u7ec7\u8bc4\u5ba1"}],
        [{"kind": "duplicate", "severity": "info", "target_index": -1,
          "message": "\u65e0\u6cd5\u505a\u300e\u4e0e\u4e0a\u6708\u8ba1\u5212\u91cd\u590d\u300f\u7684\u6a2a\u5411\u6bd4\u5bf9", "suggestion": "\u8865\u5145"}],
        {"fabricated_quotes": 0},
    ),
    (
        # \u26a0\ufe0f **\u771f\u6b63\u7684\u7f16\u9020\u5f15\u6587\u4ecd\u987b\u80fd\u62a5** \u2014\u2014
        #    \u300c\u5386\u53f2\u8ba1\u5212\u4e2d\u7684\u300d\u5c31\u662f\u5f3a\u8f6c\u8ff0\u6807\u8bb0\uff0c\u5fc5\u987b\u80fd\u62a5\u3002
        "\u300c\u5386\u53f2\u8ba1\u5212\u4e2d\u7684\u4e0d\u5b58\u5728\u9879\u300d\u5fc5\u987b\u80fd\u62a5",
        [{"task_name": "\u65b0\u4efb\u52a1", "task_content": "\u5386\u53f2\u8ba1\u5212\u4e2d\u7684\u300c\u5b8c\u5168\u4e0d\u5b58\u5728\u7684\u4e8b\u9879\u300d"}],
        [],
        {"fabricated_quotes": 1},
    ),
]


def main() -> int:
    passed = 0
    failed = 0
    print("=" * 70)
    print("计划场景幻觉检测器自检")
    print("=" * 70)

    for case in CASES:
        label, items, findings, expect = case
        issues = detect_plan_hallucination(
            items,
            findings,
            my_history=MY_HISTORY,
            dept_history=DEPT_HISTORY,
            legal_refs=LEGAL_REFS,
        )
        ok = True
        details = []
        for key, want in expect.items():
            got = len(issues.get(key, []))
            if got != want:
                ok = False
                details.append(f"{key}: 期望 {want} 实际 {got} -> {issues.get(key)}")
        if ok:
            passed += 1
            print(f"  [PASS] {label}")
        else:
            failed += 1
            print(f"  [FAIL] {label}")
            for d in details:
                print(f"         {d}")

    print("\n" + "=" * 70)
    print(f"检测器自检：{passed} 通过 / {failed} 失败")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
