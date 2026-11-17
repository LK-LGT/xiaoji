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

// ═══════════════════════════════════════════════════════════════════
// BUG-R6-46：同步游标接口分页
// ═══════════════════════════════════════════════════════════════════

test('游标接口返回分页信封而不是裸数组', () => {
  // ⚠️ 核心断言：类型必须是 PageResult<SyncCursor>。
  // 裸数组（SyncCursor[]）没有 total，前端做不出分页控件。
  assert.match(syncApi, /listSyncCursors[\s\S]*?PageResult<SyncCursor>/)
  assert.doesNotMatch(
    syncApi,
    /listSyncCursors[\s\S]*?SyncCursor\[\]/,
    '游标接口不得再返回裸数组类型',
  )
})

test('游标接口支持分页与资源类型过滤参数', () => {
  assert.match(syncApi, /listSyncCursors[\s\S]*?page\?: number/)
  assert.match(syncApi, /listSyncCursors[\s\S]*?pageSize\?: number/)
  assert.match(syncApi, /listSyncCursors[\s\S]*?resourceType\?: string/)
})

test('游标表格改走分页数据源，不再直接吃 status.cursors 全量', () => {
  // ⚠️ 回归防护：表格数据源必须是 cursorPage.records。
  // 回到 status?.cursors 会让分页形同虚设。
  assert.match(syncView, /:data="cursorPage\.records"/)
  assert.doesNotMatch(
    syncView,
    /:data="status\?\.cursors/,
    '游标表格不得直接绑定 status.cursors 全量数组',
  )
})

test('游标表格带分页控件与总数', () => {
  assert.match(syncView, /共 \{\{ cursorPage\.total \}\} 个游标/)
  assert.match(syncView, /v-model:current-page="cursorQuery\.page"/)
  assert.match(syncView, /:total="cursorPage\.total"/)
})

test('游标分页状态与加载函数存在并接入刷新流程', () => {
  assert.match(syncView, /const cursorQuery = reactive\(\{ page: 1, pageSize: 20 \}\)/)
  assert.match(syncView, /const cursorPage = ref<PageResult<SyncCursor>>/)
  assert.match(syncView, /async function loadCursors\(\)/)
  assert.match(syncView, /cursorPage\.value = await listSyncCursors\(/)
  // 刷新流程必须带上游标，否则首次进页面表格是空的
  assert.match(syncView, /Promise\.all\(\[[\s\S]*?loadCursors\(\)[\s\S]*?\]\)/)
})

test('游标加载有独立的 loading 态', () => {
  assert.match(syncView, /const cursorLoading = ref\(false\)/)
  assert.match(syncView, /v-loading="cursorLoading"/)
})

test('快照不完整的告警仍由概览数据驱动', () => {
  // 告警要反映「全局快照不完整」——那是离职判定被跳过的前提。
  // 分部门游标是分页的，不能拿当前页判断整体完整性。
  assert.match(syncView, /incompleteCursorCount/)
  assert.match(syncView, /status\.value\?\.cursors\?\.filter\(\(cursor\) => !cursor\.snapshotComplete\)/)
})
