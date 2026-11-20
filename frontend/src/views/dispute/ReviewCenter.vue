<template>
  <section class="review-center" v-loading="loading">
    <div class="heading"><div><h1>绩效复核与校准中心</h1><p>申诉、证据核验、校准决议与调整审批统一追溯。</p></div><el-button @click="load">刷新</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-tabs v-model="tab">
      <el-tab-pane label="复核案件" name="cases">
        <el-button type="primary" @click="openCreate">发起申诉</el-button>
        <el-table :data="cases" stripe empty-text="暂无授权范围内的复核案件">
          <el-table-column prop="caseNo" label="编号" min-width="200" /><el-table-column prop="employeeName" label="员工" width="100" />
          <el-table-column prop="title" label="事项" min-width="200" /><el-table-column label="状态" width="140"><template #default="{ row }">{{ label(row.status) }}<el-tag v-if="row.overdue" type="danger">超期</el-tag></template></el-table-column>
          <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" @click="openCase(row.id)">查看处理</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="分数调整" name="adjustments">
        <el-table :data="adjustments" stripe><el-table-column prop="recordId" label="考核记录" min-width="180" /><el-table-column prop="reason" label="调整依据" min-width="220" /><el-table-column label="原分数" width="100"><template #default="{ row }">{{ row.before.score.total }}</template></el-table-column><el-table-column label="状态" width="140"><template #default="{ row }">{{ label(row.status) }}</template></el-table-column><el-table-column label="操作" width="150"><template #default="{ row }"><el-button link @click="showAdjustment(row)">明细</el-button><el-button v-if="caps?.canManage && row.status === 'PENDING' && row.requestedBy !== caps.userId" link type="primary" @click="openApproval(row)">审批</el-button></template></el-table-column></el-table>
      </el-tab-pane>
      <el-tab-pane v-if="caps?.canManage" label="校准会议" name="meetings">
        <el-button type="primary" @click="meetingDialog = true">创建会议</el-button>
        <el-table :data="meetings" stripe><el-table-column prop="title" label="会议" /><el-table-column prop="period" label="考核周期" /><el-table-column label="状态"><template #default="{ row }">{{ label(row.status) }}</template></el-table-column><el-table-column label="操作"><template #default="{ row }"><el-button link type="primary" @click="openMeeting(row.id)">会议材料与决议</el-button></template></el-table-column></el-table>
      </el-tab-pane>
      <el-tab-pane v-if="caps?.isHr" label="投递与同步" name="outbox"><el-button @click="loadOutbox">刷新状态</el-button><el-table :data="outbox"><el-table-column prop="topic" label="事件" /><el-table-column prop="status" label="状态" /><el-table-column prop="attempts" label="尝试次数" /><el-table-column prop="lastError" label="待处理问题" /></el-table></el-tab-pane>

      <!--
        P2-3 案例库。
        复核人在处理申诉时需要**参考先例**，但案例不能当依据 ——
        所以这个页签对所有人可读（can-publish 只给主管/HR），
        且案例本身已脱敏、标注为参考做法。
      -->
      <el-tab-pane label="案例库" name="cases-library">
        <CaseLibraryPanel :can-publish="Boolean(caps?.canManage || caps?.isHr)" title="历史处理案例（参考）" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="createDialog" title="发起申诉" width="660px"><el-form label-position="top"><el-form-item label="复核对象"><el-select v-model="create.target" class="full"><el-option v-for="o in options" :key="o.resultId" :label="o.label" :value="o.resultId" /></el-select></el-form-item><el-form-item label="标题"><el-input v-model="create.title" maxlength="200" /></el-form-item><el-form-item label="争议事项、依据及需要核查的内容"><el-input v-model="create.reason" type="textarea" :rows="6" maxlength="10000" /></el-form-item><p>提交后可追加 PDF、PNG、JPEG 证据；每个文件不超过10MB，最多20个。</p></el-form><template #footer><el-button @click="createDialog = false">取消</el-button><el-button type="primary" :loading="busy" @click="submitCase">提交申诉</el-button></template></el-dialog>

    <el-drawer v-model="caseDrawer" title="案件复核" size="85%">
      <template v-if="active">
        <h2>{{ active.title }}</h2><p>{{ active.caseNo }} · {{ active.employeeName }} · {{ label(active.status) }}</p><p class="reason">{{ active.reason }}</p>
        <div class="actions"><el-button @click="downloadPackage">导出证据包</el-button><el-button @click="checkIntegrity">校验完整性</el-button><el-button v-if="active.recordId" @click="loadVersions">评分版本</el-button><el-button v-if="active.isOwner && terminal(active.status)" @click="reopen">提交新证据申请再次复核</el-button></div>
        <el-tabs>
          <el-tab-pane label="材料与处理">
            <h3>证据附件</h3><p v-if="!active.evidence?.length">暂无补充附件。</p>
            <div v-for="a in active.evidence" :key="a.id" class="evidence"><el-button link type="primary" @click="downloadEvidence(a)">{{ a.name }}</el-button><small>SHA-256：{{ a.digest }}</small></div>
            <label v-if="active.isOwner && ['SUBMITTED', 'NEEDS_SUPPLEMENT'].includes(active.status)" class="upload">追加证据 <input type="file" accept=".pdf,.png,.jpg,.jpeg" :disabled="busy" @change="uploadEvidence" /></label>
            <h3>案件处理</h3>
            <el-form label-position="top"><el-form-item label="处理说明 / 复核意见"><el-input v-model="comment" type="textarea" :rows="4" maxlength="10000" /></el-form-item>
              <el-form-item v-if="active.canManage && ['ACCEPTED', 'REVIEWING'].includes(active.status)" label="独立评审人"><el-select v-model="reviewerId" filterable class="full"><el-option v-for="p in people" :key="p.id" :label="p.name" :value="p.id" /></el-select></el-form-item>
            </el-form>
            <div class="actions">
              <el-button v-if="active.canManage && active.status === 'SUBMITTED'" type="primary" :disabled="busy" @click="act('ACCEPT')">受理</el-button>
              <el-button v-if="active.canManage && active.status === 'SUBMITTED'" :disabled="busy" @click="act('NOT_ACCEPTED')">不予受理</el-button>
              <template v-if="active.canManage && ['ACCEPTED', 'REVIEWING'].includes(active.status)"><el-button :disabled="busy" @click="act('ASSIGN')">指派评审人</el-button><el-button :disabled="busy" @click="act('REQUEST_SUPPLEMENT')">退回补充</el-button></template>
              <el-button v-if="active.isOwner && active.status === 'NEEDS_SUPPLEMENT'" type="primary" :disabled="busy" @click="act('SUPPLEMENT')">补充完成</el-button>
              <el-button v-if="active.isOwner && !terminal(active.status)" :disabled="busy" @click="act('WITHDRAW')">撤回</el-button>
              <template v-if="isReviewer && active.status === 'REVIEWING'"><el-button :disabled="busy" @click="submitOpinion('SUPPORT')">提交支持意见</el-button><el-button :disabled="busy" @click="submitOpinion('REJECT')">提交驳回意见</el-button><el-button :disabled="busy" @click="submitOpinion('SUPPLEMENT')">建议补充</el-button><el-button :disabled="busy" @click="act('RECUSE')">申请回避</el-button></template>
              <template v-if="active.canManage && ['ACCEPTED', 'REVIEWING'].includes(active.status)"><el-button type="success" :disabled="busy" @click="act('SUPPORT')">支持申诉</el-button><el-button :disabled="busy" @click="act('PARTIAL')">部分支持</el-button><el-button :disabled="busy" @click="act('REJECT')">维持结果</el-button></template>
              <template v-if="active.canManage && active.status === 'DECIDED'"><el-button v-if="active.recordId && ['SUPPORT', 'PARTIAL'].includes(active.decision || '')" type="primary" @click="openAdjustment">申请分数调整</el-button><el-button :disabled="busy" @click="act('ARCHIVE')">归档</el-button></template>
            </div>
            <h3>复核意见</h3><div v-for="(o, i) in active.opinions" :key="i" class="card">{{ label(o.opinion) }} · 版本 {{ o.version }}<p>{{ o.comment }}</p></div>
            <h3 v-if="active.decision">裁决</h3><p>{{ active.decisionComment }}</p>
          </el-tab-pane>
          <el-tab-pane label="中立核验报告">
            <el-button v-if="(active.canManage || isReviewer) && ['ACCEPTED', 'REVIEWING'].includes(active.status)" type="primary" :loading="busy" @click="generateReport">生成冻结数据核验报告</el-button>
            <p>报告提供事实、差异和待核实事项；裁决由授权人员完成。</p>
            <VerificationReportPanel :reports="active.reports" />
          </el-tab-pane>
          <el-tab-pane label="操作时间线"><el-timeline><el-timeline-item v-for="e in active.events" :key="e.id" :timestamp="e.createdAt"><strong>{{ label(e.action) }}</strong> · {{ e.actorId }}<p>{{ e.payload.comment || e.payload.digest || '' }}</p><small>{{ e.digest }}</small></el-timeline-item></el-timeline></el-tab-pane>
          <el-tab-pane label="冻结快照"><pre>{{ pretty(active.snapshot) }}</pre></el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-dialog v-model="adjustDialog" title="申请分数调整" width="720px"><p>仅可修订冻结方案已有的人工评价项；总分由规则引擎重算。申请人与审批人必须不同。</p><el-form label-position="top"><el-form-item label="考核记录"><el-input v-model="adjust.record_id" :disabled="!!adjust.case_id" /></el-form-item><el-form-item label="当前评分版本"><el-input-number v-model="adjust.expected_version" :min="1" /></el-form-item><div v-for="f in adjustFields" :key="f.code" class="card"><h4>{{ f.name }}</h4><el-form-item label="调整后分项分数"><el-input-number v-model="f.score" :min="f.min" :max="f.max" :precision="2" /></el-form-item><el-form-item label="该项调整的证据与依据"><el-input v-model="f.comment" type="textarea" :rows="2" /></el-form-item></div><div v-for="f in correctionFields" :key="f.code" class="card"><h4>{{ f.name }} · 数据纠错</h4><el-form-item label="实际值"><el-input-number v-model="f.actual" :min="0" /></el-form-item><el-form-item label="目标值"><el-input-number v-model="f.target" :min="0.000001" /></el-form-item><el-form-item label="纠错证据与数据来源"><el-input v-model="f.comment" type="textarea" :rows="2" /></el-form-item></div><template v-if="gradeOptions.length"><el-form-item label="确认等级"><el-select v-model="adjustGrade"><el-option v-for="g in gradeOptions" :key="g" :label="g" :value="g" /></el-select></el-form-item><el-form-item label="等级判定依据"><el-input v-model="gradeComment" type="textarea" /></el-form-item></template><el-form-item label="调整依据"><el-input v-model="adjust.reason" type="textarea" :rows="4" /></el-form-item></el-form><template #footer><el-button type="primary" :loading="busy" @click="submitAdjustment">提交审批</el-button></template></el-dialog>
    <el-dialog v-model="approvalDialog" title="独立审批" width="720px"><template v-if="approvalRow"><p>{{ approvalRow.reason }}</p><h3>修改前</h3><pre>{{ pretty(approvalRow.before) }}</pre><h3>拟调整评价</h3><pre>{{ pretty(approvalRow.proposed) }}</pre><el-input v-model="approvalComment" type="textarea" :rows="4" placeholder="审批依据必填" /></template><template #footer><el-button :loading="busy" @click="approve(false)">驳回</el-button><el-button type="primary" :loading="busy" @click="approve(true)">批准并执行</el-button></template></el-dialog>
    <el-dialog v-model="infoDialog" :title="infoTitle" width="800px"><pre>{{ infoText }}</pre></el-dialog>

    <el-dialog v-model="meetingDialog" title="创建校准会议" width="640px"><el-form label-position="top"><el-form-item label="会议标题"><el-input v-model="meetingForm.title" /></el-form-item><el-form-item label="周期"><el-date-picker v-model="meetingForm.period" type="month" value-format="YYYYMM" /></el-form-item><el-form-item label="部门范围"><el-select v-model="meetingForm.departments" multiple class="full"><el-option v-for="d in caps?.departments" :key="d.id" :label="d.name" :value="d.id" /></el-select></el-form-item><el-form-item label="参会人（须有会议范围权限）"><el-select v-model="meetingForm.participants" multiple filterable class="full"><el-option v-for="p in people" :key="p.id" :label="p.name" :value="p.id" /></el-select></el-form-item></el-form><template #footer><el-button type="primary" :loading="busy" @click="createMeeting">创建并冻结材料</el-button></template></el-dialog>
    <el-drawer v-model="meetingDrawer" title="校准会议材料与决议" size="85%"><template v-if="meetingActive"><h2>{{ meetingActive.title }} · {{ label(meetingActive.status) }}</h2><p>异常仅触发核查，不强制分布；人数少于5人的组隐藏均值和中位数。</p><h3>岗位与规则分组分布</h3><el-table :data="meetingActive.snapshot?.distributions"><el-table-column prop="department_id" label="部门" /><el-table-column prop="position" label="岗位" /><el-table-column prop="count" label="样本数" /><el-table-column prop="average" label="均分" /><el-table-column prop="median" label="中位数" /><el-table-column prop="rule_group" label="规则组" /></el-table><h3>异常线索</h3><pre>{{ pretty(meetingActive.snapshot?.alerts) }}</pre><h3>评分记录</h3><el-table :data="meetingActive.snapshot?.records"><el-table-column prop="employee_name" label="员工" /><el-table-column prop="position" label="岗位" /><el-table-column prop="total" label="分数" /><el-table-column prop="rating" label="等级" /><el-table-column prop="record_id" label="记录编号" min-width="250" /></el-table><h3>会议决议</h3><div v-for="r in meetingActive.resolutions" :key="r.id" class="card"><strong>{{ label(r.action) }}</strong> · {{ r.recordId }}<p>{{ r.basis }}</p><p>责任人：{{ r.ownerId }} · 期限：{{ r.dueAt }}</p><el-button v-if="r.action === 'ADJUST' && meetingActive.status === 'CLOSED'" @click="openMeetingAdjustment(r.recordId || '')">申请调整</el-button></div><template v-if="meetingActive.status === 'OPEN'"><el-form label-position="top"><el-form-item label="关联评分记录"><el-select v-model="resolution.record_id" clearable class="full"><el-option v-for="r in meetingActive.snapshot?.records" :key="r.record_id" :label="`${r.employee_name} / ${r.total}分 / ${r.record_id}`" :value="r.record_id" /></el-select></el-form-item><el-form-item label="决议动作"><el-select v-model="resolution.action"><el-option label="维持结果" value="KEEP" /><el-option label="转复核案件" value="REVIEW" /><el-option label="同意提出调整申请" value="ADJUST" /><el-option label="后续制度优化" value="POLICY" /></el-select></el-form-item><el-form-item label="事实依据与后续动作"><el-input v-model="resolution.basis" type="textarea" :rows="4" /></el-form-item><el-form-item label="责任人"><el-select v-model="resolution.owner_id" filterable><el-option v-for="p in people" :key="p.id" :label="p.name" :value="p.id" /></el-select></el-form-item><el-form-item label="处理期限"><el-date-picker v-model="resolution.due_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" /></el-form-item></el-form><el-button type="primary" :loading="busy" @click="addResolution">保存决议</el-button><el-button :loading="busy" @click="closeMeeting">结束会议并确认决议</el-button></template></template></el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { reviewApi, type ReviewCase, type Adjustment, type Meeting } from '@/api/reviewCenter'
import CaseLibraryPanel from '@/components/CaseLibraryPanel.vue'
import VerificationReportPanel from '@/components/VerificationReportPanel.vue'
const loading = ref(false), busy = ref(false), error = ref(''), tab = ref('cases')
const caps = ref<Awaited<ReturnType<typeof reviewApi.capabilities>>>()
const cases = ref<ReviewCase[]>([]), adjustments = ref<Adjustment[]>([]), meetings = ref<Meeting[]>([])
const people = ref<Array<{id: string; name: string}>>([]), options = ref<Array<{resultId: string; label: string}>>([])
const outbox = ref<Awaited<ReturnType<typeof reviewApi.outbox>>>([])
const createDialog = ref(false), caseDrawer = ref(false), active = ref<ReviewCase>(), comment = ref(''), reviewerId = ref('')
const create = reactive({ target: '', title: '', reason: '', parent_id: null as string | null })
const correctionFields = ref<Array<{ code: string; name: string; actual: number; target: number; comment: string }>>([])
const adjustFields = ref<Array<{ code: string; name: string; min: number; max: number; score: number; comment: string }>>([]), gradeOptions = ref<string[]>([]), adjustGrade = ref(''), gradeComment = ref('')
const adjustDialog = ref(false), adjust = reactive({ case_id: null as string | null, meeting_id: null as string | null, record_id: '', expected_version: 1, reason: '' })
const approvalDialog = ref(false), approvalRow = ref<Adjustment>(), approvalComment = ref('')
const infoDialog = ref(false), infoTitle = ref(''), infoText = ref('')
const meetingDialog = ref(false), meetingDrawer = ref(false), meetingActive = ref<Meeting>()
const meetingForm = reactive({ title: '', period: '', departments: [] as string[], participants: [] as string[] })
const resolution = reactive({ record_id: '', action: 'KEEP', basis: '', owner_id: '', due_at: '' })
const isReviewer = computed(() => active.value?.reviewers?.some(r => r.userId === caps.value?.userId && r.status === 'NONE'))
const names: Record<string, string> = { SUBMITTED: '待受理', ACCEPTED: '已受理', REVIEWING: '评审中', NEEDS_SUPPLEMENT: '待补充', DECIDED: '已裁决', ARCHIVED: '已归档', WITHDRAWN: '已撤回', NOT_ACCEPTED: '不予受理', SUPPORT: '支持申诉', REJECT: '驳回', PARTIAL: '部分支持', PENDING: '待审批', EXECUTED: '已执行', REJECTED: '已驳回', OPEN: '会议进行中', CLOSED: '会议已结束', KEEP: '维持结果', REVIEW: '转复核', ADJUST: '申请调整', POLICY: '制度优化' }
const label = (s: string) => names[s] || s
const terminal = (s: string) => ['DECIDED', 'ARCHIVED', 'WITHDRAWN', 'NOT_ACCEPTED'].includes(s)
const pretty = (v: unknown) => JSON.stringify(v, null, 2)
async function run(fn: () => Promise<void>) { busy.value = true; try { await fn() } catch (e) { ElMessage.error(e instanceof Error ? e.message : '操作失败') } finally { busy.value = false } }
async function load() { loading.value = true; error.value = ''; try { caps.value = await reviewApi.capabilities(); const [c, a, m] = await Promise.all([reviewApi.cases(), reviewApi.adjustments(), reviewApi.meetings()]); cases.value = c.items; adjustments.value = a; meetings.value = m; if (caps.value.canManage) people.value = await reviewApi.people() } catch(e) { error.value = e instanceof Error ? e.message : '加载失败' } finally { loading.value = false } }
async function openCreate() { await run(async () => { options.value = await reviewApi.options(); Object.assign(create, { target: '', title: '', reason: '', parent_id: null }); createDialog.value = true }) }
async function submitCase() { await run(async () => { active.value = await reviewApi.create(create); createDialog.value = false; caseDrawer.value = true; await load(); ElMessage.success('申诉已提交，可继续追加证据') }) }
async function openCase(id: string) { await run(async () => { active.value = await reviewApi.detail(id); comment.value = ''; caseDrawer.value = true }) }
async function refreshCase() { if(active.value) active.value = await reviewApi.detail(active.value.id); await load() }
async function act(action: string) { if(!active.value) return; await run(async () => { const row = await reviewApi.action(active.value!.id, { revision: active.value!.revision, action, comment: comment.value, reviewer_id: reviewerId.value || null }); if(action === 'RECUSE') { active.value = undefined; caseDrawer.value = false; await load() } else { active.value = row; await load() }; comment.value = ''; ElMessage.success('处理已保存') }) }
async function submitOpinion(value: string) { await run(async () => { active.value = await reviewApi.opinion(active.value!.id, { revision: active.value!.revision, value, comment: comment.value }); comment.value = '' }) }
async function uploadEvidence(event: Event) { const input = event.target as HTMLInputElement; const file = input.files?.[0]; if(!file || !active.value) return; await run(async () => { const body = new FormData(); body.append('file', file); body.append('revision', String(active.value!.revision)); await reviewApi.upload(active.value!.id, body); await refreshCase(); ElMessage.success('证据已追加') }); input.value = '' }
function save(blob: Blob, name: string) { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url) }
async function downloadPackage() { await run(async () => save(await reviewApi.package(active.value!.id), `${active.value!.caseNo}.zip`)) }
async function downloadEvidence(a: { id: string; name: string }) { await run(async () => save(await reviewApi.evidence(active.value!.id, a.id), a.name)) }
async function generateReport() { await run(async () => { await reviewApi.report(active.value!.id, active.value!.revision); await refreshCase() }) }
async function checkIntegrity() { await run(async () => { const result = await reviewApi.integrity(active.value!.id); showInfo('完整性校验', result) }) }
function showInfo(title: string, content: unknown) { infoTitle.value = title; infoText.value = pretty(content); infoDialog.value = true }
async function loadVersions() { await run(async () => showInfo('评分版本与调整来源', await reviewApi.versions(active.value!.recordId!))) }
async function reopen() { try { const result = await ElMessageBox.prompt('说明新证据及再次复核理由', '再次复核', { inputType: 'textarea', inputValidator: v => !!v?.trim() || '请填写理由' }); Object.assign(create, { target: active.value!.recordId ? `score:${active.value!.recordId}` : active.value!.snapshot?.result_id, title: `再次复核：${active.value!.title}`, reason: result.value, parent_id: active.value!.id }); await submitCase() } catch { /* 用户取消 */ } }
async function initAdjustment(recordId: string) { const ctx = await reviewApi.adjustmentContext(recordId); adjust.expected_version = ctx.version; adjustFields.value = ctx.fields; correctionFields.value = ctx.quantitative; gradeOptions.value = ctx.grade_options; adjustGrade.value = ctx.grade || ''; gradeComment.value = ''; adjustDialog.value = true }
async function openAdjustment() { await run(async () => { Object.assign(adjust, { case_id: active.value!.id, meeting_id: null, record_id: active.value!.recordId, reason: active.value!.decisionComment || '' }); await initAdjustment(adjust.record_id) }) }
async function openMeetingAdjustment(recordId: string) { await run(async () => { Object.assign(adjust, { case_id: null, meeting_id: meetingActive.value!.id, record_id: recordId, reason: '' }); await initAdjustment(recordId) }) }
async function submitAdjustment() { await run(async () => { const proposed: Record<string, unknown> = {}; for (const field of adjustFields.value) { if(field.comment.trim()) proposed[field.code] = { score: field.score, comment: field.comment } }; if(gradeOptions.value.length && gradeComment.value.trim()) proposed._grade = { level: adjustGrade.value, comment: gradeComment.value }; const corrections: Record<string, unknown> = {}; for(const field of correctionFields.value) { if(field.comment.trim()) corrections[field.code] = { actual: field.actual, target: field.target, comment: field.comment } }; if(!Object.keys(proposed).length && !Object.keys(corrections).length) throw new Error('请为需要调整或纠错的项目填写依据'); await reviewApi.requestAdjustment({ ...adjust, proposed, corrections }); adjustDialog.value = false; await load(); ElMessage.success('已提交调整审批') }) }
function showAdjustment(row: Adjustment) { showInfo('调整单明细', row) }
function openApproval(row: Adjustment) { approvalRow.value = row; approvalComment.value = ''; approvalDialog.value = true }
async function approve(approve: boolean) { await run(async () => { await reviewApi.approve(approvalRow.value!.id, { revision: approvalRow.value!.revision, approve, comment: approvalComment.value }); approvalDialog.value = false; await load(); ElMessage.success(approve ? '调整已审批并执行' : '已驳回') }) }
async function createMeeting() { await run(async () => { const row = await reviewApi.createMeeting(meetingForm); meetingDialog.value = false; await load(); await openMeeting(row.id) }) }
async function openMeeting(id: string) { meetingActive.value = await reviewApi.meeting(id); meetingDrawer.value = true }
async function addResolution() { await run(async () => { await reviewApi.resolution(meetingActive.value!.id, { ...resolution, record_id: resolution.record_id || null, revision: meetingActive.value!.revision }); await openMeeting(meetingActive.value!.id); resolution.basis = '' }) }
async function closeMeeting() { await run(async () => { await reviewApi.closeMeeting(meetingActive.value!.id, meetingActive.value!.revision); await openMeeting(meetingActive.value!.id); await load() }) }
async function loadOutbox() { await run(async () => { outbox.value = await reviewApi.outbox() }) }
onMounted(load)
</script>

<style scoped>
.review-center { padding: 24px; } .heading { display: flex; justify-content: space-between; align-items: center; } h1 { margin: 0; font-size: 24px; } p { line-height: 1.7; } .full { width: 100%; } .actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0; } .actions :deep(.el-button + .el-button) { margin-left: 0; } .card { border: 1px solid var(--el-border-color); padding: 16px; border-radius: 8px; margin: 12px 0; } pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--el-fill-color-light); padding: 12px; border-radius: 6px; max-height: 500px; overflow: auto; } small { display: block; overflow-wrap: anywhere; color: var(--el-text-color-secondary); } .evidence { margin: 12px 0; } .upload { display: block; margin: 16px 0; } .reason { white-space: pre-wrap; }
@media (max-width: 700px) { .review-center { padding: 12px; } }
</style>
