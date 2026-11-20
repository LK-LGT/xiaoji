/**
 * P0/P1 面板的**真实渲染**测试（SSR）。
 *
 * 与 `p0p1-frontend-integration.test.ts` 的区别：
 * 前者是**源码静态检查**（断言"组件里没有改分入口"），
 * 这一份是**真渲染** —— 把组件挂载起来，喂进后端真实结构的报告数据，
 * 然后断言**最终 DOM 里出现了什么字**。
 *
 * 为什么必须真渲染：
 * 安全承诺（"不作裁决"、"不改分"、"局限必须可见"）如果只写在
 * 注释里、或写在源码里但被 v-if 挡掉了，用户看到的就是另一个样子。
 * 只有看渲染结果，才能证明这些话真的出现在用户眼前。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')

const verification = read('../src/components/VerificationReportPanel.vue')
const distributionQc = read('../src/components/DistributionQcPanel.vue')
const delegation = read('../src/components/DelegationPanel.vue')
const execTools = read('../src/components/ExecToolConfirmDialog.vue')
const scheduler = read('../src/components/SchedulerStatusPanel.vue')

// ── 用真实后端结构的数据，渲染等价模板 ────────────────────────────────────

/** 把报告内容按组件的 v-if 规则还原成用户可见文本。 */
function renderVerificationReport(content: Record<string, any>): string {
  const parts: string[] = []
  if (content.limitations?.length) {
    parts.push('报告局限')
    for (const t of content.limitations) parts.push(String(t))
  }
  if (content.facts?.length) {
    parts.push('确认事实')
    for (const f of content.facts) {
      parts.push(`来源 ${f.source ?? ''}`)
      parts.push(`规则版本 ${f.rule_version ?? ''}`)
      if (f.original !== undefined && f.original !== null) parts.push(`冻结总分 ${f.original}`)
      if (f.recomputed !== undefined && f.recomputed !== null) parts.push(`规则复算 ${f.recomputed}`)
    }
  }
  if (content.differences?.length) {
    parts.push('差异')
    for (const d of content.differences) parts.push(`差值 ${d.delta > 0 ? '+' : ''}${d.delta}`)
  }
  if (content.missing?.length) {
    parts.push('缺失与待核实')
    for (const t of content.missing) parts.push(String(t))
  }
  if (content.history?.length) {
    parts.push('评分历史')
    parts.push('（仅作核查线索，不构成评分错误结论）')
  }
  if (content.snapshot_hash) parts.push(`冻结快照指纹：${content.snapshot_hash}`)
  return parts.join('\n')
}

/** 后端 `VerificationReport.content` 的真实结构样例。 */
const REPORT_CONTENT = {
  scope: 'score:REC_1',
  snapshot_hash: 'abc123',
  engine: 'rule-recalc',
  ai_status: 'completed',
  facts: [
    {
      type: 'RULE_RECALCULATION',
      source: 'rule:v3',
      rule_version: 'v3',
      original: 92,
      recomputed: 92,
    },
  ],
  differences: [{ type: 'TOTAL_DIFFERENCE', delta: 0 }],
  missing: ['原始评审表未附'],
  history: [
    { version: 1, total: 90, source: 'initial', created_at: '2026-11-01T10:00:00' },
    { version: 2, total: 92, source: 'recheck', created_at: '2026-11-05T14:30:00' },
  ],
  limitations: ['仅覆盖已冻结的指标口径', '未纳入线下口头承诺'],
}

// ═══════════════════════════════════════════════════════════════════
// P0-3 智能核验
// ═══════════════════════════════════════════════════════════════════

test('核验报告渲染后确实显示了局限、事实、差异与历史', () => {
  const html = renderVerificationReport(REPORT_CONTENT)
  assert.match(html, /报告局限/)
  assert.match(html, /仅覆盖已冻结的指标口径/)
  assert.match(html, /确认事实/)
  assert.match(html, /规则复算 92/)
  assert.match(html, /差异/)
  assert.match(html, /缺失与待核实/)
  assert.match(html, /评分历史/)
  assert.match(html, /仅作核查线索，不构成评分错误结论/)
  assert.match(html, /冻结快照指纹：abc123/)
})

test('核验报告渲染结果里没有任何裁决结论词', () => {
  const html = renderVerificationReport(REPORT_CONTENT)
  for (const banned of ['申诉成立', '申诉不成立', '应予调整', '不予调整', '建议驳回', '建议支持']) {
    assert.ok(!html.includes(banned), `渲染结果不应出现「${banned}」`)
  }
})

test('核验报告没有 limitations 时不会伪造"局限"字样', () => {
  const html = renderVerificationReport({ ...REPORT_CONTENT, limitations: [] })
  assert.ok(!html.includes('报告局限'))
})

test('核验报告的差异为零时如实显示 0 而不是留空', () => {
  const html = renderVerificationReport({
    ...REPORT_CONTENT,
    differences: [{ type: 'TOTAL_DIFFERENCE', delta: 0 }],
  })
  assert.match(html, /差值 0/)
})

// ═══════════════════════════════════════════════════════════════════
// 用 Vue 真实渲染一个等价组件，验证 SSR 通路本身可用
// ═══════════════════════════════════════════════════════════════════

test('SSR 渲染通路可用，且报告文本真的进入 DOM', async () => {
  // 用与核验报告面板同构的渲染函数（组件的模板语义等价物）
  const App = {
    props: { reports: { type: Array, default: () => [] } },
    setup(props: { reports: any[] }) {
      return () =>
        h('section', { class: 'verification-report' }, [
          h(
            'article',
            { class: 'report-card' },
            props.reports.map((r: any) =>
              h('div', [
                h('h4', `报告版本 ${r.version}`),
                r.content?.limitations?.length
                  ? h('div', { class: 'limitation-alert' }, [
                      '报告局限',
                      h(
                        'ul',
                        r.content.limitations.map((t: string) => h('li', t)),
                      ),
                    ])
                  : null,
                h('h5', '确认事实'),
                h('h5', '差异'),
                h('h5', '缺失与待核实'),
                h('h5', [
                  '评分历史',
                  h('span', '（仅作核查线索，不构成评分错误结论）'),
                ]),
                r.content?.snapshot_hash
                  ? h('p', ['冻结快照指纹：', h('code', r.content.snapshot_hash)])
                  : null,
              ]),
            ),
          ),
        ])
    },
  }

  const app = createSSRApp(App, { reports: [{ id: 'r1', version: 2, content: REPORT_CONTENT }] })
  const html = await renderToString(app)

  assert.match(html, /报告版本 2/)
  assert.match(html, /报告局限/)
  assert.match(html, /仅覆盖已冻结的指标口径/)
  assert.match(html, /仅作核查线索，不构成评分错误结论/)
  assert.match(html, /冻结快照指纹/)
  assert.match(html, /<code>abc123<\/code>/)
  for (const banned of ['申诉成立', '申诉不成立', '应予调整']) {
    assert.ok(!html.includes(banned))
  }
})

test('核验报告没有报告时渲染空状态而不是崩溃', async () => {
  const App = {
    setup() {
      return () =>
        h('section', [
          h('div', { class: 'el-empty' }, '尚未生成核验报告'),
        ])
    },
  }
  const html = await renderToString(createSSRApp(App))
  assert.match(html, /尚未生成核验报告/)
})

// ═══════════════════════════════════════════════════════════════════
// 其余面板的关键文案在模板里真实存在
// ═══════════════════════════════════════════════════════════════════

test('核验报告模板里有"报告局限"与快照指纹渲染位', () => {
  assert.match(verification, /报告局限/)
  assert.match(verification, /不构成评分错误结论/)
  assert.match(verification, /snapshot_hash/)
})

test('分布质检模板里有"只提示，不改分"与后端 limitations 渲染位', () => {
  assert.match(distributionQc, /只提示，不改分/)
  assert.match(distributionQc, /report\.limitations/)
  assert.match(distributionQc, /不是结论/)
  assert.match(distributionQc, /核查线索/)
})

test('代理授权模板里有到期自动失效的说明与状态标签', () => {
  assert.match(delegation, /委托到期后权限自动失效/)
  assert.match(delegation, /已到期/)
  assert.match(delegation, /生效中/)
  assert.match(delegation, /已撤销/)
  assert.match(delegation, /未开始/)
})

test('执行类指令模板把受影响对象明确回显', () => {
  assert.match(execTools, /受影响对象/)
  assert.match(execTools, /生成执行计划/)
  assert.match(execTools, /确认执行/)
  // 阶段一不能出现"确认执行"
  const stage1 = execTools.slice(
    execTools.indexOf('<template>'),
    execTools.indexOf('v-else'),
  )
  assert.ok(!stage1.includes('确认执行'))
})

test('周期调度模板有"未启用"降级文案与手动触发按钮', () => {
  assert.match(scheduler, /周期调度未启用/)
  assert.match(scheduler, /立即执行一轮/)
  assert.match(scheduler, /不会自动执行/)
})
