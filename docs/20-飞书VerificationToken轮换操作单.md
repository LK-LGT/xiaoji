# 飞书 Verification Token 轮换操作单（2026-11-27）

> 背景：`backend/app/tests/test_feishu_event.py` 曾把本地 `.env` 的真实
> Verification Token 写进代码并进入 git 历史。代码侧已改为假值并加护栏，
> 但**旧 Token 在飞书后台重置前仍然有效**。本操作单由 IT / 项目负责人执行。

---

## 1. 影响范围（先看）

飞书的 Verification Token 是**应用级**的：一个应用只有一个 Token，
审批回流、通讯录事件、机器人事件**共用同一个值**。

⚠️ 重置后，三条链路会同时失效，直到后端 `.env` 更新并重启。
⇒ 必须在维护窗口内一次完成「重置 + 更新 + 重启 + 验证」。

⚠️ **不要**在其他窗口单独重置，否则审批/通讯录回调会一起 401。

---

## 2. 操作步骤

### 2.1 准备
- [ ] 通知审批、通讯录、机器人三条链路的负责人
- [ ] 确认可以重启后端（`run-full-stack.py --backend` 或部署平台）
- [ ] 备份 `backend/.env`

### 2.2 飞书后台重置
1. 飞书开放平台 → 应用 → **事件与回调** → **加密策略**
2. 找到 **Verification Token**，点击「重置」
3. 复制新 Token（只显示一次）

### 2.3 更新后端配置
编辑 `backend/.env`：

```dotenv
FEISHU_APPROVAL_WEBHOOK_TOKEN=<新 Token>
FEISHU_CONTACT_EVENT_TOKEN=<新 Token>
# 如另有机器人事件 Token 配置，也一并更新
```

> 这两个 key 必须同时更新；只改一个会导致另一条链路 401。

### 2.4 重启并验证
- [ ] 重启后端
- [ ] 飞书后台「事件订阅」点一次「重新推送」/ 触发测试事件
- [ ] 审批链路：提交一条测试审批 → 后端日志出现 `approval ... updated`
- [ ] 通讯录链路：飞书改一个测试员工 → 后端日志出现 contact event
- [ ] 机器人链路：在测试群 @机器人 发一条消息 → 机器人有回复
- [ ] 后端日志无 `401 / token mismatch / Verification Token`

### 2.5 代码侧确认
- [ ] `grep -R "<新 Token>" backend/app/tests` 无结果
- [ ] 跑 `python -m pytest backend/app/tests/test_feishu_event.py backend/app/tests/test_sensitive_data_leak.py -q`
- [ ] 确认 `test_sensitive_data_leak.py` 的防写死护栏通过（本地有 .env 时会扫描）

---

## 3. 回滚

- Token 重置后**不能恢复旧值**；回滚只能：
  1. 确认 `.env` 的新值与后台一致；
  2. 重启后端；
  3. 仍失败则检查 `FEISHU_ENCRYPT_KEY` 是否也被改动（两者独立）。
- 旧 Token 已进入 git 历史，**轮换后即失效**，历史值不再有利用价值。

---

## 4. 后续

- [ ] 将飞书 App Secret / Encrypt Key / 机器人 Secret 一并纳入季度轮换
- [ ] 所有验证脚本从环境变量读凭据，禁止写死（已有护栏测试）
- [ ] 如仓库有外部协作者，再评估是否用 `git filter-repo` 清理历史
