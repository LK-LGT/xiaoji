import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  currentPeriod,
  currentPeriodMonth,
  formatPeriodCompact,
  formatPeriodLabel,
  formatPeriodShort,
  isSamePeriod,
  isValidPeriod,
  normalizePeriod,
  periodMonthKey,
  periodMonthNumber,
  periodYear,
  shiftPeriod,
  toDashedPeriod,
} from '../src/utils/period.ts'

// ═══════════════════════════════════════════════════════════════════
// BUG-R6-26：周期格式统一 —— 权威格式为紧凑 `YYYYMM`
// ═══════════════════════════════════════════════════════════════════

test('normalizePeriod 把所有受支持格式归一化为紧凑 YYYYMM', () => {
  assert.equal(normalizePeriod('202611'), '202611')
  assert.equal(normalizePeriod('2026-11'), '202611')
  assert.equal(normalizePeriod('2026-11-01'), '202611')
  assert.equal(normalizePeriod('2026-11-30'), '202611')
  // 首尾空白必须被容忍
  assert.equal(normalizePeriod('  202611  '), '202611')
  assert.equal(normalizePeriod('  2026-11  '), '202611')
})

test('normalizePeriod 对空值与非法值返回空串而不是抛错', () => {
  assert.equal(normalizePeriod(undefined), '')
  assert.equal(normalizePeriod(null), '')
  assert.equal(normalizePeriod(''), '')
  assert.equal(normalizePeriod('   '), '')
  assert.equal(normalizePeriod('abc'), '')
  assert.equal(normalizePeriod('2026'), '')
  assert.equal(normalizePeriod('202613'), '', '13 月必须被拒绝')
  assert.equal(normalizePeriod('202600'), '', '0 月必须被拒绝')
  assert.equal(normalizePeriod('2026-13'), '', '13 月必须被拒绝')
  assert.equal(normalizePeriod('26-11'), '')
  assert.equal(normalizePeriod('2026111'), '')
})

test('normalizePeriod 保留合法月份的零填充', () => {
  assert.equal(normalizePeriod('202601'), '202601')
  assert.equal(normalizePeriod('2026-01'), '202601')
})

test('toDashedPeriod 输出带横杠格式', () => {
  assert.equal(toDashedPeriod('202611'), '2026-11')
  assert.equal(toDashedPeriod('2026-11'), '2026-11')
  assert.equal(toDashedPeriod('2026-11-01'), '2026-11')
  assert.equal(toDashedPeriod(''), '')
  assert.equal(toDashedPeriod('abc'), '')
})

test('periodMonthKey 从任意日期/周期串取月份 key', () => {
  assert.equal(periodMonthKey('2026-11-03'), '2026-11')
  assert.equal(periodMonthKey('202611'), '2026-11')
  assert.equal(periodMonthKey('2026-11'), '2026-11')
  assert.equal(periodMonthKey(''), '')
  assert.equal(periodMonthKey('abc'), '')
})

test('periodMonthKey 能吃掉带时间戳的日期串', () => {
  assert.equal(periodMonthKey('2026-11-03T00:00:00'), '2026-11')
  assert.equal(periodMonthKey('2026-11-03 10:22:33'), '2026-11')
})

test('periodYear / periodMonthNumber 拆解周期', () => {
  assert.equal(periodYear('202611'), '2026')
  assert.equal(periodYear('2026-11'), '2026')
  assert.equal(periodMonthNumber('202611'), '11')
  assert.equal(periodMonthNumber('202601'), '1', '前导零必须去掉')
  assert.equal(periodMonthNumber('2026-11'), '11')
  assert.equal(periodYear(''), '')
  assert.equal(periodMonthNumber(''), '')
})

test('formatPeriodLabel 输出中文标签', () => {
  assert.equal(formatPeriodLabel('202611'), '2026年11月')
  assert.equal(formatPeriodLabel('2026-11'), '2026年11月')
  assert.equal(formatPeriodLabel('2026-11-01'), '2026年11月')
  assert.equal(formatPeriodLabel('202601'), '2026年1月')
  assert.equal(formatPeriodLabel(''), '—')
  assert.equal(formatPeriodLabel('', '暂无'), '暂无')
})

test('formatPeriodShort 输出短标签', () => {
  assert.equal(formatPeriodShort('202611'), '2026-11')
  assert.equal(formatPeriodShort('2026-11'), '2026-11')
  assert.equal(formatPeriodShort(''), '—')
})

test('formatPeriodCompact 原样透出权威格式', () => {
  assert.equal(formatPeriodCompact('2026-11'), '202611')
  assert.equal(formatPeriodCompact('202611'), '202611')
  assert.equal(formatPeriodCompact('abc'), '—')
})

test('currentPeriod / currentPeriodMonth 给出当前月', () => {
  const now = new Date(2026, 10, 17) // 2026-11-17
  assert.equal(currentPeriod(now), '202611')
  assert.equal(currentPeriodMonth(now), '2026-11')
})

test('shiftPeriod 跨年正确进位', () => {
  assert.equal(shiftPeriod('202611', 1), '202612')
  assert.equal(shiftPeriod('202611', -1), '202610')
  assert.equal(shiftPeriod('202601', -1), '202512')
  assert.equal(shiftPeriod('202612', 1), '202701')
  assert.equal(shiftPeriod('2026-12', 1), '202701', '带横杠输入也要能算')
  assert.equal(shiftPeriod('', 1), '')
  assert.equal(shiftPeriod('abc', 1), '')
})

test('isSamePeriod 跨格式比较', () => {
  assert.equal(isSamePeriod('202611', '2026-11'), true)
  assert.equal(isSamePeriod('202611', '202611'), true)
  assert.equal(isSamePeriod('202611', '202612'), false)
  assert.equal(isSamePeriod('', '202611'), false, '空值不得被当成相等')
  assert.equal(isSamePeriod(null, null), false)
})

test('isValidPeriod 判定合法性', () => {
  assert.equal(isValidPeriod('202611'), true)
  assert.equal(isValidPeriod('2026-11'), true)
  assert.equal(isValidPeriod('2026-11-01'), true)
  assert.equal(isValidPeriod('202613'), false)
  assert.equal(isValidPeriod(''), false)
  assert.equal(isValidPeriod(null), false)
})

// ═══════════════════════════════════════════════════════════════════
// 回归防护：全前端不得再出现手写 slice 拼周期
// ═══════════════════════════════════════════════════════════════════

const SRC_ROOT = fileURLToPath(new URL('../src/', import.meta.url))

function listSourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = `${dir}${entry}`
    if (statSync(full).isDirectory()) {
      out.push(...listSourceFiles(`${full}/`))
    } else if (/\.(ts|vue)$/.test(entry)) {
      out.push(full)
    }
  }
  return out
}

test('前端源码不再用手写 slice 拼周期', () => {
  const files = listSourceFiles(SRC_ROOT)
  const offenders: string[] = []
  for (const file of files) {
    // 豁免：
    //  - `utils/period.ts` 本身是权威实现，内部对**已归一化**的紧凑串做切片是合理的；
    //  - `MonthResultTaskEditor.vue` 的 `.slice(0, 4)` 是「AI 问题列表最多取 4 条」，与周期无关。
    if (file.endsWith('utils/period.ts') || file.endsWith('utils\\period.ts')) continue
    if (file.endsWith('MonthResultTaskEditor.vue')) continue
    const content = readFileSync(file, 'utf8')
    // 只针对「看起来像周期」的 slice：xxx.slice(0, 7) / xxx.slice(0, 4) / xxx.slice(4)
    const matches = content.match(/\.(?:slice|substring|substr)\(\s*0\s*,\s*[47]\s*\)/g) || []
    if (matches.length) {
      offenders.push(`${file}: ${matches.join(', ')}`)
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `以下文件仍手写 slice 拼周期，应改用 @/utils/period：\n${offenders.join('\n')}`,
  )
})
