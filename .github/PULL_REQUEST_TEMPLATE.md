## 变更说明

<!-- 简要描述本次改动解决了什么问题。 -->

## 类型

- [ ] 功能新增
- [ ] 缺陷修复
- [ ] 重构 / 技术债
- [ ] 文档 / 配置

## 影响范围

- [ ] 后端（`backend/`）
- [ ] 前端（`frontend/`）
- [ ] 智能体服务（`agent-service/`）
- [ ] 数据库迁移（`backend/migrations/`）
- [ ] CI / 工程配置

## 数据库变更

- [ ] 本次**不涉及** schema 变更
- [ ] 已新增 Alembic 迁移脚本（`backend/migrations/versions/`，revision id：`______`）
- [ ] 迁移已验证可 `downgrade` 回退

## 自检清单

> 提交前请在本地跑一遍，等价于 CI 的全流程检查：

```bash
python backend/scripts/run_ci_locally.py
```

- [ ] API 契约校验通过（`check_api_contract.py --strict`，缺失 0）
- [ ] 后端测试通过
- [ ] 前端类型检查通过（`vue-tsc --noEmit` 无错误）
- [ ] 前端测试**全部通过**（零失败硬门槛，2026-11-16 起不再有基线豁免）
- [ ] Alembic 迁移升级 / 降级往返无损

## 契约影响

> 本项目的前后端端点必须严格对齐。CI 会用 `--strict` 模式阻断。

- [ ] 本次**未改动**任何 API 路径 / 方法 / 参数 / 响应结构
- [ ] 已同步修改前端 `frontend/src/api/*.ts` 与后端路由
- [ ] 已更新 `docs/05-API契约对齐清单.md`（如涉及端点变化）

## 截图 / 演示

<!-- 前端界面变更请附截图。 -->
