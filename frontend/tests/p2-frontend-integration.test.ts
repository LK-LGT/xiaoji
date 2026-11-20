/**
 * P2 前端接入的**安全不变量**测试。
 *
 * 这些测试不测渲染细节，只测「不能被改坏的红线」。
 * 每条断言背后都有一个具体的失效场景：
 *
 * 1. 免责提示必须在 DOM 里（哪怕默认折叠）——
 *    否则用户截图转发后，AI 建议会脱离「这是建议」的语境。
 * 2. 语义质检面板**不能有写回评语的按钮**——
 *    一旦 AI 能改评语，「人保留最终决定权」这条就破了。
 * 3. 小样本岗位对比**前端要自己再挡一道**——
 *    不能只依赖后端 suppression，后端漏了前端就泄露了。
 * 4. 薪酬导出必须默认不导出姓名，且导出文件里带免责说明。
 * 5. 反馈提交**不能改分数** —— 只能落 ai_feedback。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')

const semanticQc = read('../src/components/SemanticQcPanel.vue')
const trendReport = read('../src/components/TrendReportPanel.vue')
const payroll = read('../src/components/PayrollExportPanel.vue')
const feedback = read('../src/components/FeedbackPanel.vue')
const caseLibrary = read('../src/components/CaseLibraryPanel.vue')
const dataSource = read('../src/components/DataSourcePolicyPanel.vue')
const managerReview = read('../src/views/performance/ManagerReview.vue')
const reviewCenter = read('../src/views/dispute/ReviewCenter.vue')
const aiManagement = read('../src/views/system/AiManagement.vue')
const p2Api = read('../src/api/p2.ts')

// ═══════════════════════════════════════════════════════════════════
// P2-1 语义质检面板
// ═══════════════════════════════════════════════════════════════════

test('语义质检面板始终渲染免责提示，且默认折叠', () => {
  // el-collapse 默认关闭 —— 展开动作交给用户
  assert.match(semanticQc, /el-collapse/)
  // 免责正文用常驻文案兜底，不能依赖后端返回才显示
  assert.match(semanticQc, /defaultDisclaimer/)
  assert.match(semanticQc, /不构成评价结论/)
})

test('语义质检面板展示判断依据与规则号，不展示无依据的结论', () => {
  assert.match(semanticQc, /依据/)
  assert.match(semanticQc, /ruleId/)
  assert.match(semanticQc, /issue\.basis/)
  assert.match(semanticQc, /risk\.basis/)
})

test('语义质检面板是只读的 —— 不提供任何写回评语的入口', () => {
  // 任何把 AI 产出写回业务字段的调用都必须不存在
  assert.doesNotMatch(semanticQc, /manager_comment/)
  assert.doesNotMatch(semanticQc, /confirmed_rating/)
  assert.doesNotMatch(semanticQc, /emit\(['"]update/)
  // 不允许 POST / PUT / PATCH
  assert.doesNotMatch(semanticQc, /http\.(post|put|patch|delete)/)
})

test('语义质检面板在主管评分时展示在评价意见上方', () => {
  assert.match(managerReview, /SemanticQcPanel/)
  const panelAt = managerReview.indexOf('<SemanticQcPanel')
  const formAt = managerReview.indexOf('<el-form :model="reviewFormData"')
  assert.ok(panelAt > 0 && formAt > 0)
  // 面板必须在表单之前 —— 主管写评语时就能看到问题
  assert.ok(panelAt < formAt)
})

// ═══════════════════════════════════════════════════════════════════
// P2-2 趋势复盘报表
// ═══════════════════════════════════════════════════════════════════

test('岗位横向对比在前端也做小样本保护，不依赖后端兜底', () => {
  assert.match(trendReport, /smallSample/)
  // 小样本时明细表必须整体不渲染
  assert.match(trendReport, /v-if="position\.smallSample"/)
  assert.match(trendReport, /v-else/)
})

test('趋势报表里的周期展示使用共享的周期工具而不是手写 slice', () => {
  assert.match(trendReport, /@\/utils\/period/)
  assert.doesNotMatch(trendReport, /\.slice\(0, 4\)/)
})

test('趋势报表查询参数用 snake_case，与后端 FastAPI 的 Query 名一致', () => {
  // FastAPI 不会自动把 camelCase 映射成 snake_case —— 写错就是参数被静默忽略
  assert.match(p2Api, /department_id: params\.departmentId/)
  assert.match(p2Api, /include_names: params\.includeNames/)
  assert.match(p2Api, /task_id: params\.taskId/)
})

test('风险清单加载失败时明确提示，而不是静默显示空表', () => {
  assert.match(trendReport, /rosterError/)
  assert.match(trendReport, /可能无查看权限/)
})

// ═══════════════════════════════════════════════════════════════════
// P2-2 薪酬导出
// ═══════════════════════════════════════════════════════════════════

test('薪酬导出默认不导出姓名，必须由 HR 显式勾选', () => {
  assert.match(payroll, /includeNames = ref\(false\)/)
  assert.match(payroll, /includeNames: includeNames\.value/)
})

test('薪酬导出把免责说明写进导出文件本身', () => {
  // 说明必须随文件走 —— 脱离系统上下文后仍然有效
  assert.match(payroll, /data\.disclaimer/)
  assert.match(payroll, /# \$\{data\.disclaimer\}/)
})

test('薪酬导出页面明示系数不构成调薪承诺', () => {
  assert.match(payroll, /不构成调薪承诺/)
})

test('薪酬导出把「缺分数」显式标出来，不让它混在正常行里', () => {
  // 缺分记录有确认等级（系数照样给）但算分没完成 —— 必须显眼
  assert.match(payroll, /unscoredCount/)
  assert.match(payroll, /scored === false/)
  assert.match(payroll, /未算分/)
  // 不能静默把缺分当 0（空单元格在 Excel 里像 0）
  assert.match(payroll, /row\.scored === false \? '未算分'/)
})

test('薪酬导出的 CSV 把算分状态写进文件本身', () => {
  // 表格是给人看的，文件是拿去干活的 —— 两边都得能分辨缺分
  assert.match(payroll, /算分状态/)
  assert.match(payroll, /缺分数/)
  assert.match(payroll, /已算分/)
})

// ═══════════════════════════════════════════════════════════════════
// P2-3 反馈闭环 / Prompt 灰度 / 案例库
// ═══════════════════════════════════════════════════════════════════

test('反馈面板明示反馈不影响绩效结论', () => {
  assert.match(feedback, /反馈不影响绩效结论/)
  assert.match(feedback, /不会修改分数或已确认等级/)
})

test('反馈面板在样本不足时不自己下结论', () => {
  // 后端 sufficient=false 时必须如实显示「样本不足」
  assert.match(feedback, /comparison\.sufficient/)
  assert.match(feedback, /comparison && !comparison\.sufficient/)
  assert.match(feedback, /comparison\.conclusion/)
})

test('反馈面板区分「反馈提交」与「业务写入」—— 只写反馈接口', () => {
  assert.match(feedback, /feedbackApi\.submit/)
  // 只允许写反馈接口；不得触碰评分/等级等业务写入路径
  assert.doesNotMatch(feedback, /manager-review/)
  const writes = feedback.match(/http\.(post|put|patch|delete)\(/g) ?? []
  assert.equal(writes.length, 0, '反馈面板不应直接调用业务写接口')
})

test('主管评分页的反馈只落反馈接口，不参与评分提交路径', () => {
  assert.match(managerReview, /feedbackApi\.submit/)
  // 评分提交仍然只带 confirmed_rating + manager_comment
  assert.match(managerReview, /confirmed_rating: reviewFormData\.grade/)
  assert.match(managerReview, /manager_comment: reviewFormData\.comment/)
})

test('案例库对全员可读，收录仅主管/HR', () => {
  // 复核中心：面板对所有人挂载，canPublish 由能力位决定
  assert.match(reviewCenter, /CaseLibraryPanel/)
  assert.match(reviewCenter, /caps\?\.canManage \|\| caps\?\.isHr/)
})

test('案例库明示已脱敏与参考性质', () => {
  assert.match(caseLibrary, /已脱敏/)
  assert.match(caseLibrary, /redacted/)
  assert.match(caseLibrary, /参考/)
})

test('案例收录对话框明示入库即脱敏', () => {
  assert.match(caseLibrary, /入库即脱敏/)
  assert.match(caseLibrary, /不含姓名与评语正文/)
})

// ═══════════════════════════════════════════════════════════════════
// P1-5 数据源白名单
// ═══════════════════════════════════════════════════════════════════

test('数据源白名单面板明示「默认不信任」的语义', () => {
  assert.match(dataSource, /默认不信任/)
  assert.match(dataSource, /未配置 = 不可读/)
})

test('数据源白名单把「进入算分集」与「谁可读」分成两个独立配置', () => {
  assert.match(dataSource, /进入算分集/)
  assert.match(dataSource, /可读角色/)
  assert.match(dataSource, /脱敏字段/)
})

test('数据源白名单与 Prompt 灰度挂在 AI 控制中心', () => {
  assert.match(aiManagement, /DataSourcePolicyPanel/)
  assert.match(aiManagement, /FeedbackPanel/)
})
