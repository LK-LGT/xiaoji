import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const leaderRoot = new URL('../src/views/leader/', import.meta.url)

test('leader employee work list keeps continuous row numbers and opens month plan details', async () => {
  const source = await readFile(new URL('LeaderEmployeeWork.vue', leaderRoot), 'utf8')
  const [listSource] = source.split('<el-drawer')

  for (const label of ['序号', '类型', '周期', '工作内容', '状态', '更新时间', '操作']) {
    assert.match(listSource, new RegExp(`label="${label}"`))
  }
  for (const label of ['交付与结果', '质量标准', '预估时间', '绩效权重', '预估价值']) {
    assert.doesNotMatch(listSource, new RegExp(`label="${label}"`))
  }

  assert.match(source, /rowNumber\(\$index\)/)
  assert.match(source, /\(pageNo - 1\) \* pageSize \+ index \+ 1/)
  assert.match(source, /row\.workType === 'MONTH_PLAN'/)
  assert.match(source, /v-if="row\.workType !== 'MONTH_PLAN'"/)
  assert.match(source, /@click="openMonthPlan\(row\)"/)
  assert.match(source, /月计划详情/)
  assert.match(source, /monthPlanDetail\.tasks/)
  assert.match(source, /getLeaderEmployeeMonthPlanApi/)
  assert.match(source, /getDepartmentEmployeeMonthPlanApi/)
  assert.match(source, /collapseMonthPlanRows/)
  assert.match(source, /displayItems/)
  assert.match(source, /displayTotal/)
})

test('month plan task rows are rendered only inside the detail drawer', async () => {
  const source = await readFile(new URL('LeaderEmployeeWork.vue', leaderRoot), 'utf8')
  const drawerSource = source.split('<el-drawer')[1] ?? ''

  assert.match(drawerSource, /月计划任务/)
  assert.match(drawerSource, /该月计划暂无具体任务/)
  assert.match(drawerSource, /\{\{ \$index \+ 1 \}\}/)
  assert.match(drawerSource, /row\.taskName/)
  assert.match(drawerSource, /row\.taskContent/)
  for (const label of ['考核标准（交付物、质量）', '完成时间段', '绩效权重', '预估价值']) {
    assert.match(drawerSource, new RegExp(`label="${label}"`))
  }
  assert.match(drawerSource, /formatMonthPlanStandard\(row\.deliverable, row\.quality\)/)
  assert.match(drawerSource, /formatMonthPlanPeriod\(row\.estimatedStartDate, row\.estimatedEndDate\)/)
})

test('leader employee work pagination remains available for every work type', async () => {
  const source = await readFile(new URL('LeaderEmployeeWork.vue', leaderRoot), 'utf8')

  assert.match(source, /v-if="displayTotal > 0"/)
  assert.match(source, /:total="displayTotal"/)
  assert.match(source, /layout="total, sizes, prev, pager, next, jumper"/)
  assert.match(source, /:page-sizes="\[10, 20, 50, 100\]"/)
  assert.match(source, /workType: query\.workType \|\| undefined/)
})
