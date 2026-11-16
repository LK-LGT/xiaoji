#!/usr/bin/env python
"""测试完整的绩效考核流程 API。"""

import json
from datetime import datetime, timedelta

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

print("=" * 80)
print("绩效考核完整流程测试")
print("=" * 80)

# 1. 发起考核任务
print("\n[1] 发起考核任务 (HR)")
print("-" * 80)

now = datetime.now()
task_payload = {
    "title": "2026 年 11 月月度考核",
    "period": "202611",
    "target_employee_ids": ["2aaf32ea", "17cd8645", "e76874b3"],
    "start_date": (now - timedelta(days=30)).isoformat(),
    "end_date": now.isoformat(),
    "self_review_deadline": (now + timedelta(days=2)).isoformat(),
    "manager_review_deadline": (now + timedelta(days=5)).isoformat(),
}

response = client.post("/v1/performance/tasks?current_user_id=8d5c97gb", json=task_payload)
print(f"状态码: {response.status_code}")
resp_json = response.json()
print(f"响应: {json.dumps(resp_json, indent=2, ensure_ascii=False)[:300]}...")

if response.status_code == 200:
    task_id = resp_json["data"]["task_id"]

    # 查询任务的所有记录
    print("\n获取任务的所有记录:")
    response = client.get(f"/v1/performance/tasks/{task_id}/records?current_user_id=8d5c97gb")
    print(f"状态码: {response.status_code}")
    records_data = response.json()["data"]["records"]
    print(f"记录数: {len(records_data)}")
    record_id = None
    for r in records_data:
        print(f"  - {r['employee']['name']}: {r['status']}")
        if not record_id:
            record_id = r["record_id"]

    # 2. 员工提交自评
    if record_id:
        print("\n[2] 员工提交自评")
        print("-" * 80)

        self_review_payload = {
            "record_id": record_id,
            "performance_summary": "本月完成了 AI 技术部的所有核心开发任务，包括 LLM 集成和提示词优化。",
            "achievements": ["实现了新的 RAG 架构设计", "优化了模型响应时间"],
            "challenges": ["部分硬件资源限制"]
        }

        response = client.post(
            "/v1/performance/self-reviews?current_user_id=2aaf32ea",
            json=self_review_payload
        )
        print(f"状态码: {response.status_code}")
        print(f"响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")

        # 3. 主管提交评分
        print("\n[3] 主管提交评分")
        print("-" * 80)

        manager_review_payload = {
            "record_id": record_id,
            "rating": "A",
            "manager_comment": "李凯表现优秀，技术实力强，本月完成多项核心项目。"
        }

        response = client.post(
            "/v1/performance/manager-reviews?current_user_id=g426c684",
            json=manager_review_payload
        )
        print(f"状态码: {response.status_code}")
        print(f"响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")

        # 4. 查询考核记录
        print("\n[4] 查询考核记录详情 (权限检查)")
        print("-" * 80)

        response = client.get(f"/v1/performance/records/{record_id}?current_user_id=2aaf32ea")
        print(f"状态码: {response.status_code}")
        if response.status_code == 200:
            rec_data = response.json()["data"]
            print(f"员工: {rec_data['employee']['name']}")
            print(f"状态: {rec_data['status']}")
            print(f"评级: {rec_data['rating']}")
            print(f"主管评语: {rec_data['manager_comment'][:50] if rec_data['manager_comment'] else 'N/A'}")

print("\n" + "=" * 80)
print("测试完成！")
print("=" * 80)
