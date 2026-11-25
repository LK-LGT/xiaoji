/**
 * 🧩 **月计划支持动作的纯逻辑**（CI-5 阶段 2 第五轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``EmployeeMonthPlanSupportActions.vue`` 有 **391 行**，其中「模板兜底行 /
 * 打印 HTML 拼装 / HTML 转义 / 数字格式化 / 标题生成」全是**纯计算**。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖 el-dialog / 打印 API / 多个子组件，挂载失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 模板预览行（最小结构）。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型
 * （真实类型的日期字段是**可选的**）。
 */
export interface MonthPlanTemplateRowLike {
  rowNo: number
  taskContent: string
  performanceWeight: number
  deliverable: string
  quality: string
  estimatedStartDate?: string
  estimatedEndDate?: string
  estimatedValue: number
}

/**
 * 模板预览（最小结构）。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型。
 */
export interface MonthPlanTemplatePreviewLike<
  TRow extends MonthPlanTemplateRowLike = MonthPlanTemplateRowLike,
> {
  planMonth: string
  estimatedValueTotal?: number
  items: TRow[]
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ HTML 转义
// ═══════════════════════════════════════════════════════════════════════════

/**
 * HTML 转义。
 *
 * ⚠️ **顺序很重要** ⇒ 先转 ``&`` 再转其它，
 *    否则 ``&lt;`` 里的 ``&`` 会被二次转义成 ``&amp;lt;``。
 * ⚠️ **``null`` / ``undefined`` ⇒ 空串**（不显示 "null"）。
 */
export function escapeMonthPlanHtml(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * 拆成非空行（统一换行符 + 去空白 + 过滤空行）。
 *
 * ⚠️ **``\r\n`` 和 ``\r`` 都归一到 ``\n``** ⇒ Windows 上传的文本不会多出 ``\r``。
 */
export function splitMonthPlanTextLines(value?: string | null): string[] {
  return String(value || '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 数字格式化
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 金额格式化（带 ``¥`` 和千分位）。
 *
 * ⚠️ **``0`` ⇒ ``¥0``**（不是空）。
 */
export function formatMonthPlanCurrency(value: number): string {
  return `¥${Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

/**
 * 普通数字格式化（**无千分位**）。
 *
 * ⚠️ **打印表格里不能用千分位** ⇒ 否则列宽会被撑开。
 * ⚠️ **非有限数 ⇒ 空串**（表格单元格不显示 ``NaN``）。
 */
export function formatMonthPlanPlainNumber(value?: number | null): string {
  const numericValue = Number(value || 0)
  if (!Number.isFinite(numericValue)) return ''
  return numericValue.toLocaleString('zh-CN', { maximumFractionDigits: 2, useGrouping: false })
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 标题与权限
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 打印标题（``2026 年 11 月个人目标制定``）。
 *
 * ⚠️ **月份补零**（``11`` 而不是 ``1``）⇒ 表格对齐。
 * ⚠️ **解析失败 ⇒ 用空白占位模板**
 *    ⇒ 打印出来仍是一张完整的表（而不是 ``NaN 月``）。
 */
export function buildMonthPlanPrintTitle(planMonth: string): string {
  const [year, month] = String(planMonth || '').split('-')
  const monthNumber = Number(month)
  if (!year || !Number.isFinite(monthNumber)) return '        年      月个人目标制定'
  return `${year} 年 ${String(monthNumber).padStart(2, '0')} 月个人目标制定`
}

/**
 * 是否可以调整（暂停 / 取消）。
 *
 * ⚠️ **只有「已提交 / 已通过」能调整** ⇒ 已完成的计划不能改。
 */
export function canAdjustMonthPlan(status: string): boolean {
  return status === 'submitted' || status === 'approved'
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 模板兜底行
// ═══════════════════════════════════════════════════════════════════════════

/** 空白模板的行数。 */
export const MONTH_PLAN_TEMPLATE_BLANK_ROWS = 8

/**
 * 构造模板预览行。
 *
 * ⚠️ **有真实数据 ⇒ 用真实数据**；
 *    **没有 ⇒ 给 8 行空白行** ⇒ 打印出来是一张可手填的表。
 */
export function buildMonthPlanTemplateRows<TRow extends MonthPlanTemplateRowLike>(
  template: MonthPlanTemplatePreviewLike<TRow> | null | undefined,
): TRow[] {
  if (template?.items.length) return template.items
  return Array.from({ length: MONTH_PLAN_TEMPLATE_BLANK_ROWS }, (_, index) => ({
    rowNo: index + 1,
    taskContent: '',
    performanceWeight: 0,
    deliverable: '',
    quality: '',
    estimatedStartDate: '',
    estimatedEndDate: '',
    estimatedValue: 0,
  })) as TRow[]
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 打印渲染
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 渲染任务内容单元格。
 *
 * ⚠️ **单行 ⇒ 直接转义输出**；
 *    **多行 ⇒ 第一行是标题（加粗样式），其余是普通行**。
 * ⚠️ **空内容 ⇒ 空串**（不留 ``<br>``）。
 */
export function renderMonthPlanTaskContent(value?: string | null): string {
  const lines = splitMonthPlanTextLines(value)
  if (!lines.length) return ''
  if (lines.length === 1) return escapeMonthPlanHtml(lines[0])
  const [title, ...rest] = lines
  return `<span class="task-title">${escapeMonthPlanHtml(title)}</span>${rest.map((line) => `<span class="line">${escapeMonthPlanHtml(line)}</span>`).join('')}`
}

/**
 * 渲染考核标准单元格（交付物 + 质量）。
 *
 * ⚠️ **每一段都要有标签**（「交付物：」「质量：」）
 *    ⇒ 打印件上不会分不清哪个是哪个。
 * ⚠️ **空段落整行省略**（不留空 ``<span class="line">``）。
 */
export function renderMonthPlanStandard(deliverable?: string | null, quality?: string | null): string {
  const deliverableText = String(deliverable || '').trim()
  const qualityText = String(quality || '').trim()
  return [
    deliverableText ? `<span class="standard-label">交付物：</span>${escapeMonthPlanHtml(deliverableText)}` : '',
    qualityText ? `<span class="standard-label">质量：</span>${escapeMonthPlanHtml(qualityText)}` : '',
  ]
    .filter(Boolean)
    .map((line) => `<span class="line">${line}</span>`)
    .join('')
}

/**
 * 渲染一行的打印单元格内容。
 *
 * ⚠️ **权重 / 预估价值为空时不显示**（不是 ``0``）⇒ 空白模板更好看。
 */
export function buildMonthPlanTemplatePrintCells<TRow extends MonthPlanTemplateRowLike>(
  row: TRow,
  formatters: {
    formatPeriod: (start: string | undefined, end: string | undefined, fallback: string) => string
  },
): string {
  return `
    <tr class="template-print-item-row">
      <td class="cell-center">${escapeMonthPlanHtml(row.rowNo)}</td>
      <td>${renderMonthPlanTaskContent(row.taskContent)}</td>
      <td class="cell-center">${row.performanceWeight || ''}</td>
      <td>${renderMonthPlanStandard(row.deliverable, row.quality)}</td>
      <td class="cell-center">${escapeMonthPlanHtml(formatters.formatPeriod(row.estimatedStartDate, row.estimatedEndDate, ''))}</td>
      <td class="cell-center">${row.estimatedValue ? escapeMonthPlanHtml(formatMonthPlanPlainNumber(row.estimatedValue)) : ''}</td>
    </tr>
  `
}

/**
 * 渲染所有行的打印单元格内容。
 */
export function buildMonthPlanTemplatePrintRows<TRow extends MonthPlanTemplateRowLike>(
  rows: TRow[],
  formatters: {
    formatPeriod: (start: string | undefined, end: string | undefined, fallback: string) => string
  },
): string {
  return rows.map((row) => buildMonthPlanTemplatePrintCells(row, formatters)).join('')
}

/**
 * 渲染预估价值合计。
 *
 * ⚠️ **合计为 0 ⇒ 空串**（不是 ``0``）。
 */
export function renderMonthPlanEstimatedTotal(total?: number): string {
  return total ? escapeMonthPlanHtml(formatMonthPlanPlainNumber(total)) : ''
}
