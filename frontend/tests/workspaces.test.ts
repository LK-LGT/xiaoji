import assert from 'node:assert/strict'
import test from 'node:test'
import {
  availableWorkspaces,
  defaultWorkspacePath,
  visibleWorkspaceItems,
  workspaceForPath,
} from '../src/navigation/workspaces.ts'

test('selects the highest-priority authorized workspace', () => {
  assert.equal(defaultWorkspacePath(['dashboard:view']), '/employee/dashboard')
  assert.equal(defaultWorkspacePath(['dashboard:view', 'leader:workbench:view']), '/leader/workbench')
  assert.equal(
    defaultWorkspacePath(['leader:workbench:view', 'department:dashboard:view']),
    '/department/dashboard',
  )
  assert.equal(
    defaultWorkspacePath(['department:dashboard:view', 'system:dashboard:view']),
    '/system/dashboard',
  )
})

test('keeps the declared order when every workspace is available', () => {
  const permissions = [
    'dashboard:view',
    'leader:workbench:view',
    'department:dashboard:view',
    'system:dashboard:view',
    'dispute:dashboard:view',
  ]

  assert.deepEqual(
    availableWorkspaces(permissions).map((workspace) => workspace.id),
    ['system', 'department', 'leader', 'employee', 'dispute'],
  )
})

test('returns no workspace when no workspace home permission exists', () => {
  assert.equal(defaultWorkspacePath(['planning:month:view']), undefined)
  assert.deepEqual(availableWorkspaces([]), [])
})

test('filters granular menu entries without hiding workspace-level compatibility entries', () => {
  const employee = availableWorkspaces(['dashboard:view'])[0]
  assert.ok(employee)

  assert.deepEqual(
    visibleWorkspaceItems(employee, ['dashboard:view']).map((item) => item.path),
    [
      '/employee/dashboard',
      '/employee/week-plans',
      '/employee/performance-evidence',
      '/employee/appeals',
    ],
  )

  assert.ok(
    visibleWorkspaceItems(employee, ['dashboard:view', 'planning:month:view'])
      .some((item) => item.path === '/employee/month-plans'),
  )
})

test('matches canonical, alias, and nested workspace paths', () => {
  assert.equal(workspaceForPath('/planning/month')?.id, 'employee')
  assert.equal(workspaceForPath('/employee/month-plans/3')?.id, 'employee')
  assert.equal(workspaceForPath('/system/roles')?.id, 'system')
  assert.equal(workspaceForPath('/messages'), undefined)
})

test('exposes document ingestion as a dedicated leader entry', () => {
  const leader = availableWorkspaces(['leader:workbench:view'])[0]
  assert.ok(leader)

  const items = visibleWorkspaceItems(leader, ['leader:workbench:view'])
  assert.ok(items.some((item) => item.label === '文档入库' && item.path === '/leader/knowledge'))
  assert.ok(!items.some((item) => item.path === '/leader/ai-month-context'))
})

test('exposes personal plans and consolidates leader result entries', () => {
  const permissions = ['leader:workbench:view', 'leader:result-suggest:view']
  const leader = availableWorkspaces(permissions)[0]
  assert.ok(leader)

  const items = visibleWorkspaceItems(leader, permissions)
  assert.ok(items.some((item) => item.label === '我的月计划' && item.path === '/leader/my-month-plans'))
  assert.ok(items.some((item) => item.label === '我的周计划' && item.path === '/leader/my-week-plans'))
  assert.ok(items.some((item) => item.label === '我的成果' && item.path === '/leader/results'))
  assert.ok(items.some((item) => item.label === '员工成果' && item.path === '/leader/result-suggest'))
  assert.ok(!items.some((item) => item.path === '/leader/week-result-approval'))
})

test('exposes department week approval separately from the read-only ledger', () => {
  const permissions = ['department:dashboard:view', 'department:month-approval:view', 'department:result-confirm:view']
  const department = availableWorkspaces(permissions)[0]
  assert.ok(department)

  const items = visibleWorkspaceItems(department, permissions)
  assert.ok(items.some((item) => item.label === '周计划审批' && item.path === '/department/week-plan-approval'))
  assert.ok(items.some((item) => item.label === '周计划台账' && item.path === '/department/week-plan-ledger'))
  assert.ok(items.some((item) => item.label === '周成果审批' && item.path === '/department/week-result-approval'))
})
