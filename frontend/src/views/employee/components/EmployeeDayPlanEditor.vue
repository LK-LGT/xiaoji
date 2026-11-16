<template>
  <section class="employee-day-plan-editor" :class="{ 'is-compact': compact }">
    <div v-if="!compact && isLeaderRoute" class="page-header day-editor-header">
      <div>
        <span class="eyebrow">日计划 / {{ dateLabel }}</span>
        <div class="day-editor-header__title"><h1 class="page-title">日计划编制</h1><el-tag :type="statusMeta.type">{{ statusMeta.label }}</el-tag></div>
        <p class="page-subtitle">日期由日计划入口自动确定，本页只填写当天工作内容。</p>
      </div>
      <div class="toolbar">
        <el-button :icon="Clock" @click="versionDrawerVisible = true">历史版本</el-button>
        <el-button :icon="ChatLineSquare" @click="assistDrawerVisible = true">辅助工具</el-button>
        <el-tooltip content="刷新日计划" placement="bottom"><el-button :icon="Refresh" circle aria-label="刷新日计划" :loading="loading" :disabled="busy" @click="loadDetail" /></el-tooltip>
        <el-button v-if="form.status === 'submitted'" type="warning" :loading="withdrawing" @click="withdrawPlan">撤回</el-button>
      </div>
    </div>
    <div v-else-if="compact" class="day-plan-dialog-toolbar">
      <div><strong>{{ form.planDate }}</strong><span>{{ form.orgName || '未配置归属组织' }}</span></div>
      <div class="toolbar"><el-tag :type="statusMeta.type">{{ statusMeta.label }}</el-tag><el-button :icon="Clock" @click="versionDrawerVisible = true">历史版本</el-button><el-button v-if="editable" :icon="ChatLineSquare" @click="assistDrawerVisible = true">AI 助手</el-button><el-tooltip content="刷新日计划" placement="bottom"><el-button :icon="Refresh" circle aria-label="刷新日计划" :loading="loading" :disabled="busy" @click="loadDetail" /></el-tooltip><el-button v-if="form.status === 'submitted'" type="warning" :loading="withdrawing" @click="withdrawPlan">撤回</el-button></div>
    </div>

    <el-alert v-if="errorMessage" class="dashboard-alert" type="warning" :closable="false" show-icon :title="errorMessage" />
    <el-alert v-if="restoredFromVersionId" class="dashboard-alert" type="warning" :closable="false" show-icon :title="`历史版本 V${restoredVersionNo} 已恢复到当前计划，尚未保存`" />
    <el-alert v-if="calendarRuleNotice" class="dashboard-alert" :type="form.calendarRule?.forceReport ? 'warning' : 'info'" :closable="false" show-icon :title="calendarRuleNotice" />
    <el-alert v-if="!editable && !loading" class="dashboard-alert" type="info" :closable="false" show-icon title="当前日计划已提交或已流转，页面仅支持查看。" />

    <el-skeleton v-if="loading" :rows="8" animated />
    <div v-else class="day-editor-layout">
      <main class="day-editor-main">
        <PlanFeedbackBanner v-if="form.leaderComment" :status="form.status" :comment="form.leaderComment" title="直属领导反馈" :next-step="form.status === 'rejected' ? '修改计划后重新提交' : undefined" />
        <section v-if="form.departmentComment" class="day-department-feedback"><span>部门补审意见</span><p>{{ form.departmentComment }}</p><small>{{ formatTime(form.departmentReviewedAt) }}</small></section>

        <section class="day-task-stage">
          <div class="day-task-toolbar">
            <div><span class="section-kicker">计划编制</span><h2>日计划 <small>{{ workPointCount }} 个工作要点</small></h2><p>当天工作统一写在一张表中，按 1、2、3 分点即可。</p></div>
            <div class="day-task-toolbar__actions">
              <template v-if="!compact && !isLeaderRoute">
                <el-tag :type="statusMeta.type">{{ statusMeta.label }}</el-tag>
                <el-button :icon="Clock" @click="versionDrawerVisible = true">历史版本</el-button>
                <el-button :icon="ChatLineSquare" @click="assistDrawerVisible = true">辅助工具</el-button>
                <el-tooltip content="刷新日计划" placement="bottom"><el-button :icon="Refresh" circle aria-label="刷新日计划" :loading="loading" :disabled="busy" @click="loadDetail" /></el-tooltip>
                <el-button v-if="form.status === 'submitted'" type="warning" :loading="withdrawing" @click="withdrawPlan">撤回</el-button>
              </template>
              <el-button v-if="editable && hasDraftContent" class="day-clear-button" :icon="Delete" :disabled="busy" @click="clearDraft">一键清空</el-button>
              <el-radio-group v-model="taskViewMode" class="day-task-view-switch" aria-label="日计划编辑视图" @change="changeTaskViewMode">
                <el-radio-button value="table"><el-icon><Grid /></el-icon><span>表格</span></el-radio-button>
                <el-radio-button value="cards"><el-icon><Tickets /></el-icon><span>任务卡</span></el-radio-button>
              </el-radio-group>
            </div>
          </div>

          <div v-if="taskViewMode === 'table'" class="compact-day-task-table" role="table" aria-label="日计划表">
            <div class="compact-day-task-table__head" role="row"><span>姓名</span><span>工作内容</span></div>
            <article class="compact-day-task-row" role="row">
              <div class="compact-day-task-cell compact-day-task-cell--name" role="cell"><span class="compact-day-field-label">姓名</span><strong>{{ authStore.user?.realName || '当前员工' }}</strong></div>
              <div id="day-plan-content" class="compact-day-task-cell compact-day-task-cell--content" :class="{ 'has-error': showContentError }" role="cell"><span class="compact-day-field-label">工作内容</span><el-input v-if="editable" v-model="form.content" type="textarea" :rows="10" maxlength="2000" show-word-limit placeholder="1. 第一项工作&#10;2. 第二项工作&#10;3. 第三项工作" :disabled="editingDisabled" /><p v-else class="compact-day-readonly">{{ form.content || '未填写工作内容' }}</p><small v-if="showContentError" class="compact-day-field-error">请填写当天工作内容</small></div>
            </article>
          </div>

          <div v-else class="day-task-editor-list">
            <DayPlanTaskEditorCard v-model:content="form.content" :expanded="dayCardExpanded" :editable="editable" :disabled="editingDisabled" :show-content-error="showContentError" @toggle="dayCardExpanded = !dayCardExpanded" />
          </div>
        </section>

      </main>

      <PlanValidationAside v-if="compact" :issues="issues" :dirty="dirty" :saving="saving" :submitting="submitting" :editable="editable" :save-error="saveError" :summary-items="summaryItems" submit-label="提交审批" actions-first @locate="locateIssue" @save="saveDraft()" @submit="submitPlan" />
    </div>

    <aside v-if="!compact" class="day-validation-bar" aria-label="日计划提交操作">
      <div class="day-save-state" aria-live="polite"><span :class="{ 'is-saving': saving, 'is-error': !!saveError, 'is-dirty': dirty }"></span><div><strong>{{ saveError ? '保存失败' : saving ? '正在保存' : dirty ? '有未保存修改' : '当前内容已保存' }}</strong><small>{{ saveError || '提交前将再次核对' }}</small></div></div>
      <div class="day-submit-readiness"><div><span>提交准备度</span><strong>{{ readiness }}%</strong><button v-if="visibleIssues.length" type="button" @click="assistDrawerVisible = true">{{ visibleIssues.length }} 项待处理</button><el-tag v-else-if="validationFeedbackVisible" size="small" type="success">可以提交</el-tag></div><el-progress :percentage="readiness" :show-text="false" :stroke-width="7" :status="visibleBlockingIssues.length ? 'warning' : 'success'" /></div>
      <div class="day-validation-actions">
        <el-button class="day-save-button" :icon="DocumentChecked" :loading="saving" :disabled="!editable || saving || submitting || clearing" @click="saveDraft()">保存草稿</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!editable || saving || clearing" @click="submitPlan">提交审批</el-button>
      </div>
    </aside>

    <el-drawer v-model="assistDrawerVisible" title="日计划辅助工具" size="min(460px, 92vw)" append-to-body>
      <div class="day-assist-stack">
        <section class="day-assist-card day-assist-links"><div class="day-assist-card__head"><strong>关联本周计划（选填）</strong><el-tag size="small" effect="plain">辅助信息</el-tag></div><el-select v-if="editable" v-model="form.relatedWeekPlanItemIds" multiple clearable filterable collapse-tags collapse-tags-tooltip placeholder="关联本周计划任务" :disabled="editingDisabled"><el-option v-for="item in form.weekPlanItemOptions" :key="item.id" :label="item.content" :value="item.id" /></el-select><p v-else>{{ form.relatedWeekPlanItemIds.length ? `已关联 ${form.relatedWeekPlanItemIds.length} 项本周任务` : '未关联本周计划任务' }}</p></section>
        <section class="day-assist-card"><div class="day-assist-card__head"><strong>提交前检查</strong><el-tag size="small" :type="validationFeedbackVisible ? (visibleIssues.length ? 'danger' : 'success') : 'info'">{{ validationFeedbackVisible ? (visibleIssues.length ? `${visibleIssues.length} 项待处理` : '可以提交') : '尚未检查' }}</el-tag></div><div v-if="visibleIssues.length" class="day-issue-list"><button v-for="issue in visibleIssues" :key="issue.key" type="button" :class="{ 'is-advisory': !issue.blocking }" @click="locateIssue(issue)"><span>{{ issue.blocking ? '!' : 'i' }}</span><b>{{ issue.label }}</b></button></div><p v-else>{{ validationFeedbackVisible ? '日计划内容已通过提交前检查。' : '点击提交审批后，系统会定位需要补充的字段。' }}</p></section>
      </div>
    </el-drawer>

    <el-tooltip v-if="editable && !compact" content="打开 AI 日计划助手" placement="left">
      <button
        class="day-ai-floating-entry"
        type="button"
        aria-label="打开 AI 日计划助手"
        :disabled="busy"
        @click="aiAssistantVisible = true"
      >
        <el-icon><ChatLineSquare /></el-icon><span>AI 助手</span>
      </button>
    </el-tooltip>

    <el-drawer
      v-model="aiAssistantVisible"
      class="day-ai-drawer"
      title="AI 日计划助手"
      size="min(720px, 96vw)"
      append-to-body
    >
      <PlanAiAssistant
        v-if="form.planDate"
        mode="day"
        :date="form.planDate"
        :form="form"
        @version-created="latestDraftVersionNo = $event"
        @apply="applyAiDraft"
      />
    </el-drawer>

    <PlanDraftVersionDrawer v-model="versionDrawerVisible" plan-type="DAY" :period-key="form.planDate" :current-snapshot="currentVersionSnapshot" :editable="editable" @restore="restoreDraftVersion" @loaded="latestDraftVersionNo = $event" />
  </section>
</template>

<script setup lang="ts">
import { ChatLineSquare, Clock, Delete, DocumentChecked, Grid, Refresh, Tickets } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { deleteEmployeeDayPlanDraftApi, getEmployeeDayPlanDetailApi, saveEmployeeDayPlanDraftApi, submitEmployeeDayPlanApi, withdrawEmployeeDayPlanApi, type EmployeeDayPlanDetailResp, type EmployeeLegacyMonthPlanItem, type EmployeePlanStatus, type EmployeeWeekPlanItemOption, type EmployeeWorkdayCalendarItem, type SaveDayPlanDraftReq } from '@/api/employee'
import PlanFeedbackBanner from './PlanFeedbackBanner.vue'
import PlanValidationAside, { type PlanSummaryItem, type PlanValidationIssue } from './PlanValidationAside.vue'
import PlanAiAssistant from './PlanAiAssistant.vue'
import DayPlanTaskEditorCard from './DayPlanTaskEditorCard.vue'
import type { DayPlanAiForm, WeekPlanAiItem } from '@/api/employeeAi'
import { aiRequestId } from '@/api/employeeAi'
import PlanDraftVersionDrawer from './PlanDraftVersionDrawer.vue'
import { listPlanDraftVersionsApi, type PlanDraftVersionSummary } from '@/api/planDraftVersions'
import { useAuthStore } from '@/stores/auth'
import { countWeekWorkPoints } from '@/utils/weekPlanDisplay'

type TaskViewMode = 'table' | 'cards'

const props = withDefaults(defineProps<{ date: string; compact?: boolean }>(), { compact: false })
const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const authStore = useAuthStore()
const loading = ref(false); const saving = ref(false); const submitting = ref(false); const withdrawing = ref(false); const clearing = ref(false); const errorMessage = ref(''); const saveError = ref(''); const savedSnapshot = ref(''); let loadRequestId = 0
const aiAssistantVisible = ref(false)
const assistDrawerVisible = ref(false)
const validationFeedbackVisible = ref(false)
const dayCardExpanded = ref(false)
const taskViewMode = ref<TaskViewMode>(readInitialTaskViewMode())
const versionDrawerVisible = ref(false); const latestDraftVersionNo = ref<number>(); const restoredFromVersionId = ref<number>(); const restoredVersionNo = ref<number>()
// BUG-R6-14：日计划 / 周计划条目主键均为字符串 UUID。
const form = reactive({ id: '', planDate: props.date, orgName: '', relatedWeekPlanItemIds: [] as string[], content: '', remark: '', status: 'draft' as EmployeePlanStatus, weekPlanItemOptions: [] as EmployeeWeekPlanItemOption[], legacyMonthPlanItem: null as EmployeeLegacyMonthPlanItem | null, reviewStatus: 'PENDING_COMMENT', riskLevel: 'LOW', leaderComment: '', reviewedAt: '', departmentComment: '', departmentReviewedAt: '', calendarRule: null as EmployeeWorkdayCalendarItem | null })
const planStatusMap = { draft: { label: '草稿', type: 'info' }, submitted: { label: '已提交', type: 'warning' }, approved: { label: '已通过', type: 'success' }, rejected: { label: '已驳回', type: 'danger' }, confirmed: { label: '已确认', type: 'success' }, paused: { label: '已暂停', type: 'warning' }, canceled: { label: '已撤销', type: 'danger' }, archived: { label: '已归档', type: 'info' } } as const
const editable = computed(() => form.status === 'draft' || form.status === 'rejected')
const isLeaderRoute = computed(() => route.path.startsWith('/leader/'))
const currentVersionSnapshot = computed<Record<string, unknown>>(() => ({ planDate: form.planDate, relatedWeekPlanItemIds: [...form.relatedWeekPlanItemIds], content: form.content, remark: '' }))
const busy = computed(() => loading.value || saving.value || submitting.value || withdrawing.value || clearing.value)
const editingDisabled = computed(() => !editable.value || busy.value)
const statusMeta = computed(() => planStatusMap[form.status] ?? planStatusMap.draft)
const dateLabel = computed(() => `${shortDate(props.date)} ${weekdayLabel(props.date)}`)
const calendarRuleNotice = computed(() => { const rule = form.calendarRule; if (!rule || (!rule.explicit && rule.ruleType === 'WORKDAY')) return ''; const labels: Record<string, string> = { WORKDAY: '工作日', WEEKEND: '周末', HOLIDAY: '节假日', LEAVE: '请假', BUSINESS_TRIP: '出差', SPECIAL_SHIFT: '特殊排班' }; return `${labels[rule.ruleType] || rule.ruleType}：${rule.forceReport ? '当日需要填报日计划' : '当日不强制填报日计划'}${rule.description ? `；${rule.description}` : ''}` })
const dirty = computed(() => !!savedSnapshot.value && normalizeForm() !== savedSnapshot.value)
const issues = computed<PlanValidationIssue[]>(() => { const result: PlanValidationIssue[] = []; if (!form.content.trim()) result.push({ key: 'content', label: '请填写当天工作内容', targetId: 'day-plan-content', blocking: true }); return result })
const visibleIssues = computed(() => validationFeedbackVisible.value ? issues.value : [])
const visibleBlockingIssues = computed(() => visibleIssues.value.filter((issue) => issue.blocking))
const readiness = computed(() => form.content.trim() ? 100 : 0)
const showContentError = computed(() => validationFeedbackVisible.value && !form.content.trim())
const workPointCount = computed(() => countWeekWorkPoints(form.content))
const summaryItems = computed<PlanSummaryItem[]>(() => [{ label: '工作内容', value: form.content.trim() ? '已填写' : '未填写' }, { label: '工作要点', value: `${workPointCount.value} 项` }])
const hasDraftContent = computed(() => Boolean(form.content.trim() || form.relatedWeekPlanItemIds.length || form.remark.trim()))

function shortDate(value: string) { return value ? value.slice(5).replace('-', '/') : '--' }
function weekdayLabel(value: string) { if (!value) return ''; return ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][new Date(`${value}T00:00:00`).getDay()] }
function formatTime(value?: string) { return value ? value.replace('T', ' ').slice(0, 16) : '' }
function normalizeForm() { return JSON.stringify({ planDate: form.planDate, relatedWeekPlanItemIds: [...form.relatedWeekPlanItemIds].sort(), content: form.content.trim() }) }
function readInitialTaskViewMode(): TaskViewMode { try { const saved = window.localStorage.getItem('day-plan-task-view'); return saved === 'cards' ? 'cards' : 'table' } catch { return 'table' } }
function changeTaskViewMode(value: string | number | boolean | undefined) { const mode: TaskViewMode = value === 'cards' ? 'cards' : 'table'; taskViewMode.value = mode; try { window.localStorage.setItem('day-plan-task-view', mode) } catch { /* preference persistence is optional */ } }
function applyAiDraft(value: WeekPlanAiItem[] | DayPlanAiForm) { if (Array.isArray(value)) return; form.content = value.content; form.remark = ''; if (taskViewMode.value === 'cards') dayCardExpanded.value = true }
function applyDetail(detail: EmployeeDayPlanDetailResp) { Object.assign(form, { id: detail.id, planDate: detail.planDate, orgName: detail.orgName ?? '', relatedWeekPlanItemIds: [...(detail.relatedWeekPlanItemIds ?? [])], content: detail.content ?? '', remark: detail.remark ?? '', status: detail.status, weekPlanItemOptions: detail.weekPlanItemOptions ?? [], legacyMonthPlanItem: detail.legacyMonthPlanItem ?? null, reviewStatus: detail.reviewStatus ?? 'PENDING_COMMENT', riskLevel: detail.riskLevel ?? 'LOW', leaderComment: detail.leaderComment ?? '', reviewedAt: detail.reviewedAt ?? '', departmentComment: detail.departmentComment ?? '', departmentReviewedAt: detail.departmentReviewedAt ?? '', calendarRule: detail.calendarRule ?? null }); savedSnapshot.value = normalizeForm() }
function buildPayload(recordVersion = false): SaveDayPlanDraftReq { return { id: form.id || undefined, planDate: form.planDate, relatedWeekPlanItemIds: [...form.relatedWeekPlanItemIds], content: form.content.trim(), remark: '', recordVersion, versionRequestId: recordVersion ? aiRequestId() : undefined, restoredFromVersionId: recordVersion ? restoredFromVersionId.value : undefined } }
async function confirmDiscard() {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前日计划草稿还没有保存，是否先保存草稿？', '离开日计划编制', {
      type: 'warning',
      distinguishCancelAndClose: true,
      confirmButtonText: '保存草稿',
      cancelButtonText: '不保存离开',
      closeOnClickModal: false,
    })
    return await saveDraft({ allowEmpty: true, silent: true })
  } catch (action) {
    return action === 'cancel'
  }
}
async function locateIssue(issue: PlanValidationIssue) { if (!issue.targetId) return; assistDrawerVisible.value = false; if (taskViewMode.value === 'cards') dayCardExpanded.value = true; await nextTick(); const target = document.getElementById(issue.targetId); target?.scrollIntoView({ behavior: 'smooth', block: 'center' }); setTimeout(() => (target?.querySelector('input, textarea, button, [tabindex]') as HTMLElement | null)?.focus(), 280) }
async function loadDetail() { const requestId = ++loadRequestId; const requestedDate = props.date; loading.value = true; errorMessage.value = ''; validationFeedbackVisible.value = false; try { const detail = await getEmployeeDayPlanDetailApi(requestedDate); if (requestId !== loadRequestId || requestedDate !== props.date) return; applyDetail(detail); restoredFromVersionId.value = undefined; restoredVersionNo.value = undefined; await refreshLatestDraftVersion().catch(() => undefined) } catch (error) { if (requestId === loadRequestId) errorMessage.value = error instanceof Error ? error.message : '日计划加载失败，请稍后重试' } finally { if (requestId === loadRequestId) loading.value = false } }
async function saveDraft(options: { allowEmpty?: boolean; silent?: boolean } = {}) {
  if (!editable.value || saving.value) return false
  if (!options.allowEmpty && !form.content.trim()) {
    validationFeedbackVisible.value = true
    await locateIssue(issues.value[0])
    return false
  }
  saving.value = true; saveError.value = ''; errorMessage.value = ''
  try {
    applyDetail(await saveEmployeeDayPlanDraftApi(buildPayload(true)))
    restoredFromVersionId.value = undefined; restoredVersionNo.value = undefined
    await refreshLatestDraftVersion().catch(() => undefined)
    if (!options.silent) ElMessage.success('日计划草稿已保存')
    emit('changed')
    return true
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : '保存草稿失败，请稍后重试'
    if (options.silent) ElMessage.error(saveError.value)
    return false
  } finally {
    saving.value = false
  }
}
async function submitPlan() { validationFeedbackVisible.value = true; if (restoredFromVersionId.value) { ElMessage.warning('请先保存已恢复的历史版本'); return } const blocking = issues.value.find((issue) => issue.blocking); if (!editable.value || blocking) { if (blocking) await locateIssue(blocking); return } submitting.value = true; saveError.value = ''; errorMessage.value = ''; try { const result = await submitEmployeeDayPlanApi(buildPayload(false)); form.id = result.id; form.status = result.status; savedSnapshot.value = normalizeForm(); ElMessage.success('日计划已提交'); emit('changed') } catch (error) { errorMessage.value = error instanceof Error ? error.message : '提交日计划失败，请稍后重试' } finally { submitting.value = false } }

async function clearDraft() {
  if (!editable.value || clearing.value || !hasDraftContent.value) return
  try {
    await ElMessageBox.confirm('确认清空当前日计划草稿内容？清空后仍可重新填写。', '一键清空草稿', {
      type: 'warning',
      confirmButtonText: '清空草稿',
      cancelButtonText: '取消',
    })
  } catch { return }
  clearing.value = true; saveError.value = ''; errorMessage.value = ''
  try {
    if (form.id && form.status === 'draft') {
      await deleteEmployeeDayPlanDraftApi(form.id)
      form.id = ''
      form.relatedWeekPlanItemIds = []
      form.content = ''
      form.remark = ''
      form.leaderComment = ''
      form.departmentComment = ''
      form.reviewStatus = 'PENDING_COMMENT'
      form.riskLevel = 'LOW'
      savedSnapshot.value = normalizeForm()
      emit('changed')
    } else if (form.id) {
      form.relatedWeekPlanItemIds = []
      form.content = ''
      form.remark = ''
      applyDetail(await saveEmployeeDayPlanDraftApi(buildPayload(true)))
      await refreshLatestDraftVersion().catch(() => undefined)
      emit('changed')
    } else {
      form.relatedWeekPlanItemIds = []
      form.content = ''
      form.remark = ''
      savedSnapshot.value = normalizeForm()
    }
    restoredFromVersionId.value = undefined
    restoredVersionNo.value = undefined
    validationFeedbackVisible.value = false
    ElMessage.success('草稿内容已清空')
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : '清空草稿失败，请稍后重试'
  } finally {
    clearing.value = false
  }
}
function restoreDraftVersion(snapshot: Record<string, unknown>, version: PlanDraftVersionSummary) { form.relatedWeekPlanItemIds = Array.isArray(snapshot.relatedWeekPlanItemIds) ? snapshot.relatedWeekPlanItemIds.map((item) => String(item).trim()).filter(Boolean) : []; form.content = String(snapshot.content ?? ''); form.remark = ''; restoredFromVersionId.value = version.id; restoredVersionNo.value = version.versionNo; ElMessage.warning(`历史版本 V${version.versionNo} 已恢复，请确认后手动保存`) }
async function refreshLatestDraftVersion() { if (!form.planDate) return; const versions = await listPlanDraftVersionsApi('DAY', form.planDate); latestDraftVersionNo.value = versions[0]?.versionNo }
async function withdrawPlan() { if (!form.id || form.status !== 'submitted' || withdrawing.value) return; try { await ElMessageBox.confirm('撤回后日计划恢复为草稿，可修改后重新提交。确认撤回？', '撤回日计划', { type: 'warning' }) } catch { return } withdrawing.value = true; errorMessage.value = ''; try { await withdrawEmployeeDayPlanApi(form.id); ElMessage.success('日计划已撤回为草稿'); await loadDetail(); emit('changed') } catch (error) { errorMessage.value = error instanceof Error ? error.message : '撤回日计划失败，请稍后重试'; await loadDetail() } finally { withdrawing.value = false } }

watch(() => props.date, loadDetail)
onBeforeRouteLeave(async () => await confirmDiscard())
onMounted(loadDetail)
</script>

<style scoped>
.day-editor-header {
  align-items: flex-end;
}

.day-editor-header__title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.day-editor-header__title .page-title {
  margin-bottom: 0;
}

.day-plan-dialog-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.day-plan-dialog-toolbar > div:first-child {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.day-plan-dialog-toolbar span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.day-editor-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.day-editor-main {
  display: grid;
  gap: 14px;
  min-width: 0;
}

.day-department-feedback {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
}

.day-department-feedback {
  background: #f7f9f7;
}

.day-department-feedback span {
  color: var(--blue);
  font-size: 10px;
  font-weight: 800;
}

.day-department-feedback p {
  margin: 8px 0;
  line-height: 1.65;
}

.day-department-feedback small {
  color: var(--muted);
}

.day-ai-assistant {
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
}

.day-ai-assistant :deep(.el-collapse-item__header),
.day-ai-assistant :deep(.el-collapse-item__wrap) {
  height: auto;
  min-height: 58px;
  border: 0;
}

.day-ai-assistant :deep(.el-collapse-item__content) {
  padding-bottom: 14px;
}

.day-ai-assistant__title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.day-ai-assistant__title > svg {
  width: 18px;
  color: var(--blue);
}

.day-ai-assistant__title > span {
  display: grid;
  gap: 2px;
  text-align: left;
}

.day-ai-assistant__title strong {
  color: var(--ink);
  font-size: 13px;
}

.day-ai-assistant__title small {
  overflow: hidden;
  color: var(--muted);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.day-ai-floating-entry {
  position: fixed;
  right: max(16px, env(safe-area-inset-right));
  bottom: max(20px, env(safe-area-inset-bottom));
  z-index: 20;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  max-width: calc(100vw - 32px);
  padding: 0 16px;
  border: 0;
  border-radius: 8px;
  color: #fff;
  background: #2563eb;
  box-shadow: 0 10px 24px rgb(37 99 235 / 24%);
  font-size: 14px;
  font-weight: 750;
  cursor: pointer;
  transition: transform .18s ease, box-shadow .18s ease, background-color .18s ease;
}

.day-ai-floating-entry:hover:not(:disabled) {
  background: #1d4ed8;
  box-shadow: 0 16px 40px rgb(37 99 235 / 32%);
  transform: translateY(-1px);
}

.day-ai-floating-entry:focus-visible {
  outline: 3px solid rgb(37 99 235 / 28%);
  outline-offset: 3px;
}

.day-ai-floating-entry:disabled {
  cursor: not-allowed;
  opacity: .55;
}

.day-ai-floating-entry .el-icon {
  font-size: 17px;
}

.day-ai-drawer :deep(.plan-ai) {
  padding: 0;
  border: 0;
}

.employee-day-plan-editor:not(.is-compact) {
  min-width: 0;
  padding-bottom: 112px;
  overflow-x: hidden;
}

.section-kicker {
  color: var(--blue);
  font-size: 10px;
  font-weight: 800;
}

.day-task-stage {
  min-width: 0;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
}

.day-task-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.day-task-toolbar > div:first-child {
  display: grid;
  gap: 4px;
}

.day-task-toolbar h2 {
  margin: 0;
  font-size: 20px;
}

.day-task-toolbar h2 small,
.day-task-toolbar p {
  color: var(--muted);
  font-size: 12px;
}

.day-task-toolbar p {
  margin: 0;
}

.day-task-toolbar__actions {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.day-task-view-switch {
  flex: 0 0 auto;
  padding: 3px;
  border: 1px solid #d8e2dd;
  border-radius: 7px;
  background: #eaf0ec;
}

.day-task-view-switch :deep(.el-radio-button__inner) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 7px 12px;
  border: 0 !important;
  border-radius: 5px !important;
  color: #586b65;
  background: transparent;
  box-shadow: none !important;
  font-size: 12px;
  font-weight: 700;
}

.day-task-view-switch :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  color: #145f54;
  background: #fff;
  box-shadow: 0 1px 4px rgb(48 73 64 / 14%) !important;
}

.compact-day-task-table {
  --day-task-columns: 110px minmax(520px, 1fr);
  width: 100%;
  min-width: 0;
  overflow-x: auto;
  border: 1px solid #cfdad4;
}

.compact-day-task-table__head,
.compact-day-task-row {
  display: grid;
  grid-template-columns: var(--day-task-columns);
  min-width: 680px;
}

.compact-day-task-table__head {
  min-height: 44px;
  border-bottom: 1px solid #c7d4cd;
  color: #40564f;
  background: #e6f0f6;
  font-size: 14px;
  font-weight: 800;
}

.compact-day-task-table__head > span {
  display: grid;
  min-width: 0;
  place-items: center;
  padding: 6px;
  border-right: 1px solid #c7d4cd;
  text-align: center;
}

.compact-day-task-table__head > span:last-child {
  border-right: 0;
}

.compact-day-task-row {
  min-height: 280px;
  align-items: stretch;
  background: #fff;
}

.compact-day-task-cell {
  display: flex;
  min-width: 0;
  align-items: center;
  padding: 8px;
  border-right: 1px solid #d5ded9;
}

.compact-day-task-cell:last-child {
  border-right: 0;
}

.compact-day-task-cell:focus-within {
  background: rgb(67 136 120 / 5%);
}

.compact-day-task-cell.has-error {
  background: #fff9f6;
  box-shadow: inset 0 0 0 1px #c65346;
}

.compact-day-task-cell--name {
  justify-content: center;
  color: #2f4a42;
  text-align: center;
}

.compact-day-task-cell--content {
  align-items: stretch;
  flex-direction: column;
  gap: 6px;
}

.compact-day-field-label {
  display: none;
  color: #62746e;
  font-size: 13px;
  font-weight: 750;
}

.compact-day-field-error {
  color: #b5533d;
  font-size: 11px;
}

.compact-day-task-table :deep(.el-textarea),
.compact-day-task-table :deep(.el-textarea__inner) {
  height: 100%;
}

.compact-day-task-table :deep(.el-textarea__inner) {
  min-height: 250px !important;
  padding: 8px;
  resize: none;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  line-height: 1.65;
}

.compact-day-readonly {
  width: 100%;
  margin: 0;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.day-task-editor-list {
  display: grid;
  min-width: 0;
  gap: 12px;
}

.day-validation-bar {
  position: fixed;
  right: 34px;
  bottom: 0;
  left: 302px;
  z-index: 14;
  display: grid;
  grid-template-columns: minmax(160px, .7fr) minmax(260px, 1.3fr) minmax(280px, .8fr);
  gap: 18px;
  align-items: center;
  width: auto;
  max-width: var(--content-max, 1540px);
  min-height: 68px;
  margin-inline: auto;
  padding: 10px 20px max(10px, env(safe-area-inset-bottom));
  border-top: 1px solid #cbd7d1;
  background: rgb(255 253 248 / 96%);
  box-shadow: 0 -8px 24px rgb(35 57 49 / 9%);
  backdrop-filter: blur(14px);
}

.day-save-state {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 9px;
}

.day-save-state > span {
  width: 9px;
  height: 9px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: #3b8f77;
}

.day-save-state > span.is-dirty,
.day-save-state > span.is-saving { background: #c88738; }
.day-save-state > span.is-error { background: #c65346; }
.day-save-state > div { display: grid; min-width: 0; gap: 2px; }
.day-save-state strong,.day-save-state small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.day-save-state strong { color: #40544e; font-size: 12px; }
.day-save-state small { color: var(--muted); font-size: 10px; }

.day-submit-readiness {
  display: grid;
  min-width: 0;
  gap: 6px;
}

.day-submit-readiness > div { display: flex; min-width: 0; align-items: center; gap: 9px; }
.day-submit-readiness span { color: #52645f; font-size: 11px; font-weight: 750; white-space: nowrap; }
.day-submit-readiness strong { color: #2d776c; font: 800 17px/1 "IBM Plex Mono", monospace; }
.day-submit-readiness button { padding: 0; border: 0; color: #b1473c; background: transparent; font-size: 11px; font-weight: 750; cursor: pointer; }
.day-submit-readiness :deep(.el-progress-bar__outer) { background: #e7ece9; }

.day-validation-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.day-validation-actions .el-button { width: 100%; min-height: 40px; margin: 0; }
.day-save-button {
  border-color: #b9d1c7;
  color: #145f54;
  background: #f4faf7;
  font-weight: 750;
}

.day-save-button:hover:not(.is-disabled) {
  border-color: #2d776c;
  color: #0f4f47;
  background: #ecf7f2;
}

.day-clear-button {
  border-color: #ead2c8;
  color: #9a513c;
  background: #fff8f5;
}

.day-clear-button:hover:not(.is-disabled) {
  border-color: #c76851;
  color: #88412f;
  background: #fff2ec;
}

.day-assist-stack { display: grid; gap: 14px; }
.day-assist-card { display: grid; gap: 12px; padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.day-assist-card__head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.day-assist-card p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.day-issue-list { display: grid; gap: 7px; }
.day-issue-list button { display: grid; grid-template-columns: 22px minmax(0, 1fr); gap: 8px; align-items: center; width: 100%; padding: 9px; border: 1px solid #efd2c8; border-radius: 8px; color: #8d3e2f; background: #fff8f5; text-align: left; cursor: pointer; }
.day-issue-list button span { display: grid; width: 20px; height: 20px; place-items: center; border-radius: 50%; color: #fff; background: #b9573f; font-size: 11px; }
.day-issue-list button b { font-size: 12px; font-weight: 650; line-height: 1.45; }

.employee-day-plan-editor:not(.is-compact) .day-ai-floating-entry {
  bottom: max(88px, calc(env(safe-area-inset-bottom) + 78px));
}

@media (prefers-reduced-motion: reduce) {
  .day-ai-floating-entry {
    transition: none;
  }
}

.is-compact .day-editor-layout {
  grid-template-columns: minmax(0, 1fr) 290px;
}

@media (max-width: 1180px) {
  .employee-day-plan-editor:not(.is-compact) { padding-bottom: 150px; }

  .day-validation-bar {
    grid-template-columns: minmax(150px, .7fr) minmax(250px, 1.3fr) auto;
    gap: 8px 16px;
    padding-inline: 18px;
  }

  .day-submit-readiness { grid-column: 1 / 3; grid-row: 1; }
  .day-save-state { grid-column: 1; grid-row: 2; }
  .day-validation-actions { grid-column: 2 / 4; grid-row: 2; justify-self: end; width: min(100%, 480px); }
  .employee-day-plan-editor:not(.is-compact) .day-ai-floating-entry { bottom: max(150px, calc(env(safe-area-inset-bottom) + 140px)); }

  .day-editor-layout,
  .is-compact .day-editor-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .day-validation-bar { right: 16px; left: 16px; }
}

@media (max-width: 760px) {
  .day-editor-header,
  .day-plan-dialog-toolbar,
  .day-task-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .day-task-stage { padding: 16px; }
  .day-task-toolbar__actions { align-items: stretch; flex-direction: column; width: 100%; }
  .day-task-toolbar__actions .day-clear-button { width: 100%; margin: 0; }
  .day-task-view-switch { width: 100%; }
  .day-task-view-switch :deep(.el-radio-button) { width: 50%; }
  .day-task-view-switch :deep(.el-radio-button__inner) { justify-content: center; width: 100%; }

  .compact-day-task-table { overflow: visible; border: 0; }
  .compact-day-task-table__head { display: none; }
  .compact-day-task-row { grid-template-columns: 1fr; min-width: 0; min-height: 0; gap: 8px; }
  .compact-day-task-cell { align-items: stretch; flex-direction: column; gap: 8px; border: 1px solid #d5ded9; }
  .compact-day-task-cell:last-child { border-right: 1px solid #d5ded9; }
  .compact-day-field-label { display: block; }
  .compact-day-task-table :deep(.el-textarea__inner) { min-height: 150px !important; }
  .compact-day-task-cell--name { align-items: flex-start; text-align: left; }

  .employee-day-plan-editor:not(.is-compact) { padding-bottom: 150px; }
  .day-validation-bar { grid-template-columns: auto minmax(0, 1fr); gap: 8px 10px; min-height: 0; padding: 9px 12px max(9px, env(safe-area-inset-bottom)); }
  .day-save-state { grid-column: 1; grid-row: 1; gap: 6px; }
  .day-save-state small { display: none; }
  .day-submit-readiness { grid-column: 2; grid-row: 1; gap: 4px; }
  .day-submit-readiness span { display: none; }
  .day-submit-readiness strong { font-size: 14px; }
  .day-submit-readiness button { font-size: 10px; }
  .day-validation-actions { grid-column: 1 / -1; grid-row: 2; width: 100%; }
  .day-validation-actions .el-button { min-height: 36px; }
  .employee-day-plan-editor:not(.is-compact) .day-ai-floating-entry { right: 16px; bottom: max(148px, calc(env(safe-area-inset-bottom) + 138px)); }

}

</style>
