import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

/**
 * 🔴 企业知识库前后端契约回归（2026-11-27）。
 *
 * ⚠️ **本文件锁住的缺陷**
 * ------------------------------------------------------------------
 * 后端的 12 个 knowledge 端点**全是空壳**（恒返回空数组 / 恒返回 0），
 * 而前端有完整的三个页面 ⇒ **上传弹「成功」，刷新后什么都没了**。
 *
 * 此外还锁住一个**契约断裂**：
 * `POST /v1/leader/knowledge/upload` 后端原先**必填** `title`，
 * 而前端 `uploadLeaderKnowledgeApi` **不传 title** ⇒ 必定 422。
 */

const knowledgeApi = readFileSync(
  fileURLToPath(new URL('../src/api/knowledge.ts', import.meta.url)),
  'utf8',
)
const enterpriseKnowledgeView = readFileSync(
  fileURLToPath(new URL('../src/views/system/EnterpriseKnowledge.vue', import.meta.url)),
  'utf8',
)
const leaderKnowledgeView = readFileSync(
  fileURLToPath(new URL('../src/views/leader/LeaderKnowledge.vue', import.meta.url)),
  'utf8',
)

test('leader upload sends exactly the fields the backend accepts', () => {
  // 前端提交的字段
  assert.match(knowledgeApi, /data\.append\('audienceCodes'/)
  assert.match(knowledgeApi, /data\.append\('scopeType'/)
  assert.match(knowledgeApi, /data\.append\('knowledgeType'/)
  assert.match(knowledgeApi, /data\.append\('appliesTo'/)
  assert.match(knowledgeApi, /data\.append\('bindingLevel'/)

  // ⚠️ **前端不传 title** ⇒ 后端不能把 title 设为必填
  assert.doesNotMatch(knowledgeApi, /data\.append\('title'/)
})

test('knowledge status and document list endpoints are wired to real APIs', () => {
  assert.match(knowledgeApi, /'\/system\/knowledge\/status'/)
  assert.match(knowledgeApi, /'\/system\/knowledge\/documents'/)
  assert.match(knowledgeApi, /`\/system\/knowledge\/documents\/\$\{id\}`/)
  assert.match(knowledgeApi, /`\/system\/knowledge\/documents\/\$\{id\}\/history`/)
  assert.match(knowledgeApi, /`\/system\/knowledge\/documents\/\$\{id\}\/versions`/)
  assert.match(knowledgeApi, /`\/system\/knowledge\/documents\/\$\{id\}\/disable`/)
  assert.match(knowledgeApi, /`\/system\/knowledge\/documents\/\$\{id\}\/enable`/)
  assert.match(knowledgeApi, /`\/system\/knowledge\/documents\/\$\{id\}`/)  // DELETE
  assert.match(knowledgeApi, /'\/system\/knowledge\/sync'/)
  assert.match(knowledgeApi, /'\/system\/knowledge\/preview'/)
  assert.match(knowledgeApi, /'\/leader\/knowledge\/upload'/)
  assert.match(knowledgeApi, /'\/leader\/knowledge\/uploads'/)
})

test('enterprise knowledge view consumes the real status fields', () => {
  // 文档状态分组计数（按 status，不是按知识类型）
  assert.match(enterpriseKnowledgeView, /countOf\('PUBLISHED'\)/)
  assert.match(enterpriseKnowledgeView, /countOf\('DISABLED'\)/)
  assert.match(enterpriseKnowledgeView, /status\.chunkCount/)
  assert.match(enterpriseKnowledgeView, /status\.vectorPointCount/)
  assert.match(enterpriseKnowledgeView, /status\.relationCount/)
})

test('enterprise knowledge view must not hardcode an external vector database', () => {
  // ⚠️ 本实现用的是**内置 BM25 索引**（零外部依赖）
  //    ⇒ 页面文案不能宣称「已入 Qdrant」，否则与后端实际行为不符。
  assert.doesNotMatch(enterpriseKnowledgeView, /Qdrant/)
  assert.match(enterpriseKnowledgeView, /builtin-bm25/)
})

test('vault not configured is not treated as a fatal runtime error', () => {
  // ⚠️ 「页面上传」不需要 Vault ⇒ 只有配置了却连不上才是故障
  assert.match(enterpriseKnowledgeView, /Vault 未配置/)
  assert.doesNotMatch(enterpriseKnowledgeView, /if \(!status\.vaultAvailable\) return 'Vault 不可用'/)
})

test('leader knowledge page reads real upload records', () => {
  assert.match(leaderKnowledgeView, /recentUploads/)
  assert.match(leaderKnowledgeView, /item\.chunkCount/)
  assert.match(leaderKnowledgeView, /item\.vectorCount/)
  assert.match(leaderKnowledgeView, /暂无入库文档/)
})
