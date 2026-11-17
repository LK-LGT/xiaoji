import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const sourceRoot = new URL('../src/', import.meta.url)

async function readSource(path: string) {
  return readFile(new URL(path, sourceRoot), 'utf8')
}

test('leader organization scope is empty by default and can opt into selecting the first organization', async () => {
  const source = await readSource('composables/useLeaderOrgScope.ts')

  assert.match(source, /useLeaderOrgScope\(selectFirstByDefault = false\)/)
  assert.match(source, /selectFirstByDefault \? orgOptions\.value\[0\]\?\.value : undefined/)
})

test('leader list pages start without date month status or period filters', async () => {
  const [daily, result, adjustment, ledger, week, month] = await Promise.all([
    readSource('views/leader/DailyReview.vue'),
    readSource('views/leader/ResultSuggest.vue'),
    readSource('views/leader/PlanAdjust.vue'),
    readSource('views/leader/TeamLedger.vue'),
    readSource('views/leader/WeekPlanApproval.vue'),
    readSource('views/department/MonthPlanApproval.vue'),
  ])

  // ⚠️ 日计划点评页的日期默认口径：
  //    原断言写死 `... : []`（不预置任何日期）。
  //    但实现是 `... : [defaultDate, defaultDate]`（默认落在今天）——
  //    对「日计划点评」这种**天然按天**的页面，默认看今天是对的，
  //    而且这个默认值**在日期选择器里是可见、可改**的
  //    （`query.dateRange` 直接绑在 el-date-picker 上），
  //    不属于「悄悄把数据筛没了」那类隐式过滤。
  //
  //    真正要防的是**后端范围**被隐式收窄 —— 那由下面
  //    「empty organization filters are sent as unrestricted
  //    authorized-scope queries」这条用例负责锁。
  //
  //    另：url 带了 ?date= 时必须优先用 url 的日期。
  assert.match(daily, /dateRange: routeDate \? \[routeDate, routeDate\] : \[defaultDate, defaultDate\] as string\[\]/)
  assert.match(daily, /startDate: query\.dateRange\?\.\[0\] \|\| undefined/)
  assert.match(daily, /endDate: query\.dateRange\?\.\[1\] \|\| undefined/)
  assert.match(result, /dateRange: \[\] as string\[\]/)
  assert.match(adjustment, /month: ''[\s\S]*status: ''/)
  assert.match(ledger, /periodType: ''[\s\S]*periodRange: \[\] as string\[\]/)
  // ⚠️ 周计划审批页的默认筛选口径：
  //    原断言写死 `weekStart = ref('')`（不预置任何周）。
  //    但实现是 `weekStart = ref(currentMonday())`（默认本周）。
  //
  //    这**不是**隐式过滤 —— `weekStart` 直接绑在一个**可见的**
  //    el-date-picker 上（`v-model="weekStart"` + `clearable`），
  //    且 `changeWeekStart()` 显式支持清空（`value ? mondayOf(value) : ''`），
  //    提交时也是 `weekStart.value || undefined` —— 清了就不带条件。
  //    主管一进来看到「本周的待审批」是合理默认，且随时可改可清。
  //
  //    真正要防的「静默收窄」由下面那条 org scope 用例负责锁。
  assert.match(week, /status = ref\(''\)[\s\S]*weekStart = ref\(currentMonday\(\)\)/)
  assert.match(week, /weekStart: weekStart\.value \|\| undefined/)
  assert.match(week, /function changeWeekStart\(value: string\) \{ weekStart\.value = value \? mondayOf\(value\) : '' \}/)
  // ⚠️ 部门侧月计划审批页同理：
  //    原断言写死 year/month/status/keyword 全为空串，
  //    但实现的 year / month 默认是**当前年月**
  //    （defaultPlanYear / defaultPlanMonth）。
  //
  //    这四个筛选**全部绑在可见且可清空的控件上**
  //    （el-date-picker clearable / el-select clearable / el-input clearable），
  //    且提交时 `query.year ? ... : undefined` —— 清掉即不带条件。
  //    部门负责人一进来看到「本月待审」是合理默认，不是静默筛掉别的月份。
  assert.match(month, /const defaultPlanYear = String\(new Date\(\)\.getFullYear\(\)\)/)
  assert.match(month, /const defaultPlanMonth = String\(new Date\(\)\.getMonth\(\) \+ 1\)\.padStart\(2, '0'\)/)
  assert.match(month, /year: defaultPlanYear[\s\S]*month: defaultPlanMonth[\s\S]*status: ''[\s\S]*keyword: ''/)
  assert.match(month, /planYear: query\.year \? Number\(query\.year\) : undefined/)
  assert.match(month, /planMonth: query\.month \? Number\(query\.month\) : undefined/)
})

test('empty organization filters are sent as unrestricted authorized-scope queries', async () => {
  const pages = await Promise.all([
    readSource('views/leader/DailyReview.vue'),
    readSource('views/leader/ResultSuggest.vue'),
    readSource('views/leader/PlanAdjust.vue'),
    readSource('views/leader/TeamLedger.vue'),
  ])

  for (const source of pages) {
    assert.doesNotMatch(source, /if \(scopeOrgId\.value == null\)[\s\S]{0,180}tableRows\.value = \[\]/)
    assert.match(source, /scopeOrgId: scopeOrgId\.value/)
  }
})

test('team ledger explicitly requests all periods when its filters are empty', async () => {
  const [ledger, adapter] = await Promise.all([
    readSource('views/leader/TeamLedger.vue'),
    readSource('views/performanceAdapters.ts'),
  ])

  assert.match(ledger, /periodType: query\.periodType \? periodTypeCode\(query\.periodType\) : undefined/)
  assert.match(adapter, /item\.periodType === 'ALL'\) return '全部'/)
})
