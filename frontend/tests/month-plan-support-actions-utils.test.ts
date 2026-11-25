import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildMonthPlanPrintTitle,
  buildMonthPlanTemplatePrintRows,
  buildMonthPlanTemplateRows,
  canAdjustMonthPlan,
  escapeMonthPlanHtml,
  formatMonthPlanCurrency,
  formatMonthPlanPlainNumber,
  MONTH_PLAN_TEMPLATE_BLANK_ROWS,
  renderMonthPlanEstimatedTotal,
  renderMonthPlanStandard,
  renderMonthPlanTaskContent,
  splitMonthPlanTextLines,
  type MonthPlanTemplateRowLike,
} from '../src/utils/monthPlanSupportActions.ts'

function makeRow(overrides: Partial<MonthPlanTemplateRowLike> = {}): MonthPlanTemplateRowLike {
  return {
    rowNo: 1,
    taskContent: '',
    performanceWeight: 0,
    deliverable: '',
    quality: '',
    estimatedStartDate: '',
    estimatedEndDate: '',
    estimatedValue: 0,
    ...overrides,
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ HTML 转义
// ═══════════════════════════════════════════════════════════════════════════

test('HTML 转义：五个特殊字符', () => {
  assert.equal(escapeMonthPlanHtml('<script>'), '&lt;script&gt;')
  assert.equal(escapeMonthPlanHtml('a & b'), 'a &amp; b')
  assert.equal(escapeMonthPlanHtml('"quoted"'), '&quot;quoted&quot;')
  assert.equal(escapeMonthPlanHtml("it's"), 'it&#39;s')
})

test('HTML 转义：& 先转（不会二次转义）', () => {
  assert.equal(escapeMonthPlanHtml('&lt;'), '&amp;lt;')
})

test('HTML 转义：null / undefined ⇒ 空串', () => {
  assert.equal(escapeMonthPlanHtml(null), '')
  assert.equal(escapeMonthPlanHtml(undefined), '')
})

test('HTML 转义：数字 ⇒ 字符串', () => {
  assert.equal(escapeMonthPlanHtml(123), '123')
})

test('拆行：\\r\\n 和 \\r 都归一到 \\n', () => {
  assert.deepEqual(splitMonthPlanTextLines('a\r\nb\rc'), ['a', 'b', 'c'])
})

test('拆行：去空白 + 过滤空行', () => {
  assert.deepEqual(splitMonthPlanTextLines('  a  \n\n  b  \n'), ['a', 'b'])
})

test('拆行：空值 ⇒ 空数组', () => {
  assert.deepEqual(splitMonthPlanTextLines(null), [])
  assert.deepEqual(splitMonthPlanTextLines(undefined), [])
  assert.deepEqual(splitMonthPlanTextLines(''), [])
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 数字格式化
// ═══════════════════════════════════════════════════════════════════════════

test('金额格式化：带 ¥ 和千分位', () => {
  assert.equal(formatMonthPlanCurrency(1234567), '¥1,234,567')
})

test('金额格式化：0 ⇒ ¥0（不是空）', () => {
  assert.equal(formatMonthPlanCurrency(0), '¥0')
})

test('普通数字：无千分位（打印表格用）', () => {
  assert.equal(formatMonthPlanPlainNumber(1234567), '1234567')
})

test('普通数字：非有限数 ⇒ 空串（不显示 NaN / Infinity）', () => {
  // ⚠️ `Number(NaN || 0)` 会因 NaN 是 falsy 而变成 0 ⇒ 走「0」分支；
  //    真正会返回空串的是 Infinity（truthy 但非有限）。
  assert.equal(formatMonthPlanPlainNumber(Number.POSITIVE_INFINITY), '')
  assert.equal(formatMonthPlanPlainNumber(Number.NEGATIVE_INFINITY), '')
})

test('普通数字：缺失 ⇒ 0', () => {
  assert.equal(formatMonthPlanPlainNumber(undefined), '0')
  assert.equal(formatMonthPlanPlainNumber(null), '0')
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 标题与权限
// ═══════════════════════════════════════════════════════════════════════════

test('打印标题：月份补零', () => {
  assert.equal(buildMonthPlanPrintTitle('2026-11'), '2026 年 11 月个人目标制定')
  assert.equal(buildMonthPlanPrintTitle('2026-01'), '2026 年 01 月个人目标制定')
})

test('打印标题：解析失败 ⇒ 空白占位模板', () => {
  assert.equal(buildMonthPlanPrintTitle('bad'), '        年      月个人目标制定')
  assert.equal(buildMonthPlanPrintTitle(''), '        年      月个人目标制定')
})

test('打印标题：缺月份 ⇒ 空白占位模板', () => {
  assert.equal(buildMonthPlanPrintTitle('2026'), '        年      月个人目标制定')
})

test('可否调整：只有已提交 / 已通过', () => {
  assert.equal(canAdjustMonthPlan('submitted'), true)
  assert.equal(canAdjustMonthPlan('approved'), true)
  assert.equal(canAdjustMonthPlan('draft'), false)
  assert.equal(canAdjustMonthPlan('completed'), false)
  assert.equal(canAdjustMonthPlan(''), false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 模板兜底行
// ═══════════════════════════════════════════════════════════════════════════

test('模板行：有真实数据 ⇒ 用真实数据', () => {
  const items = [makeRow({ rowNo: 1, taskContent: 'x' })]
  const rows = buildMonthPlanTemplateRows({ planMonth: '2026-11', items })
  assert.equal(rows, items)
})

test('模板行：没有数据 ⇒ 8 行空白行', () => {
  const rows = buildMonthPlanTemplateRows({ planMonth: '2026-11', items: [] })
  assert.equal(rows.length, MONTH_PLAN_TEMPLATE_BLANK_ROWS)
  assert.deepEqual(rows.map((r) => r.rowNo), [1, 2, 3, 4, 5, 6, 7, 8])
})

test('模板行：没有模板 ⇒ 8 行空白行', () => {
  assert.equal(buildMonthPlanTemplateRows(null).length, MONTH_PLAN_TEMPLATE_BLANK_ROWS)
  assert.equal(buildMonthPlanTemplateRows(undefined).length, MONTH_PLAN_TEMPLATE_BLANK_ROWS)
})

test('模板行：空白行的内容都为空', () => {
  const rows = buildMonthPlanTemplateRows(null)
  assert.equal(rows[0].taskContent, '')
  assert.equal(rows[0].performanceWeight, 0)
  assert.equal(rows[0].estimatedValue, 0)
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 打印渲染
// ═══════════════════════════════════════════════════════════════════════════

test('任务内容：单行 ⇒ 直接转义输出', () => {
  assert.equal(renderMonthPlanTaskContent('简单任务'), '简单任务')
})

test('任务内容：单行含 HTML ⇒ 被转义', () => {
  assert.equal(renderMonthPlanTaskContent('<b>x</b>'), '&lt;b&gt;x&lt;/b&gt;')
})

test('任务内容：多行 ⇒ 第一行是标题，其余是普通行', () => {
  const html = renderMonthPlanTaskContent('标题\n第二行\n第三行')
  assert.equal(
    html,
    '<span class="task-title">标题</span><span class="line">第二行</span><span class="line">第三行</span>',
  )
})

test('任务内容：空内容 ⇒ 空串', () => {
  assert.equal(renderMonthPlanTaskContent(''), '')
  assert.equal(renderMonthPlanTaskContent(null), '')
  assert.equal(renderMonthPlanTaskContent('   \n  '), '')
})

test('考核标准：交付物 + 质量各有标签', () => {
  const html = renderMonthPlanStandard('交付物A', '质量B')
  assert.equal(
    html,
    '<span class="line"><span class="standard-label">交付物：</span>交付物A</span>'
    + '<span class="line"><span class="standard-label">质量：</span>质量B</span>',
  )
})

test('考核标准：空段落整行省略', () => {
  const html = renderMonthPlanStandard('', '质量B')
  assert.equal(html, '<span class="line"><span class="standard-label">质量：</span>质量B</span>')
})

test('考核标准：两处都空 ⇒ 空串', () => {
  assert.equal(renderMonthPlanStandard('', ''), '')
  assert.equal(renderMonthPlanStandard(null, undefined), '')
})

test('考核标准：内容被转义', () => {
  assert.ok(renderMonthPlanStandard('<script>', '').includes('&lt;script&gt;'))
})

test('打印单元格：权重为空 ⇒ 不显示 0', () => {
  const html = buildMonthPlanTemplatePrintRows([makeRow()], { formatPeriod: () => '' })
  assert.ok(html.includes('<td class="cell-center"></td>'))
})

test('打印单元格：预估价值为空 ⇒ 不显示', () => {
  const html = buildMonthPlanTemplatePrintRows([makeRow({ estimatedValue: 0 })], { formatPeriod: () => '' })
  assert.ok(html.includes('<td class="cell-center"></td>'))
})

test('打印单元格：预估价值有值 ⇒ 无千分位', () => {
  const html = buildMonthPlanTemplatePrintRows([makeRow({ estimatedValue: 1234567 })], { formatPeriod: () => '' })
  assert.ok(html.includes('1234567'))
  assert.ok(!html.includes('1,234,567'))
})

test('打印单元格：格式化函数收到原始日期', () => {
  const calls: unknown[][] = []
  buildMonthPlanTemplatePrintRows(
    [makeRow({ estimatedStartDate: 'S', estimatedEndDate: 'E' })],
    { formatPeriod: (s, e, f) => { calls.push([s, e, f]); return '' } },
  )
  assert.deepEqual(calls[0], ['S', 'E', ''])
})

test('预估价值合计：0 ⇒ 空串', () => {
  assert.equal(renderMonthPlanEstimatedTotal(0), '')
  assert.equal(renderMonthPlanEstimatedTotal(undefined), '')
})

test('预估价值合计：有值 ⇒ 无千分位', () => {
  assert.equal(renderMonthPlanEstimatedTotal(1234567), '1234567')
})
