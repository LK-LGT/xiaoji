import assert from 'node:assert/strict'
import test from 'node:test'
import { fallbackForPath, isUsableBackPath } from '../src/navigation/smartBack.ts'

test('accepts only same-app back paths that are not blocked auth pages', () => {
  assert.equal(isUsableBackPath('/employee/week-plans', '/employee/week-plans/3'), true)
  assert.equal(isUsableBackPath('/login?redirect=/employee/week-plans', '/employee/week-plans'), false)
  assert.equal(isUsableBackPath('/change-password', '/employee/week-plans'), false)
  assert.equal(isUsableBackPath('https://example.com', '/employee/week-plans'), false)
  assert.equal(isUsableBackPath('/employee/week-plans/3', '/employee/week-plans/3'), false)
})

test('chooses detail/list fallbacks for nested business pages', () => {
  assert.equal(fallbackForPath('/employee/week-plans/12/items/7/results/submit'), '/employee/week-plans/12')
  assert.equal(fallbackForPath('/employee/week-plans/12/edit'), '/employee/week-plans/12')
  assert.equal(fallbackForPath('/employee/week-plans/12'), '/employee/week-plans')
  assert.equal(fallbackForPath('/employee/month-plans/9/edit'), '/employee/month-plans/9')
  assert.equal(fallbackForPath('/employee/month-plans/9'), '/employee/month-plans')
  assert.equal(fallbackForPath('/employee/month-edit'), '/employee/month-plans')
  assert.equal(fallbackForPath('/dispute/cases/5/review-panel'), '/dispute/cases/5')
  assert.equal(fallbackForPath('/dispute/cases/5'), '/dispute/cases')
})

test('falls back to workspace homes for top-level sections and messages', () => {
  assert.equal(fallbackForPath('/employee/results/submit'), '/employee/results')
  assert.equal(fallbackForPath('/leader/team-ledger'), '/leader/workbench')
  assert.equal(fallbackForPath('/department/result-confirm'), '/department/dashboard')
  assert.equal(fallbackForPath('/system/roles'), '/system/dashboard')
  assert.equal(fallbackForPath('/messages', '/employee/dashboard'), '/employee/dashboard')
})
