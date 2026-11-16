import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const syncView = readFileSync(
  fileURLToPath(new URL('../src/views/system/FeishuSyncManagement.vue', import.meta.url)),
  'utf8',
)
const syncApi = readFileSync(
  fileURLToPath(new URL('../src/api/feishuOrganization.ts', import.meta.url)),
  'utf8',
)

test('sync console reads the scheduler runtime endpoint, not just the config flag', () => {
  // ⚠️ 核心断言：必须调**调度器运行态**端点。
  // 只看 status.scheduledEnabled（配置开关）的话，
  // 「配了但线程没跑」永远发现不了 —— 那正是 R8 最初的样子。
  assert.match(syncApi, /getSyncSchedulerStatus/)
  assert.match(syncApi, /\/feishu\/sync-organization\/scheduler/)
  assert.match(syncView, /getSyncSchedulerStatus/)
})

test('scheduler status type separates config flag from live thread state', () => {
  // enabled = 配置；running = 进程内线程。两者不能混成一个字段。
  assert.match(syncApi, /export interface SchedulerStatus/)
  assert.match(syncApi, /enabled: boolean/)
  assert.match(syncApi, /running: boolean/)
  assert.match(syncApi, /consecutiveFailures: number/)
  assert.match(syncApi, /nextRunAt: string \| null/)
})

test('sync console warns when scheduler is configured but thread is not running', () => {
  // 「配了但没跑」必须显式提示，不能只显示「已开启」。
  assert.match(syncView, /schedulerMisconfigured/)
  assert.match(syncView, /定时同步已配置，但调度线程未运行/)
})

test('sync console surfaces scheduler runtime facts', () => {
  assert.match(syncView, /schedulerSummary/)
  assert.match(syncView, /consecutiveFailures/)
  assert.match(syncView, /nextRunAt/)
})
