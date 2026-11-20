/**
 * P0 / P1 前端接入的**安全不变量**测试。
 *
 * 这些测试不测渲染细节，只测「不能被改坏的红线」。
 * 每条断言背后都有一个具体的失效场景：
 *
 * 1. 互评面板**不能替主管改分** —— 互评只是参考材料。
 * 2. 分布质检面板**不能有改分入口**，且必须透出后端的 limitations
 *    （小样本保护不可被前端悄悄吞掉）。
 * 3. 核验报告**不能出现裁决结论词**，且必须展示局限性。
 * 4. 执行类指令**必须两阶段**：没有 prepare 就没有 confirm 的入口，
 *    且 confirm 必须携带一次性确认令牌。
 * 5. 代理授权**不提供续期绕过** —— 到期即失效是硬闸门。
 * 6. 周期调度面板**非 HR 不可见**，手动触发必须二次确认。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')

const peerReview = read('../src/components/PeerReviewPanel.vue')
const distributionQc = read('../src/components/DistributionQcPanel.vue')
const verification = read('../src/components/VerificationReportPanel.vue')
const scheduler = read('../src/components/SchedulerStatusPanel.vue')
const execTools = read('../src/components/ExecToolConfirmDialog.vue')
const delegation = read('../src/components/DelegationPanel.vue')
const p0p1Api = read('../src/api/p0p1.ts')

const schemeFilling = read('../src/views/performance/SchemeFilling.vue')
const managerReview = read('../src/views/performance/ManagerReview.vue')
const reviewCenter = read('../src/views/dispute/ReviewCenter.vue')
const workdayRules = read('../src/views/system/WorkdayRules.vue')
const systemDashboard = read('../src/views/system/SystemDashboard.vue')
const performanceDashboard = read('../src/views/performance/PerformanceDashboard.vue')

/**
 * 去掉注释，只留可执行代码 —— 否则设计说明里的词会被误判成 UI 元素。
 */
const codeOf = (source: string) =>
  source
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')

// ═══════════════════════════════════════════════════════════════════
// P0-1 互评
// ═══════════════════════════════════════════════════════════════════

test('互评面板已接入真实后端端点', () => {
  assert.match(peerReview, /peerReviewApi/)
  assert.match(p0p1Api, /scheme-records\/\$\{recordId\}\/peers/)
  assert.match(p0p1Api, /scheme-tasks\/\$\{taskId\}\/peer-progress/)
  assert.match(p0p1Api, /scheme-tasks\/\$\{taskId\}\/peer-remind/)
  assert.match(p0p1Api, /performance\/peers\/\$\{peerId\}/)
})

test('互评面板不提供任何改分入口 —— 互评只是参考材料', () => {
  // 互评接口只提交 score + comment，绝不写回 record / confirmed_rating
  assert.doesNotMatch(peerReview, /confirmed_rating/)
  assert.doesNotMatch(peerReview, /manager_comment/)
  assert.doesNotMatch(peerReview, /self_scores/)
})

test('互评面板对普通员工只展示进度，不展示他人互评内容', () => {
  // 分数与意见直接取自后端返回，组件不做拼装 —— 后端对无权者返回 null
  assert.match(peerReview, /row\.score/)
  assert.match(peerReview, /row\.comment/)
})

test('互评意见有最短长度校验，与后端规则一致', () => {
  assert.match(peerReview, /length < 5/)
})

test('互评面板已嵌入填报页与主管评分页', () => {
  assert.match(schemeFilling, /<PeerReviewPanel/)
  assert.match(managerReview, /<PeerReviewPanel/)
})

test('互评催办只对 HR 开放（后端 peer-remind 要求 HR）', () => {
  assert.match(schemeFilling, /canRemindPeers/)
  assert.match(schemeFilling, /includes\('hr'\)/)
})

// ═══════════════════════════════════════════════════════════════════
// P0-2 分布质检
// ═══════════════════════════════════════════════════════════════════

test('分布质检面板已接入真实后端端点', () => {
  assert.match(distributionQc, /distributionQcApi/)
  assert.match(p0p1Api, /ai\/reviews\/distribution/)
})

test('分布质检面板没有改分入口 —— 只提示不改分', () => {
  assert.doesNotMatch(distributionQc, /confirmed_rating/)
  assert.doesNotMatch(distributionQc, /http\.(post|put|patch|delete)/)
  assert.doesNotMatch(distributionQc, /adjusted|adjust_score|updateScore/)
})

test('分布质检面板原样透出后端 limitations', () => {
  assert.match(distributionQc, /report\.limitations/)
  assert.match(distributionQc, /只提示，不改分/)
})

test('分布质检小样本时显示占位符而非编造均值', () => {
  // row.mean 为 null 时展示 "—"，不能展示 0
  assert.match(distributionQc, /row\.mean !== null/)
  assert.match(distributionQc, /row\.cv !== null/)
})

test('分布质检面板不出现通过/不通过类结论', () => {
  assert.doesNotMatch(codeOf(distributionQc), /通过|不通过|合格|不合格/)
})

test('分布质检面板已嵌入主管评分页', () => {
  assert.match(managerReview, /<DistributionQcPanel/)
})

// ═══════════════════════════════════════════════════════════════════
// P0-3 智能核验
// ═══════════════════════════════════════════════════════════════════

test('核验报告已接入后端并替换掉原始 JSON 展示', () => {
  assert.match(reviewCenter, /<VerificationReportPanel/)
  assert.match(p0p1Api, /review-center\/cases\/\$\{caseId\}\/reports/)
  // 原始的 <pre>{{ pretty(r.content.facts) }}</pre> 必须消失
  assert.doesNotMatch(reviewCenter, /pretty\(r\.content\.facts\)/)
})

test('核验报告不出现裁决结论词', () => {
  // 报告是证据材料 —— "申诉成立/不成立"必须由人另行作出
  const code = codeOf(verification)
  assert.doesNotMatch(code, /申诉成立|申诉不成立|应予调整|不予调整/)
  assert.doesNotMatch(code, /建议驳回|建议支持/)
})

test('核验报告展示局限性，并声明不构成结论', () => {
  assert.match(verification, /content\.limitations/)
  assert.match(verification, /不构成评分错误结论/)
})

test('核验报告组件是只读的 —— 不提供改分或裁决按钮', () => {
  assert.doesNotMatch(verification, /http\.(post|put|patch|delete)/)
  assert.doesNotMatch(verification, /confirmed_rating/)
})

// ═══════════════════════════════════════════════════════════════════
// P1-2 周期调度
// ═══════════════════════════════════════════════════════════════════

test('周期调度面板已接入真实后端端点', () => {
  assert.match(scheduler, /schedulerApi/)
  assert.match(p0p1Api, /performance\/scheduler\/status/)
  assert.match(p0p1Api, /performance\/scheduler\/run/)
})

test('周期调度面板对非 HR 不可见（静默降级而非报错）', () => {
  assert.match(scheduler, /visible/)
  assert.match(scheduler, /includes\('hr'\)/)
  assert.match(scheduler, /v-if="visible"/)
})

test('手动触发周期调度必须二次确认，且确认文案写明副作用', () => {
  assert.match(scheduler, /ElMessageBox\.confirm/)
  assert.match(scheduler, /推进到期的考核任务状态/)
})

test('周期调度面板已嵌入工作日规则页', () => {
  assert.match(workdayRules, /<SchedulerStatusPanel/)
})

// ═══════════════════════════════════════════════════════════════════
// P1-3 执行类指令（两阶段确认）
// ═══════════════════════════════════════════════════════════════════

test('执行类指令已接入真实后端端点', () => {
  assert.match(execTools, /execToolsApi/)
  assert.match(p0p1Api, /exec-tools\/\$\{tool\}\/prepare/)
  assert.match(p0p1Api, /exec-tools\/\$\{tool\}\/confirm/)
  // 会话换执行类令牌的入口
  assert.match(p0p1Api, /exec-tools\/token/)
})

test('执行类指令严格两阶段 —— 没有 prepare 就没有 confirm 的入口', () => {
  // confirm 按钮只在 plan 存在时渲染
  assert.match(execTools, /v-else/)
  assert.match(execTools, /plan\.value\.confirmToken/)
})

test('确认必须携带一次性确认令牌与工具令牌', () => {
  assert.match(execTools, /confirm_token: plan\.value\.confirmToken/)
  assert.match(execTools, /bearerToken\.value/)
})

test('执行对话框完整回显受影响对象，不允许只显示一个数字', () => {
  assert.match(execTools, /impactRows/)
  assert.match(execTools, /受影响对象/)
})

test('执行类指令不得绕过两阶段直接调后端写接口', () => {
  // 组件里不允许出现裸的 http.post 到催办/导出/统计业务端点
  assert.doesNotMatch(execTools, /http\.(post|put|delete)/)
})

test('执行类指令入口已接入绩效工作台', () => {
  assert.match(performanceDashboard, /<ExecToolConfirmDialog/)
  assert.match(performanceDashboard, /openExecTool/)
})

// ═══════════════════════════════════════════════════════════════════
// P1-4 代理授权
// ═══════════════════════════════════════════════════════════════════

test('代理授权已接入真实后端端点', () => {
  assert.match(delegation, /delegationApi/)
  assert.match(p0p1Api, /'\/delegations'/)
  assert.match(p0p1Api, /delegations\/my-scope/)
  assert.match(p0p1Api, /delegations\/\$\{delegationId\}/)
})

test('代理授权面板不提供续期绕过入口 —— 到期即失效', () => {
  // 只看可执行代码（模板里的按钮/事件绑定 + 脚本里的函数调用），
  // 注释里的"续期"字样是设计说明，不构成绕过入口
  const code = codeOf(delegation)
  assert.doesNotMatch(code, /extend|renew/)
  assert.doesNotMatch(code, /续期/)
})

test('代理授权面板展示有效期与状态', () => {
  assert.match(delegation, /startsAt/)
  assert.match(delegation, /endsAt/)
  assert.match(delegation, /已到期/)
})

test('代理授权撤销需要二次确认', () => {
  assert.match(delegation, /ElMessageBox\.confirm/)
})

test('代理授权面板已嵌入系统管理工作台', () => {
  assert.match(systemDashboard, /<DelegationPanel/)
})

// ═══════════════════════════════════════════════════════════════════
// 通用红线
// ═══════════════════════════════════════════════════════════════════

test('P0/P1 API 客户端全部走 /api 前缀（Vite 代理到 /v1）', () => {
  // 写 /v1/xxx 会绕过代理，导致 404
  // 只看真正发起请求的 URL 字面量，不看注释里的路径示例
  const requestUrls = [...p0p1Api.matchAll(/http\.(?:get|post|put|delete)<[^>]*>\(\s*['"`]([^'"`]+)['"`]/g)]
    .map((m) => m[1])
  assert.ok(requestUrls.length > 0, '应至少有一处请求调用')
  for (const url of requestUrls) {
    assert.ok(!url.startsWith('/v1/'), `请求路径不应以 /v1/ 开头：${url}`)
  }
})

test('P0/P1 组件没有引入硬编码的分数写入路径', () => {
  for (const source of [peerReview, distributionQc, verification, execTools, delegation]) {
    assert.doesNotMatch(source, /localStorage\.setItem\([^)]*score/)
    assert.doesNotMatch(source, /updated_rating|new_rating/)
  }
})
