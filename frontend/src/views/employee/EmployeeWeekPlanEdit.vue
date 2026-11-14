<template>
  <section class="page-panel week-plan-workbench">
    <div v-if="isLeaderWorkspace" class="page-header week-editor-header">
      <div>
        <h1 class="page-title">{{ form.weekStart ? `${form.weekStart} 至 ${weekEnd}` : '--' }} 周计划编制</h1>
        <p class="page-subtitle">第 {{ weekNumber }} 周 · 本页只编制工作内容，审批通过后再到成果提交填写完成情况</p>
      </div>
      <div class="toolbar">
        <el-tag v-if="detailStatus" :type="weekPlanStatusMeta[detailStatus].type">{{ weekPlanStatusMeta[detailStatus].label }}</el-tag>
        <el-button :icon="Clock" @click="versionDrawerVisible = true">历史版本</el-button>
        <el-button :icon="ChatLineSquare" @click="assistDrawerVisible = true">辅助工具</el-button>
        <el-button v-if="detailStatus === 'PENDING'" type="warning" plain :loading="withdrawing" @click="withdrawPlan">撤回计划</el-button>
        <el-button :icon="ArrowLeft" @click="backToList">返回列表</el-button>
      </div>
    </div>

    <el-alert v-if="errorMessage" class="dashboard-alert" type="warning" :closable="false" show-icon :title="errorMessage" />
    <el-alert v-if="restoredFromVersionId" class="dashboard-alert" type="warning" :closable="false" show-icon :title="`历史版本 V${restoredVersionNo} 已恢复到当前计划，尚未保存`" />
    <div v-loading="loading" class="plan-editor-layout mt16">
      <main class="plan-editor-main">
        <PlanFeedbackBanner v-if="approvalComment" :status="detailStatus" :comment="approvalComment" next-step="根据意见修改计划并重新提交" />
        <div v-if="!isLeaderWorkspace" class="toolbar week-editor-actions">
          <el-tag v-if="detailStatus" :type="weekPlanStatusMeta[detailStatus].type">{{ weekPlanStatusMeta[detailStatus].label }}</el-tag>
          <el-button :icon="Clock" @click="versionDrawerVisible = true">历史版本</el-button>
          <el-button :icon="ChatLineSquare" @click="assistDrawerVisible = true">辅助工具</el-button>
          <el-button v-if="detailStatus === 'PENDING'" type="warning" plain :loading="withdrawing" @click="withdrawPlan">撤回计划</el-button>
          <el-button :icon="ArrowLeft" @click="backToList">返回列表</el-button>
        </div>

        <section class="week-plan-identity">
          <div class="week-plan-identity__copy">
            <span class="section-kicker">计划基础信息</span>
            <strong>{{ form.weekStart ? `${form.weekStart} 至 ${weekEnd}` : '请选择自然周' }}</strong>
            <p>{{ isEdit ? '已有计划的周期保持不变。' : '周期已从周计划入口带入，无需手动选择。' }}</p>
          </div>
          <div class="week-plan-identity__meta">
            <div class="week-number"><span>第</span><b>{{ weekNumber }}</b><span>周</span></div>
            <span><strong>{{ workPointCount }}</strong> 个工作要点</span>
            <span><strong>1</strong> 份周计划</span>
          </div>
        </section>

        <section class="week-task-stage">
          <div class="week-task-toolbar">
            <div><span class="section-kicker">计划编制</span><h2>本周计划 <small>{{ workPointCount }} 个工作要点</small></h2><p>所有工作统一写在一份周计划中，按 1、2、3 分点即可。</p></div>
            <div class="week-task-actions">
              <el-button v-if="editable && hasDraftContent" class="week-clear-button" :icon="Delete" :disabled="loading || saving || submitting || aiChecking || clearing" @click="clearDraft">一键清空</el-button>
              <el-radio-group v-model="taskViewMode" class="week-task-view-switch" aria-label="周任务编辑视图" @change="changeTaskViewMode">
                <el-radio-button value="table"><el-icon><Grid /></el-icon><span>表格</span></el-radio-button>
                <el-radio-button value="cards"><el-icon><Tickets /></el-icon><span>任务卡</span></el-radio-button>
              </el-radio-group>
            </div>
          </div>

          <div v-if="taskViewMode === 'table'" class="compact-week-task-table" role="table" aria-label="周计划表">
            <div class="compact-week-task-table__head" role="row">
              <span>姓名</span><span>工作内容</span><span>考核标准（交付物、质量）</span><span>统计周期</span>
            </div>
            <article class="compact-week-task-row" role="row">
              <div class="compact-week-task-cell compact-week-task-cell--name" role="cell"><span class="compact-week-field-label">姓名</span><strong>{{ authStore.user?.realName || '当前员工' }}</strong></div>
              <div id="week-item-0-content" class="compact-week-task-cell compact-week-task-cell--content" :class="{ 'has-error': fieldErrors().content }" role="cell">
                <span class="compact-week-field-label">工作内容</span>
                <el-input v-model="weekItem.content" type="textarea" :rows="12" maxlength="20000" placeholder="1. 第一项工作&#10;2. 第二项工作&#10;3. 第三项工作" :disabled="!editable || saving || submitting || aiChecking || clearing" />
              </div>
              <div class="compact-week-task-cell compact-week-task-cell--standard" role="cell">
                <label id="week-item-0-deliverable" :class="{ 'has-error': fieldErrors().deliverable }">
                  <span class="compact-week-field-label">交付物</span>
                  <el-input v-model="weekItem.deliverable" type="textarea" :autosize="{ minRows: 2 }" maxlength="500" placeholder="填写交付物" :disabled="!editable || saving || submitting || aiChecking || clearing" />
                </label>
                <label id="week-item-0-quality" :class="{ 'has-error': fieldErrors().quality }">
                  <span class="compact-week-field-label">质量</span>
                  <el-input v-model="weekItem.quality" type="textarea" :autosize="{ minRows: 2 }" maxlength="500" placeholder="填写质量标准" :disabled="!editable || saving || submitting || aiChecking || clearing" />
                </label>
              </div>
              <div class="compact-week-task-cell compact-week-task-cell--period" role="cell"><span class="compact-week-field-label">统计周期</span><span>{{ form.weekStart }}<br>至 {{ weekEnd }}</span></div>
            </article>
          </div>

          <div v-else class="week-task-editor-list">
            <WeekPlanTaskEditorCard
              v-model="form.items[0]"
              :week-start="form.weekStart"
              :week-end="weekEnd"
              :expanded="weekCardExpanded"
              :disabled="!editable || saving || submitting || aiChecking || clearing"
              :field-errors="fieldErrors()"
              @toggle="weekCardExpanded = !weekCardExpanded"
            />
          </div>
        </section>
      </main>
    </div>

    <aside class="week-validation-bar" aria-label="周计划提交操作">
      <div class="week-save-state" aria-live="polite"><span :class="{ 'is-saving': saving, 'is-error': !!saveError, 'is-dirty': dirty }"></span><div><strong>{{ saveError ? '保存失败' : saving ? '正在保存' : dirty ? '有未保存修改' : '当前内容已保存' }}</strong><small>{{ saveError || '提交前将再次核对' }}</small></div></div>
      <div class="week-submit-readiness"><div><span>提交准备度</span><strong>{{ readiness }}%</strong><button v-if="visibleIssues.length" type="button" @click="assistDrawerVisible = true">{{ visibleIssues.length }} 项待处理</button><el-tag v-else-if="validationFeedbackVisible" size="small" type="success">可以提交</el-tag></div><el-progress :percentage="readiness" :show-text="false" :stroke-width="7" :status="visibleBlockingIssues.length ? 'warning' : 'success'" /></div>
      <PlanAiReviewCell class="week-submit-metrics" :review="aiReview" :loading="aiChecking" :stale="aiReviewStale" show-status @view="aiReviewDrawerVisible = true" />
      <div class="week-validation-actions">
        <el-button class="week-save-button" :icon="DocumentChecked" :loading="saving" :disabled="!editable || saving || submitting || clearing" @click="saveDraft">保存草稿</el-button>
        <el-button plain type="primary" :loading="aiChecking" :disabled="!editable || saving || submitting || clearing" @click="checkWeekPlanNow">AI检查</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!editable || saving || aiChecking || clearing" @click="submitPlan">提交审批</el-button>
      </div>
    </aside>

    <el-drawer v-model="assistDrawerVisible" title="辅助工具" size="min(460px, 92vw)" append-to-body>
      <div class="week-assist-stack">
        <AiReviewPanel :review="aiReview" :stale="aiReviewStale" title="AI周计划检查" empty-text="AI会逐项对照父级月计划，检查拆解关系、重复内容和一周内的可执行性。" compact>
          <template #actions><el-button v-if="aiChecking" type="danger" plain @click="stopAiCheck">停止生成</el-button><el-button v-else type="primary" plain :disabled="loading || saving || submitting" @click="checkWeekPlanNow">{{ aiReview ? '重新AI检查' : '立即AI检查' }}</el-button></template>
        </AiReviewPanel>
        <section class="week-assist-card"><div class="week-assist-card__head"><strong>提交前检查</strong><el-tag size="small" :type="validationFeedbackVisible ? (visibleIssues.length ? 'danger' : 'success') : 'info'">{{ validationFeedbackVisible ? (visibleIssues.length ? `${visibleIssues.length} 项待处理` : '可以提交') : '尚未检查' }}</el-tag></div><div v-if="visibleIssues.length" class="week-issue-list"><button v-for="issue in visibleIssues" :key="issue.key" type="button" :class="{ 'is-advisory': !issue.blocking }" @click="locateIssue(issue)"><span>{{ issue.blocking ? '!' : 'i' }}</span><b>{{ issue.label }}</b></button></div><p v-else>{{ validationFeedbackVisible ? '周计划内容已通过提交前检查。' : '点击提交审批后，系统会定位需要补充的字段。' }}</p></section>
      </div>
    </el-drawer>

    <PlanAiReviewDrawer
      v-model="aiReviewDrawerVisible"
      plan-type="WEEK"
      :review="aiReview"
      :loading="aiChecking"
      :stale="aiReviewStale"
      :subject="`${form.weekStart || '--'} 至 ${weekEnd || '--'} 周计划`"
      :context="authStore.user?.realName || '当前员工'"
    />

    <el-tooltip content="打开 AI 周计划助手" placement="left">
      <button
        class="week-ai-floating-entry"
        type="button"
        aria-label="打开 AI 周计划助手"
        :disabled="loading || saving || submitting || aiChecking"
        @click="aiAssistantVisible = true"
      >
        <el-icon><ChatLineSquare /></el-icon><span>AI 助手</span>
      </button>
    </el-tooltip>

    <el-drawer
      v-model="aiAssistantVisible"
      class="week-ai-drawer"
      title="AI 周计划助手"
      size="min(720px, 96vw)"
      append-to-body
    >
      <PlanAiAssistant
        v-if="form.weekStart"
        mode="week"
        :date="form.weekStart"
        :form="{ items: form.items.map((item) => ({ monthPlanItemId: item.monthPlanItemId, content: item.content, deliverable: item.deliverable, quality: item.quality, plannedFinishDate: weekEnd })) }"
        @version-created="latestDraftVersionNo = $event"
        @apply="applyAiDraft"
      />
    </el-drawer>

    <PlanDraftVersionDrawer v-model="versionDrawerVisible" plan-type="WEEK" :period-key="form.weekStart" :current-snapshot="currentVersionSnapshot" :editable="editable" @restore="restoreDraftVersion" @loaded="latestDraftVersionNo = $event" />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { createEmployeeWeekPlanApi, deleteEmployeeWeekPlanApi, getEmployeeWeekPlanApi, listEmployeeWeekPlansApi, submitEmployeeWeekPlanApi, updateEmployeeWeekPlanApi, weekPlanStatusMeta, withdrawEmployeeWeekPlanApi, type WeekPlanSavePayload, type WeekPlanStatus } from '@/api/weekPlan'
import { checkPlanAiReviewApi, getLatestAiReviewApi, type AiReview } from '@/api/aiReview'
import AiReviewPanel from '@/components/AiReviewPanel.vue'
import PlanAiReviewCell from '@/components/PlanAiReviewCell.vue'
import PlanAiReviewDrawer from '@/components/PlanAiReviewDrawer.vue'
import { notifyAiReviewResult } from '@/utils/aiReviewFeedback'
import PlanFeedbackBanner from './components/PlanFeedbackBanner.vue'
import type { PlanValidationIssue } from './components/PlanValidationAside.vue'
import WeekPlanTaskEditorCard from './components/WeekPlanTaskEditorCard.vue'
import PlanAiAssistant from './components/PlanAiAssistant.vue'
import type { DayPlanAiForm, WeekPlanAiItem } from '@/api/employeeAi'
import { useSmartBack } from '@/composables/useSmartBack'
import { useCancelableAiRequest } from '@/composables/useCancelableAiRequest'
import PlanDraftVersionDrawer from './components/PlanDraftVersionDrawer.vue'
import { listPlanDraftVersionsApi, type PlanDraftVersionSummary } from '@/api/planDraftVersions'
import { aiRequestId } from '@/api/employeeAi'
import { ArrowLeft, ChatLineSquare, Clock, Delete, DocumentChecked, Grid, Tickets } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { countWeekWorkPoints, mergeWeekTextField, mergeWeekWorkContent } from '@/utils/weekPlanDisplay'

type TaskViewMode = 'table' | 'cards'

const route = useRoute()
const router = useRouter()
const isLeaderWorkspace = route.path.startsWith('/leader/')
const weekPlanBasePath = isLeaderWorkspace ? '/leader/my-week-plans' : '/employee/week-plans'
const approvalTargetLabel = isLeaderWorkspace ? '部门负责人' : '直属领导'
const authStore = useAuthStore()
const { goBack } = useSmartBack(weekPlanBasePath)
const initialRouteId = Number(route.params.id || 0)
const currentPlanId = ref(Number.isInteger(initialRouteId) && initialRouteId > 0 ? initialRouteId : 0)
const isEdit = computed(() => currentPlanId.value > 0)
const initialWeekStart = routeWeekStart() || currentMonday()
const loading = ref(false)
const saving = ref(false)
const submitting = ref(false)
const withdrawing = ref(false)
const clearing = ref(false)
const errorMessage = ref('')
const saveError = ref('')
const aiAssistantVisible = ref(false)
const assistDrawerVisible = ref(false)
const validationFeedbackVisible = ref(false)
const aiReview = ref<AiReview | null>(null)
const aiReviewDrawerVisible = ref(false)
const aiReviewStale = ref(false)
const { running: aiChecking, run: runAiCheck, stop: stopAiCheckRequest } = useCancelableAiRequest()
const detailStatus = ref<WeekPlanStatus>('DRAFT')
const approvalComment = ref('')
const versionDrawerVisible = ref(false)
const latestDraftVersionNo = ref<number>()
const restoredFromVersionId = ref<number>()
const restoredVersionNo = ref<number>()
const savedSnapshot = ref('')
const allowRouteLeave = ref(false)
const weekCardExpanded = ref(false)
const taskViewMode = ref<TaskViewMode>(readInitialTaskViewMode())
const form = reactive<WeekPlanSavePayload>({ weekStart: initialWeekStart, versionNo: undefined, items: [emptyItem()] })
const editable = computed(() => detailStatus.value === 'DRAFT' || detailStatus.value === 'REJECTED')
const currentVersionSnapshot = computed<Record<string, unknown>>(() => normalizePayload(form))

const weekEnd = computed(() => form.weekStart ? addDays(form.weekStart, 6) : '')
const weekNumber = computed(() => form.weekStart ? getWeekNumber(form.weekStart) : '--')
const weekItem = computed(() => form.items[0] || emptyItem())
const workPointCount = computed(() => countWeekWorkPoints(weekItem.value.content))
const hasDraftContent = computed(() => Boolean(weekItem.value.content.trim() || weekItem.value.deliverable?.trim() || weekItem.value.quality?.trim() || weekItem.value.monthPlanItemId))
const emptySnapshot = JSON.stringify(normalizePayload({ weekStart: initialWeekStart, items: [emptyItem()] }))
const dirty = computed(() => savedSnapshot.value
  ? normalizeForm() !== savedSnapshot.value
  : !isEdit.value && normalizeForm() !== emptySnapshot)
const issues = computed<PlanValidationIssue[]>(() => {
  const result: PlanValidationIssue[] = []
  if (!form.weekStart) result.push({ key: 'week-start', label: '请选择自然周', targetId: 'week-plan-start', blocking: true })
  const item = weekItem.value
  if (!item.content.trim()) result.push({ key: 'content', label: '尚未填写本周工作内容', targetId: 'week-item-0-content', blocking: true })
  if (!item.deliverable?.trim()) result.push({ key: 'deliverable', label: '尚未填写交付物', targetId: 'week-item-0-deliverable', blocking: true })
  if (!item.quality?.trim()) result.push({ key: 'quality', label: '尚未填写质量标准', targetId: 'week-item-0-quality', blocking: true })
  return result
})
const visibleIssues = computed(() => validationFeedbackVisible.value ? issues.value : [])
const visibleBlockingIssues = computed(() => visibleIssues.value.filter((issue) => issue.blocking))
const readiness = computed(() => {
  if (!validationFeedbackVisible.value) return 100
  if (!issues.value.length) return 100
  return Math.max(0, Math.round(((issues.value.length - visibleBlockingIssues.value.length) / issues.value.length) * 100))
})
function emptyItem(): WeekPlanSavePayload['items'][number] { return { monthPlanItemId: null, content: '', deliverable: '', quality: '', plannedFinishDate: addDays(initialWeekStart, 6) } }
function readInitialTaskViewMode(): TaskViewMode {
  try {
    const stored = window.localStorage.getItem('week-plan-task-view')
    if (stored === 'table' || stored === 'cards') return stored
    return window.matchMedia('(max-width: 980px)').matches ? 'cards' : 'table'
  } catch {
    return 'table'
  }
}
function normalizePayload(payload: Pick<WeekPlanSavePayload, 'weekStart' | 'items'>) { const item = mergeSaveItems(payload.items, addDays(payload.weekStart, 6)); return { weekStart: payload.weekStart, items: [{ monthPlanItemId: item.monthPlanItemId, content: item.content.trim(), deliverable: item.deliverable?.trim() || '', quality: item.quality?.trim() || '', plannedFinishDate: item.plannedFinishDate || addDays(payload.weekStart, 6) }] } }
function normalizeForm() { return JSON.stringify(normalizePayload(form)) }
function addDays(value: string, days: number) { const date = new Date(`${value}T00:00:00`); date.setDate(date.getDate() + days); return dateValue(date) }
function dateValue(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` }
function routeWeekStart() {
  const value = Array.isArray(route.query.weekStart) ? route.query.weekStart[0] : route.query.weekStart
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && isMonday(value) && value >= currentMonday() ? value : ''
}
function currentMonday() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(new Date()).map((part) => [part.type, part.value]))
  const date = new Date(`${parts.year}-${parts.month}-${parts.day}T00:00:00Z`)
  const day = date.getUTCDay() || 7
  date.setUTCDate(date.getUTCDate() - day + 1)
  return date.toISOString().slice(0, 10)
}
function isMonday(value: string) { return new Date(`${value}T00:00:00`).getDay() === 1 }
function getWeekNumber(value: string) { const date = new Date(`${value}T00:00:00`); const first = new Date(date.getFullYear(), 0, 1); return String(Math.ceil((((date.getTime() - first.getTime()) / 86400000) + first.getDay() + 1) / 7)).padStart(2, '0') }
function fieldErrors() { return { content: visibleIssues.value.find((issue) => issue.targetId === 'week-item-0-content')?.label, deliverable: visibleIssues.value.find((issue) => issue.targetId === 'week-item-0-deliverable')?.label, quality: visibleIssues.value.find((issue) => issue.targetId === 'week-item-0-quality')?.label } }
function changeTaskViewMode(value: string | number | boolean | undefined) { const mode: TaskViewMode = value === 'cards' ? 'cards' : 'table'; taskViewMode.value = mode; try { window.localStorage.setItem('week-plan-task-view', mode) } catch { /* Keep the current-session choice when storage is unavailable. */ } }
function mergeSaveItems(items: WeekPlanSavePayload['items'], finishDate = weekEnd.value) {
  const safeItems = items.length ? items : [emptyItem()]
  const parentId = safeItems.length === 1 ? safeItems[0].monthPlanItemId : null
  return { monthPlanItemId: parentId && parentId > 0 ? parentId : null, content: mergeWeekWorkContent(safeItems.map((item) => item.content)), deliverable: mergeWeekTextField(safeItems.map((item) => item.deliverable)), quality: mergeWeekTextField(safeItems.map((item) => item.quality)), plannedFinishDate: finishDate }
}
function applyAiDraft(value: WeekPlanAiItem[] | DayPlanAiForm) { if (!Array.isArray(value)) return; const previous = weekItem.value; const deliverable = mergeWeekTextField(value.map((item) => item.deliverable)); const quality = mergeWeekTextField(value.map((item) => item.quality)); form.items = [{ monthPlanItemId: value.length === 1 ? (value[0].monthPlanItemId ?? previous.monthPlanItemId ?? null) : null, content: mergeWeekWorkContent(value.map((item) => item.content)), deliverable: deliverable || previous.deliverable || '', quality: quality || previous.quality || '', plannedFinishDate: weekEnd.value }]; aiAssistantVisible.value = false }
async function locateIssue(issue: PlanValidationIssue) { if (!issue.targetId) return; assistDrawerVisible.value = false; if (taskViewMode.value === 'cards') weekCardExpanded.value = true; await nextTick(); const target = document.getElementById(issue.targetId); target?.scrollIntoView({ behavior: 'smooth', block: 'center' }); setTimeout(() => (target?.querySelector('input, textarea, button, [tabindex]') as HTMLElement | null)?.focus(), 280) }
function buildPayload(recordVersion = false): WeekPlanSavePayload { const item = mergeSaveItems(form.items); return { weekStart: form.weekStart, versionNo: form.versionNo, items: [{ monthPlanItemId: item.monthPlanItemId, content: item.content.trim(), deliverable: item.deliverable?.trim() || '', quality: item.quality?.trim() || '', plannedFinishDate: weekEnd.value }], recordVersion, versionRequestId: recordVersion ? aiRequestId() : undefined, restoredFromVersionId: recordVersion ? restoredFromVersionId.value : undefined } }
async function persistDraft(recordVersion = false) {
  const creating = !isEdit.value
  const saved = creating
    ? await createEmployeeWeekPlanApi(buildPayload(recordVersion))
    : await updateEmployeeWeekPlanApi(currentPlanId.value, buildPayload(recordVersion))
  form.versionNo = saved.summary.versionNo
  detailStatus.value = saved.summary.status
  currentPlanId.value = saved.summary.id
  savedSnapshot.value = normalizeForm()
  if (recordVersion) { restoredFromVersionId.value = undefined; restoredVersionNo.value = undefined; await refreshLatestDraftVersion().catch(() => undefined) }
  if (creating) {
    allowRouteLeave.value = true
    try { await router.replace(`${weekPlanBasePath}/${saved.summary.id}/edit`) }
    finally { allowRouteLeave.value = false }
  }
  return saved
}
async function saveDraft() { if (saving.value) return false; saving.value = true; saveError.value = ''; errorMessage.value = ''; try { await persistDraft(true); ElMessage.success('周计划草稿已保存并建立版本'); return true } catch (error) { saveError.value = error instanceof Error ? error.message : '保存草稿失败，请稍后重试'; return false } finally { saving.value = false } }
async function clearDraft() {
  if (!editable.value || clearing.value || !hasDraftContent.value) return
  try {
    await ElMessageBox.confirm('确认清空当前周计划草稿？已有草稿记录及工作内容将一并清除。', '一键清空草稿', {
      type: 'warning',
      confirmButtonText: '清空草稿',
      cancelButtonText: '取消',
    })
  } catch { return }

  clearing.value = true
  saveError.value = ''
  errorMessage.value = ''
  const weekStart = form.weekStart
  try {
    if (isEdit.value) {
      if (!form.versionNo) throw new Error('周计划版本信息缺失，请刷新页面后重试')
      await deleteEmployeeWeekPlanApi(currentPlanId.value, form.versionNo)
    }
    currentPlanId.value = 0
    form.versionNo = undefined
    form.items = [{ monthPlanItemId: null, content: '', deliverable: '', quality: '', plannedFinishDate: addDays(weekStart, 6) }]
    detailStatus.value = 'DRAFT'
    approvalComment.value = ''
    aiReview.value = null
    aiReviewStale.value = false
    restoredFromVersionId.value = undefined
    restoredVersionNo.value = undefined
    validationFeedbackVisible.value = false
    savedSnapshot.value = normalizeForm()
    allowRouteLeave.value = true
    await router.replace({ path: `${weekPlanBasePath}/new/edit`, query: { weekStart } })
    ElMessage.success('周计划草稿已清空')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清空周计划草稿失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  } finally {
    allowRouteLeave.value = false
    clearing.value = false
  }
}
async function submitPlan() {
  if (restoredFromVersionId.value) { ElMessage.warning('请先保存已恢复的历史版本'); return }
  validationFeedbackVisible.value = true
  const blocking = issues.value.find((issue) => issue.blocking)
  if (blocking) { await locateIssue(blocking); return }
  try {
    await ElMessageBox.confirm('确认提交当前周计划审批？提交后将进入审批流程。', '提交周计划', {
      type: 'warning',
      confirmButtonText: '提交审批',
      cancelButtonText: '取消',
    })
  } catch { return }
  submitting.value = true
  saveError.value = ''
  errorMessage.value = ''
  try {
    const saved = await persistDraft(false)
    await submitEmployeeWeekPlanApi(saved.summary.id, saved.summary.versionNo)
    savedSnapshot.value = normalizeForm()
    ElMessage.success(`周计划已提交${approvalTargetLabel}审批`)
    allowRouteLeave.value = true
    await router.push(weekPlanBasePath)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '周计划提交失败，请稍后重试'
  } finally {
    allowRouteLeave.value = false
    submitting.value = false
  }
}
async function checkWeekPlanNow() { if (restoredFromVersionId.value) { ElMessage.warning('请先保存已恢复的历史版本'); return } const blocking = issues.value.find((issue) => issue.blocking); if (blocking) { await locateIssue(blocking); return } if (aiChecking.value || saving.value || submitting.value) return; saveError.value = ''; errorMessage.value = ''; try { const saved = await persistDraft(false); const result = await runAiCheck(({ requestId, signal }) => checkPlanAiReviewApi('WEEK_PLAN', saved.summary.id, { requestId, signal })); if (!result) return; aiReview.value = result; aiReviewStale.value = false; assistDrawerVisible.value = false; aiReviewDrawerVisible.value = true; notifyAiReviewResult(aiReview.value, 'AI语义检查已完成，逐维度报告已显示在当前页面') } catch (error) { errorMessage.value = error instanceof Error ? error.message : 'AI检查失败，请稍后重试' } }
async function stopAiCheck() { const confirmed = await stopAiCheckRequest(); confirmed ? ElMessage.info('已停止本次生成') : ElMessage.warning('界面已停止等待，服务端取消未确认') }

function restoreDraftVersion(snapshot: Record<string, unknown>, version: PlanDraftVersionSummary) { const items = Array.isArray(snapshot.items) ? snapshot.items.map((raw) => { const item = raw as Partial<WeekPlanSavePayload['items'][number]>; return { monthPlanItemId: item.monthPlanItemId ?? null, content: String(item.content ?? ''), deliverable: String(item.deliverable ?? ''), quality: String(item.quality ?? ''), plannedFinishDate: weekEnd.value } }) : []; form.items = [mergeSaveItems(items, weekEnd.value)]; restoredFromVersionId.value = version.id; restoredVersionNo.value = version.versionNo; ElMessage.warning(`历史版本 V${version.versionNo} 已恢复，请确认后手动保存`) }
async function refreshLatestDraftVersion() { if (!form.weekStart) return; const versions = await listPlanDraftVersionsApi('WEEK', form.weekStart); latestDraftVersionNo.value = versions[0]?.versionNo }
async function withdrawPlan() {
  if (!currentPlanId.value || detailStatus.value !== 'PENDING' || !form.versionNo || withdrawing.value) return
  try {
    await ElMessageBox.confirm('撤回后周计划恢复为草稿，可修改后重新提交。确认撤回？', '撤回周计划', {
      type: 'warning',
      confirmButtonText: '确认撤回',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  withdrawing.value = true
  errorMessage.value = ''
  try {
    const result = await withdrawEmployeeWeekPlanApi(currentPlanId.value, form.versionNo)
    detailStatus.value = result.status
    form.versionNo = result.versionNo
    approvalComment.value = ''
    savedSnapshot.value = normalizeForm()
    ElMessage.success('周计划已撤回为草稿')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '撤回周计划失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  } finally {
    withdrawing.value = false
  }
}
async function confirmLeave() { if (!dirty.value) return true; try { await ElMessageBox.confirm('当前有未保存修改，离开后这些内容会丢失。确认离开？', '离开编辑页', { type: 'warning', confirmButtonText: '放弃修改并离开', cancelButtonText: '继续编辑' }); return true } catch { return false } }
async function backToList() { await goBack(weekPlanBasePath) }

async function openExistingTargetWeekPlan() {
  const [existing] = await listEmployeeWeekPlansApi({ weekStart: form.weekStart })
  if (!existing) return false
  await ElMessageBox.alert('该周已有周计划，将为你打开已有计划。', '该周已有周计划', {
    type: 'info',
    confirmButtonText: '打开已有计划',
    showClose: false,
    closeOnClickModal: false,
    closeOnPressEscape: false,
  })
  const target = existing.status === 'DRAFT' || existing.status === 'REJECTED'
    ? `${weekPlanBasePath}/${existing.id}/edit`
    : `${weekPlanBasePath}/${existing.id}`
  allowRouteLeave.value = true
  try { await router.replace(target) }
  finally { allowRouteLeave.value = false }
  return true
}

async function load() {
  if (!isEdit.value) {
    loading.value = true
    try {
      if (await openExistingTargetWeekPlan()) return
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '该周周计划检查失败'
      return
    } finally { loading.value = false }
  }
  if (!isEdit.value) { await refreshLatestDraftVersion().catch(() => undefined); return }
  loading.value = true
  try {
    const detail = await getEmployeeWeekPlanApi(currentPlanId.value)
    form.weekStart = detail.summary.weekStart
    form.versionNo = detail.summary.versionNo
    form.items = [mergeSaveItems(detail.items.map((item) => ({ monthPlanItemId: item.monthPlanItemId, content: item.content, deliverable: item.deliverable || '', quality: item.quality || '', plannedFinishDate: item.plannedFinishDate || detail.summary.weekEnd })), detail.summary.weekEnd)]
    detailStatus.value = detail.summary.status
    approvalComment.value = detail.summary.approvalComment || ''
    savedSnapshot.value = normalizeForm()
    aiReview.value = await getLatestAiReviewApi('WEEK_PLAN', currentPlanId.value)
    aiReviewStale.value = false
    await refreshLatestDraftVersion().catch(() => undefined)
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : '周计划详情加载失败' } finally { loading.value = false }
}

onBeforeRouteLeave(async () => {
  if (allowRouteLeave.value) return true
  return await confirmLeave()
})
onMounted(load)

watch(form, () => {
  if (!loading.value && !saving.value && !submitting.value && !aiChecking.value && aiReview.value) aiReviewStale.value = true
}, { deep: true })
</script>

<style scoped>
.week-plan-workbench { min-width: 0; max-width: 100%; padding-bottom:112px; overflow-x: hidden; background: #f6f7f5; }
.week-editor-header { align-items: flex-start; }
.week-editor-actions { justify-content: flex-end; }
.plan-editor-layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; align-items: start; }
.plan-editor-main { display: grid; min-width: 0; gap: 16px; }
.week-plan-identity { display: flex; align-items: center; justify-content: space-between; gap: 24px; min-width: 0; padding: 20px; border: 1px solid var(--line); border-left: 4px solid #2d776c; border-radius: 8px; background: #fff; }
.week-plan-identity__copy { display: grid; min-width: 0; gap: 5px; }
.section-kicker { color: #2d776c; font-size: 10px; font-weight: 800; letter-spacing: .08em; }
.week-plan-identity__copy > strong { overflow-wrap: anywhere; color: var(--ink); font-size: 20px; }
.week-plan-identity__copy p { margin: 0; color: var(--muted); font-size: 12px; }
.week-plan-identity__meta { display: flex; align-items: center; gap: 16px; flex: 0 0 auto; }
.week-plan-identity__meta > span { display: grid; gap: 2px; color: var(--muted); font-size: 11px; text-align: center; }
.week-plan-identity__meta > span strong { color: var(--ink); font-size: 15px; }
.week-number { display: flex; align-items: baseline; gap: 4px; padding: 9px 12px; border: 1px solid #c9dbd2; border-radius: 6px; background: #eef6f1; color: #526c63; font-size: 10px; }
.week-number b { color: #2d776c; font: 800 24px/1 "IBM Plex Mono", "Cascadia Mono", monospace; }
.week-task-stage { min-width: 0; padding: 20px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.week-task-toolbar { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:16px; }
.week-task-stage h2 { margin: 5px 0; font-size: 20px; }
.week-task-stage h2 small { color: var(--muted); font-size: 12px; font-weight: 600; }
.week-task-toolbar p { margin: 0; color: var(--muted); font-size: 12px; }
.week-task-actions { display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:8px; }
.week-clear-button { border-color:#ead2c8; color:#9a513c; background:#fff8f5; }
.week-clear-button:hover:not(.is-disabled) { border-color:#c76851; color:#88412f; background:#fff2ec; }
.week-task-view-switch { flex:0 0 auto; padding:3px; border:1px solid #d8e2dd; border-radius:7px; background:#eaf0ec; }
.week-task-view-switch :deep(.el-radio-button__inner) { display:inline-flex; align-items:center; gap:6px; min-height:32px; padding:7px 12px; border:0 !important; border-radius:5px !important; color:#586b65; background:transparent; box-shadow:none !important; font-size:12px; font-weight:700; }
.week-task-view-switch :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) { color:#145f54; background:#fff; box-shadow:0 1px 4px rgb(48 73 64 / 14%) !important; }
.week-task-view-switch :deep(.el-icon) { font-size:15px; }
.week-task-editor-list { display:grid; min-width:0; gap:12px; }
.compact-week-task-table { --week-task-columns:110px minmax(430px,1fr) minmax(300px,.72fr) 190px; width:100%; min-width:0; overflow-x:auto; border:1px solid #cfdad4; }
.compact-week-task-table__head,.compact-week-task-row { display:grid; grid-template-columns:var(--week-task-columns); min-width:0; }
.compact-week-task-table__head { min-height:46px; border-bottom:1px solid #c7d4cd; color:#40564f; background:#edf2ee; font-size:16px; font-weight:800; }
.compact-week-task-table__head > span { display:grid; min-width:0; place-items:center; padding:5px 6px; border-right:1px solid #c7d4cd; text-align:center; line-height:1.25; }
.compact-week-task-table__head > span:last-child { border-right:0; }
.compact-week-task-row { min-width:1030px; min-height:260px; align-items:stretch; border-bottom:1px solid #d5ded9; background:#fff; }
.compact-week-task-table__head { min-width:1030px; }
.compact-week-task-row:last-child { border-bottom:0; }
.compact-week-task-row:hover { background:#fbfdfb; }
.compact-week-task-cell { display:flex; min-width:0; align-items:center; padding:7px; border-right:1px solid #d5ded9; }
.compact-week-task-cell:last-child { border-right:0; }
.compact-week-task-cell:focus-within { background:rgb(67 136 120 / 5%); }
.compact-week-task-cell.has-error { background:rgb(198 83 70 / 4%); box-shadow:inset 0 0 0 1px #c65346; }
.compact-week-task-cell--name { justify-content:center; color:#2f4a42; text-align:center; }
.compact-week-task-cell--content { align-items:stretch; flex-direction:column; gap:6px; }
.compact-week-task-cell--standard { align-items:stretch; flex-direction:column; gap:0; padding:0; }
.compact-week-task-cell--standard label { display:grid; min-height:0; padding:8px; border-bottom:1px dashed #d5ded9; }
.compact-week-task-cell--standard label:last-child { border-bottom:0; }
.compact-week-task-cell--standard label.has-error { background:rgb(198 83 70 / 4%); box-shadow:inset 0 0 0 1px #c65346; }
.compact-week-task-cell--standard .compact-week-field-label { display:block; margin-bottom:5px; font-size:12px; }
.compact-week-task-cell--standard :deep(.el-textarea),.compact-week-task-cell--standard :deep(.el-textarea__inner) { height:auto; }
.compact-week-task-cell--standard :deep(.el-textarea__inner) { min-height:82px !important; resize:none; }
.compact-week-task-cell--period { justify-content:center; color:#52665f; font-size:12px; line-height:1.55; text-align:center; }
.compact-week-field-label { display:none; color:#62746e; font-size:16px; font-weight:750; }
.compact-week-task-table :deep(.el-input__wrapper),.compact-week-task-table :deep(.el-textarea__inner) { padding:3px 4px; border-radius:0; background:transparent; box-shadow:none; }
.compact-week-task-table :deep(.el-textarea),.compact-week-task-table :deep(.el-textarea__inner) { height:100%; }
.compact-week-task-table :deep(.el-textarea__inner) { min-height:230px !important; resize:none; line-height:1.65; }
.compact-week-task-table :deep(.el-input__wrapper.is-focus),.compact-week-task-table :deep(.el-textarea__inner:focus) { box-shadow:none; }
.compact-week-task-cell--standard :deep(.el-textarea),.compact-week-task-cell--standard :deep(.el-textarea__inner) { height:auto; }
.compact-week-task-cell--standard :deep(.el-textarea__inner) { min-height:82px !important; resize:none; }
.week-ai-floating-entry { position: fixed; right: max(16px, env(safe-area-inset-right)); bottom: max(88px, calc(env(safe-area-inset-bottom) + 78px)); z-index: 20; display: inline-flex; align-items: center; gap: 8px; height: 44px; max-width: calc(100vw - 32px); padding: 0 16px; border: 0; border-radius: 8px; color: #fff; background: #2563eb; box-shadow: 0 10px 24px rgb(37 99 235 / 24%); font-size: 14px; font-weight: 750; cursor: pointer; transition: transform .18s ease, box-shadow .18s ease, background .18s ease; }
.week-ai-floating-entry:hover:not(:disabled) { background: #1d4ed8; box-shadow: 0 16px 40px rgb(37 99 235 / 32%); transform: translateY(-1px); }
.week-ai-floating-entry:focus-visible { outline: 3px solid rgb(37 99 235 / 28%); outline-offset: 3px; }
.week-ai-floating-entry:disabled { opacity: .55; cursor: not-allowed; }
.week-ai-floating-entry .el-icon { font-size: 17px; }
.week-ai-drawer :deep(.plan-ai) { border: 0; padding: 0; }
.week-validation-bar { position:fixed; right:34px; bottom:0; left:302px; z-index:14; display:grid; grid-template-columns:minmax(160px,.7fr) minmax(260px,1.3fr) auto minmax(280px,.8fr); gap:18px; align-items:center; width:auto; max-width:var(--content-max,1540px); min-height:68px; margin-inline:auto; padding:10px 20px max(10px,env(safe-area-inset-bottom)); border-top:1px solid #cbd7d1; background:rgb(255 253 248 / 96%); box-shadow:0 -8px 24px rgb(35 57 49 / 9%); backdrop-filter:blur(14px); }
.week-save-state { display:flex; min-width:0; align-items:center; gap:9px; }
.week-save-state > span { width:9px; height:9px; flex:0 0 auto; border-radius:50%; background:#3b8f77; }
.week-save-state > span.is-dirty { background:#c88738; }
.week-save-state > span.is-saving { background:#c88738; animation:pulse 1.1s infinite; }
.week-save-state > span.is-error { background:#c65346; }
.week-save-state > div { display:grid; min-width:0; gap:2px; }
.week-save-state strong,.week-save-state small { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.week-save-state strong { color:#40544e; font-size:12px; }
.week-save-state small { color:var(--muted); font-size:10px; }
.week-submit-readiness { display:grid; min-width:0; gap:6px; }
.week-submit-readiness > div { display:flex; min-width:0; align-items:center; gap:9px; }
.week-submit-readiness span { color:#52645f; font-size:11px; font-weight:750; white-space:nowrap; }
.week-submit-readiness strong { color:#2d776c; font:800 17px/1 "IBM Plex Mono",monospace; }
.week-submit-readiness button { padding:0; border:0; color:#b1473c; background:transparent; font-size:11px; font-weight:750; cursor:pointer; }
.week-submit-readiness :deep(.el-progress-bar__outer) { background:#e7ece9; }
.week-submit-metrics { display:flex; align-items:center; color:#667a73; font-size:11px; white-space:nowrap; }
.week-submit-metrics > span { padding:0 13px; }
.week-submit-metrics > span + span { border-left:1px solid #d9e1dd; }
.week-submit-metrics strong { color:#263d36; font:800 14px/1 "IBM Plex Mono",monospace; }
.week-validation-actions { display:grid; grid-template-columns:repeat(3,minmax(92px,1fr)); gap:10px; }
.week-validation-actions .el-button { width:100%; min-height:40px; margin:0; }
.week-save-button { border-color:#b9d1c7; color:#145f54; background:#f4faf7; font-weight:750; }
.week-save-button:hover:not(.is-disabled) { border-color:#2d776c; color:#0f4f47; background:#ecf7f2; }
.week-assist-stack { display:grid; gap:14px; }
.week-assist-card { display:grid; gap:12px; padding:14px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.week-assist-card__head { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.week-assist-card p { margin:0; color:var(--muted); font-size:12px; line-height:1.6; }
.week-issue-list { display:grid; gap:7px; }
.week-issue-list button { display:grid; grid-template-columns:22px minmax(0,1fr); gap:8px; align-items:center; width:100%; padding:9px; border:1px solid #efd2c8; border-radius:8px; color:#8d3e2f; background:#fff8f5; text-align:left; cursor:pointer; }
.week-issue-list button.is-advisory { border-color:#d9e2dd; color:#536c64; background:#f6f9f7; }
.week-issue-list button span { display:grid; width:20px; height:20px; place-items:center; border-radius:50%; color:#fff; background:#b9573f; font-size:11px; }
.week-issue-list button.is-advisory span { background:#718b82; }
.week-issue-list button b { font-size:12px; font-weight:650; line-height:1.45; }
@media (prefers-reduced-motion: reduce) { .week-ai-floating-entry { transition: none; } }
@media (max-width: 1180px) {
  .week-plan-workbench { padding-bottom:150px; }
  .week-validation-bar { grid-template-columns:minmax(150px,.7fr) minmax(250px,1.3fr) auto; gap:8px 16px; padding-inline:18px; }
  .week-submit-readiness { grid-column:1 / 3; grid-row:1; }
  .week-submit-metrics { grid-column:3; grid-row:1; }
  .week-save-state { grid-column:1; grid-row:2; }
  .week-validation-actions { grid-column:2 / 4; grid-row:2; justify-self:end; width:min(100%,520px); }
  .week-ai-floating-entry { bottom:max(150px,calc(env(safe-area-inset-bottom) + 140px)); }
}
@media (max-width: 980px) {
  .week-validation-bar { right:16px; left:16px; }
  .week-task-toolbar { align-items:flex-start; flex-direction:column; }
  .week-task-actions { justify-content:flex-start; width:100%; }
}
@media (max-width: 680px) {
  .week-plan-workbench { padding-bottom:150px; }
  .week-plan-identity { align-items: flex-start; flex-direction: column; padding: 16px; }
  .week-plan-identity__meta { width: 100%; justify-content: space-between; }
  .week-task-stage { padding: 16px; }
  .week-task-actions { align-items:stretch; flex-direction:column; }
  .week-task-actions > .el-button,.week-task-actions > .el-button-group { width:100%; }
  .week-task-actions > .el-button-group { display:grid; grid-template-columns:1fr 1fr; }
  .week-validation-bar { right:16px; left:16px; grid-template-columns:auto minmax(0,1fr) auto; gap:8px 10px; min-height:0; padding:9px 12px max(9px,env(safe-area-inset-bottom)); }
  .week-save-state { grid-column:1; grid-row:1; gap:6px; }
  .week-save-state small { display:none; }
  .week-submit-readiness { grid-column:2; grid-row:1; gap:4px; }
  .week-submit-readiness span { display:none; }
  .week-submit-readiness strong { font-size:14px; }
  .week-submit-readiness button { font-size:10px; }
  .week-submit-metrics { grid-column:3; grid-row:1; font-size:10px; }
  .week-submit-metrics > span { padding:0 5px; }
  .week-submit-metrics strong { font-size:12px; }
  .week-validation-actions { grid-column:1 / -1; grid-row:2; width:100%; }
  .week-validation-actions { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .week-validation-actions .el-button { min-height:36px; }
  .week-ai-floating-entry { right:16px; bottom:max(148px,calc(env(safe-area-inset-bottom) + 138px)); }
}
</style>
