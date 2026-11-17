/**
 * 考核周期（period）格式统一 —— **BUG-R6-26**。
 *
 * ## 为什么需要这个模块
 *
 * 系统里存在**两种周期格式**，语义相同、写法不同：
 *
 * | 来源                    | 格式       | 示例        |
 * |-------------------------|------------|-------------|
 * | 考核任务 `period`       | `YYYYMM`   | `202611`    |
 * | 月计划 `plan_month`     | `YYYY-MM`  | `2026-11`   |
 *
 * 过去前端到处**手写字符串拼接**来回转换：
 *
 * ```ts
 * plan.weekStart.slice(0, 7)                    // '2026-11-xx' → '2026-11'
 * `${period.slice(0, 4)}-${period.slice(4)}`    // '202611' → '2026-11'
 * ```
 *
 * 这种写法有三重问题：
 *
 * 1. **口径分叉** —— 同一件事有 N 种写法，改一处漏一处；
 * 2. **静默出错** —— `slice` 越界不报错，只是产出垃圾字符串；
 * 3. **不好测** —— 逻辑散落在各个 `.vue` 里，没法集中断言。
 *
 * ## 约定
 *
 * - **紧凑 `YYYYMM` 是权威格式**（与后端 `AssessmentTask.period` 一致）。
 * - 所有**入参**（查询参数、表单值）统一走 {@link normalizePeriod}，
 *   输出紧凑格式；
 * - 所有**展示**统一走 {@link formatPeriodLabel} / {@link formatPeriodShort}；
 * - 需要按月分组、比较时用 {@link periodMonthKey}（得到 `YYYY-MM`），
 *   因为月计划的 `plan_month` 在库里就是 `YYYY-MM`。
 *
 * ⚠️ **不要用 `slice` 手搓** —— 新增代码请一律用本模块。
 */

/** 紧凑格式：`YYYYMM` */
const COMPACT_RE = /^(\d{4})(\d{2})$/
/** 带横杠格式：`YYYY-MM` */
const DASHED_RE = /^(\d{4})-(\d{2})$/

/**
 * 把任意受支持的周期格式归一化为**紧凑 `YYYYMM`**（权威格式）。
 *
 * @param value `202611` / `2026-11` / `2026-11-01` 均可
 * @returns 紧凑格式；无法解析时返回空串
 *
 * @example
 * normalizePeriod('202611')   // '202611'
 * normalizePeriod('2026-11')  // '202611'
 * normalizePeriod('2026-11-01') // '202611'
 * normalizePeriod('abc')      // ''
 */
export function normalizePeriod(value?: string | null): string {
  const text = String(value ?? '').trim()
  if (!text) return ''

  // `YYYY-MM`
  const dashed = DASHED_RE.exec(text)
  if (dashed) {
    const month = Number(dashed[2])
    return month >= 1 && month <= 12 ? `${dashed[1]}${dashed[2]}` : ''
  }

  // `YYYYMM`
  const compact = COMPACT_RE.exec(text)
  if (compact) {
    const month = Number(compact[2])
    return month >= 1 && month <= 12 ? `${compact[1]}${compact[2]}` : ''
  }

  // `YYYY-MM-DD` —— 取前 7 位当 `YYYY-MM` 再走一遍
  const dateMatch = /^(\d{4})-(\d{2})-\d{2}$/.exec(text)
  if (dateMatch) {
    const month = Number(dateMatch[2])
    return month >= 1 && month <= 12 ? `${dateMatch[1]}${dateMatch[2]}` : ''
  }

  return ''
}

/**
 * 把周期转成**带横杠 `YYYY-MM`**。
 *
 * ⚠️ 用途仅限「按月分组的 key」与「月计划 `plan_month` 对齐」——
 * **不要**用它做接口入参（入参统一用紧凑格式）。
 */
export function toDashedPeriod(value?: string | null): string {
  const compact = normalizePeriod(value)
  return compact ? `${compact.slice(0, 4)}-${compact.slice(4, 6)}` : ''
}

/**
 * 从**任意日期/周期字符串**里取出它所属的月份 key（`YYYY-MM`）。
 *
 * ⚠️ 这是替代满地 `date.slice(0, 7)` 的正确做法 ——
 * `slice` 遇到空串/非法串会静默产出 `''`，
 * 而这里会给出明确的可判定结果。
 *
 * @example
 * periodMonthKey('2026-11-03') // '2026-11'
 * periodMonthKey('202611')     // '2026-11'
 */
export function periodMonthKey(value?: string | null): string {
  const text = String(value ?? '').trim()
  if (!text) return ''

  const dashed = DASHED_RE.exec(text)
  if (dashed) return `${dashed[1]}-${dashed[2]}`

  const compact = COMPACT_RE.exec(text)
  if (compact) return `${compact[1]}-${compact[2]}`

  const dateMatch = /^(\d{4})-(\d{2})-\d{2}/.exec(text)
  if (dateMatch) return `${dateMatch[1]}-${dateMatch[2]}`

  return ''
}

/**
 * 从周期里取出年份（`2026`）。
 */
export function periodYear(value?: string | null): string {
  const compact = normalizePeriod(value)
  return compact ? compact.slice(0, 4) : ''
}

/**
 * 从周期里取出月份（`11`，去掉前导零）。
 */
export function periodMonthNumber(value?: string | null): string {
  const compact = normalizePeriod(value)
  return compact ? String(Number(compact.slice(4, 6))) : ''
}

/**
 * 展示用中文标签：`2026年11月`。
 *
 * @example
 * formatPeriodLabel('202611')  // '2026年11月'
 * formatPeriodLabel('2026-11') // '2026年11月'
 */
export function formatPeriodLabel(value?: string | null, emptyText = '—'): string {
  const compact = normalizePeriod(value)
  if (!compact) return emptyText
  return `${compact.slice(0, 4)}年${Number(compact.slice(4, 6))}月`
}

/**
 * 展示用短标签：`2026-11`。
 *
 * @example
 * formatPeriodShort('202611') // '2026-11'
 */
export function formatPeriodShort(value?: string | null, emptyText = '—'): string {
  const dashed = toDashedPeriod(value)
  return dashed || emptyText
}

/**
 * 展示用紧凑标签：`202611`（原样透出权威格式）。
 */
export function formatPeriodCompact(value?: string | null, emptyText = '—'): string {
  const compact = normalizePeriod(value)
  return compact || emptyText
}

/**
 * 当前月份（紧凑 `YYYYMM`）。
 */
export function currentPeriod(now: Date = new Date()): string {
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}`
}

/**
 * 当前月份（`YYYY-MM`）。
 */
export function currentPeriodMonth(now: Date = new Date()): string {
  const compact = currentPeriod(now)
  return `${compact.slice(0, 4)}-${compact.slice(4, 6)}`
}

/**
 * 周期加减月份。
 *
 * @param value 任意受支持的周期格式
 * @param offset 偏移月数（可负）
 *
 * @example
 * shiftPeriod('202611', 1)  // '202612'
 * shiftPeriod('202611', -1) // '202610'
 * shiftPeriod('202601', -1) // '202512'
 */
export function shiftPeriod(value: string | null | undefined, offset: number): string {
  const compact = normalizePeriod(value)
  if (!compact) return ''
  const year = Number(compact.slice(0, 4))
  const month = Number(compact.slice(4, 6))
  const date = new Date(year, month - 1 + offset, 1)
  return `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, '0')}`
}

/**
 * 周期是否相等（**格式无关**）。
 *
 * @example
 * isSamePeriod('202611', '2026-11') // true
 */
export function isSamePeriod(left?: string | null, right?: string | null): boolean {
  const a = normalizePeriod(left)
  const b = normalizePeriod(right)
  return !!a && !!b && a === b
}

/**
 * 周期是否合法。
 */
export function isValidPeriod(value?: string | null): boolean {
  return !!normalizePeriod(value)
}
