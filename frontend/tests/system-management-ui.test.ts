import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  appendKnowledgeBatchFiles,
  applyMetadataToBatchItems,
  emptyKnowledgeUploadMetadata,
  runKnowledgeBatchUpload,
} from '../src/utils/knowledgeBatchUpload.ts'

const registerView = readFileSync(
  fileURLToPath(new URL('../src/views/system/EmployeeRegister.vue', import.meta.url)),
  'utf8',
)
const loginView = readFileSync(
  fileURLToPath(new URL('../src/views/Login.vue', import.meta.url)),
  'utf8',
)
const orgView = readFileSync(
  fileURLToPath(new URL('../src/views/system/OrgManagement.vue', import.meta.url)),
  'utf8',
)
const employeeManagementView = readFileSync(
  fileURLToPath(new URL('../src/views/system/EmployeeManagement.vue', import.meta.url)),
  'utf8',
)
const systemApi = readFileSync(
  fileURLToPath(new URL('../src/api/system.ts', import.meta.url)),
  'utf8',
)
const aiManagementView = readFileSync(
  fileURLToPath(new URL('../src/views/system/AiManagement.vue', import.meta.url)),
  'utf8',
)

test('employee registration selects one role and allows an empty direct leader', () => {
  assert.match(registerView, /v-model="form\.roleId"/)
  assert.doesNotMatch(registerView, /v-model="form\.roleId"[^>]*multiple/)
  assert.match(registerView, /role\.code === 'EMPLOYEE'/)
  assert.match(registerView, /v-model="form\.directLeaderId"[^>]*clearable/)
  assert.doesNotMatch(registerView, /directLeaderId:\s*\[\{\s*required:\s*true/)
  assert.match(registerView, /roleId:\s*form\.roleId!/)
  assert.match(registerView, /result\.directLeaderName \|\| '未配置'/)
})

test('employee management edits one role and submits a one-item role list', () => {
  assert.match(employeeManagementView, /v-model="editForm\.roleId"/)
  assert.doesNotMatch(employeeManagementView, /v-model="editForm\.roleId"[^>]*multiple/)
  assert.match(employeeManagementView, /roleIds:\s*\[editForm\.roleId\]/)
})

test('login copy accepts account or mobile with the existing password flow', () => {
  assert.match(loginView, /label="账号或手机号"/)
  assert.match(loginView, /placeholder="请输入账号或手机号"/)
  assert.match(loginView, /autocomplete="username"/)
  assert.match(loginView, /autocomplete="current-password"/)
  assert.match(loginView, /reactive\(\{ username: '', password: '' \}\)/)
  assert.doesNotMatch(loginView, /开发环境账号/)
  assert.doesNotMatch(loginView, /Admin@123456/)
})

test('organization management confirms and calls logical delete API', () => {
  assert.match(systemApi, /export const deleteDepartment/)
  assert.match(systemApi, /http\.delete\(`\/system\/departments\/\$\{id\}`\)/)
  assert.match(orgView, /ElMessageBox/)
  assert.match(orgView, /deleteDepartment/)
  assert.match(orgView, /async function removeDepartment/)
  assert.match(orgView, /仍有子节点或员工/)
})

test('AI management only offers external providers', () => {
  assert.doesNotMatch(aiManagementView, /value="MOCK"/)
  assert.doesNotMatch(aiManagementView, /planning-mock-v1/)
  assert.match(aiManagementView, /providerCode: 'ALIYUN'/)
})

test('AI call detail renders the current call trace with legacy-safe technical detail', () => {
  assert.match(aiManagementView, /当前调用链路/)
  assert.match(aiManagementView, /normalizedTraceEvents/)
  assert.match(aiManagementView, /业务结果摘要/)
  assert.match(aiManagementView, /技术详情/)
  assert.match(aiManagementView, /供应商未提供/)
  assert.match(aiManagementView, /暂无执行追溯记录/)
  assert.match(aiManagementView, /formatTraceEvent/)
  assert.doesNotMatch(aiManagementView, /本次完整链路|getAiCallLogChainApi|MONTH_PLAN_AGENT_/)
})

test('AI call detail delegates input context to the semantic summary component', () => {
  assert.match(aiManagementView, /AiInputSummary/)
  assert.match(aiManagementView, /activeLog\.inputSummary/)
  assert.doesNotMatch(aiManagementView, /<h3>已脱敏输入摘要<\/h3>/)
})

const completeKnowledgeMetadata = {
  audienceCodes: ['EMPLOYEE'] as const,
  scopeType: 'GLOBAL' as const,
  knowledgeType: 'PROJECT_MATERIAL',
  appliesTo: ['MONTH_PLAN'],
  bindingLevel: 'REFERENCE',
}

const uploadResult = (name: string) => ({
  documentId: 1, knowledgeId: `K-${name}`, title: name, fileName: name, fileType: 'md',
  chunkCount: 1, vectorCount: 1, embeddingModels: [], uploadedAt: '2026-08-05T00:00:00',
})

test('knowledge batch files keep independent classification and reject duplicates', () => {
  const first = appendKnowledgeBatchFiles([], [
    { name: 'a.md', size: 1 }, { name: 'b.md', size: 2 },
  ], completeKnowledgeMetadata)
  applyMetadataToBatchItems(first.items, [first.items[0].id], completeKnowledgeMetadata)
  first.items[0].metadata.appliesTo.push('PLAN_CHECK')
  assert.deepEqual(first.items[1].metadata.appliesTo, ['MONTH_PLAN'])

  const duplicate = appendKnowledgeBatchFiles(first.items, [{ name: 'a.md', size: 1 }], completeKnowledgeMetadata)
  assert.equal(duplicate.items.length, 2)
  assert.match(duplicate.rejected[0].reason, /同批次/)
})

test('knowledge batch validation failure starts no upload', async () => {
  const added = appendKnowledgeBatchFiles([], [{ name: 'a.md', size: 1 }], emptyKnowledgeUploadMetadata())
  let calls = 0
  const run = await runKnowledgeBatchUpload(added.items, async (file) => {
    calls += 1
    return uploadResult(file.name)
  }, { validate: (metadata) => metadata.knowledgeType ? '' : '请选择文档类型' })
  assert.equal(run.started, false)
  assert.equal(calls, 0)
})

test('knowledge batch isolates failures, limits concurrency, and retries one file', async () => {
  const added = appendKnowledgeBatchFiles([], Array.from({ length: 5 }, (_, index) => ({
    name: `${index}.md`, size: index + 1,
  })), completeKnowledgeMetadata)
  let active = 0
  let maxActive = 0
  let shouldFail = true
  const upload = async (file: { name: string; size: number }) => {
    active += 1
    maxActive = Math.max(maxActive, active)
    await new Promise((resolve) => setTimeout(resolve, 4))
    active -= 1
    if (file.name === '2.md' && shouldFail) throw new Error('索引失败')
    return uploadResult(file.name)
  }
  const first = await runKnowledgeBatchUpload(added.items, upload, { validate: () => '', concurrency: 2 })
  assert.equal(maxActive, 2)
  assert.equal(first.succeeded.length, 4)
  assert.equal(first.failed.length, 1)

  shouldFail = false
  const retry = await runKnowledgeBatchUpload(added.items, upload, {
    validate: () => '', concurrency: 2, itemIds: [first.failed[0].id],
  })
  assert.equal(retry.succeeded.length, 1)
  assert.equal(added.items.every((item) => item.status === 'success'), true)
})
