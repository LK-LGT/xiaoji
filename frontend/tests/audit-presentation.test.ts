import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { formatAuditResult, formatRawDetail, operatorCaption } from '../src/views/system/auditPresentation.ts'

const auditView = readFileSync(fileURLToPath(new URL('../src/views/system/AuditLog.vue', import.meta.url)), 'utf8')
const dashboardView = readFileSync(fileURLToPath(new URL('../src/views/system/SystemDashboard.vue', import.meta.url)), 'utf8')

test('audit presentation helpers preserve raw detail and prioritize real names', () => {
  assert.equal(formatAuditResult('SUCCESS'), '成功')
  assert.equal(formatAuditResult('FAILED'), '失败')
  assert.equal(formatRawDetail('{bad'), '{bad')
  assert.equal(operatorCaption({ operatorName: '张三', username: 'zhangsan' }), '账号：zhangsan')
})

test('audit workbench exposes business filters and keeps technical data collapsed', () => {
  assert.match(auditView, /getAuditOptions/)
  assert.match(auditView, /placeholder="具体动作"/)
  assert.match(auditView, /发生的事情/)
  assert.match(auditView, /active\.summary/)
  assert.match(auditView, /active\.detailItems/)
  assert.match(auditView, /<details class="audit-technical-details">/)
  assert.match(auditView, /技术信息/)
})

test('system dashboard uses enriched audit labels', () => {
  assert.match(dashboardView, /actionLabel/)
  assert.match(dashboardView, /operatorName/)
  assert.match(dashboardView, /targetSummary/)
})
