import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

function source(path: string) {
  return readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')
}

const panel = source('../src/views/employee/components/PlanAiConversationPanel.vue')
const monthWorkspace = source('../src/views/employee/EmployeeMonthPlanAiWorkspace.vue')
const planAssistant = source('../src/views/employee/components/PlanAiAssistant.vue')
const monthEditor = source('../src/views/employee/EmployeeMonthPlanEdit.vue')
const conversationApi = source('../src/api/aiConversations.ts')
const employeeAiApi = source('../src/api/employeeAi.ts')

test('shared panel restores active messages and exposes history and new conversation controls', () => {
  assert.match(panel, /resolveAiConversation/)
  assert.match(panel, /listAiConversationMessages/)
  assert.match(panel, /历史对话/)
  assert.match(panel, /新对话不会继承其记忆/)
  assert.match(panel, /历史对话仅供查看，不会进入当前记忆/)
  assert.match(panel, /:disabled="busy \|\| loading \|\| readonlyView"/)
  assert.match(panel, /v-if="busy"/)
  assert.match(panel, /emit\('stop'\)/)
  assert.match(panel, /message\.status === 'CANCELLED'/)
  assert.match(panel, /message\.status === 'FAILED'/)
  assert.match(panel, /loadOlder/)
})

test('conversation API uses employee scoped lifecycle endpoints', () => {
  assert.match(conversationApi, /\/employee\/ai\/conversations\/active/)
  assert.match(conversationApi, /\/employee\/ai\/conversations\/history/)
  assert.match(conversationApi, /\/employee\/ai\/conversations\/\$\{conversationId\}\/messages/)
  assert.match(conversationApi, /\/employee\/ai\/conversations\/new/)
})

test('month generate optimize and check contracts share a conversation id', () => {
  assert.match(monthWorkspace, /usePlanAiConversation\('MONTH'/)
  assert.match(monthWorkspace, /conversationId:\s*active\.id/)
  // ⚠️ 会话主键是 **UUID 字符串**（后端 `AiConversation.id: String(64)`，
  //   路由参数也是 `conversation_id: str`）。
  //   此处曾断言 `ref<number>` —— 那会把 UUID 当数字发给后端，直接 422。
  assert.match(monthEditor, /const aiConversationId = ref<string>/)
  assert.match(monthEditor, /conversationId: aiConversationId\.value/)
  assert.equal((employeeAiApi.match(/conversationId\?: string/g) || []).length, 7)
})

test('week and day assistant routes follow ups through generate or adjust with memory', () => {
  assert.match(planAssistant, /mode === 'week' \? 'WEEK' : 'DAY'/)
  assert.match(planAssistant, /conversationId: conversationId\.value/g)
  assert.match(planAssistant, /function sendConversationMessage/)
  assert.match(planAssistant, /if \(hasDraft\) void adjust\(message\)/)
  assert.match(planAssistant, /else void generate\(message\)/)
  assert.match(planAssistant, /conversationId\.value = undefined/)
})
