<template>
  <section class="page-panel month-plan-editor-page">
    <div v-if="isLeaderContext" class="page-header month-editor-header">
      <div>
        <h1 class="page-title">{{ form.planMonth || '--' }} 月计划编制</h1>
        <p class="page-subtitle">{{ form.employeeName || '当前员工' }} · {{ form.departmentName || '当前部门' }} · 编制任务并提交审批</p>
      </div>
      <div class="toolbar"><el-button :icon="ArrowLeft" @click="backToList">返回月计划</el-button><el-button :icon="Clock" @click="versionDrawerVisible = true">历史版本</el-button><el-button :icon="ChatLineSquare" @click="assistDrawerVisible = true">辅助工具</el-button><el-button v-if="form.status === 'submitted'" type="warning" plain :loading="withdrawing" @click="withdrawPlan">撤回计划</el-button></div>
    </div>

    <el-alert v-if="errorMessage" class="dashboard-alert" type="warning" :closable="false" show-icon :title="errorMessage" />
    <el-alert v-if="restoredFromVersionId" class="dashboard-alert" type="warning" :closable="false" show-icon :title="`历史版本 V${restoredVersionNo} 已恢复到当前计划，尚未保存`" />

    <el-skeleton v-if="loading" :rows="8" animated />
    <template v-else>
      <section v-if="form.approvalComment" class="rejection-guidance">
        <div><span>审批反馈</span><strong>{{ form.approvalComment }}</strong></div><p>请对照反馈修改对应任务，检查栏会持续提示未完成项。</p>
      </section>

      <div class="editor-workbench mt16">
        <main class="editor-main">
          <div v-if="!isLeaderContext" class="toolbar editor-context-actions"><el-button :icon="ArrowLeft" @click="backToList">返回月计划</el-button><el-button :icon="Clock" @click="versionDrawerVisible = true">历史版本</el-button><el-button :icon="ChatLineSquare" @click="assistDrawerVisible = true">辅助工具</el-button><el-button v-if="form.status === 'submitted'" type="warning" plain :loading="withdrawing" @click="withdrawPlan">撤回计划</el-button></div>
          <section v-if="!form.id" class="month-plan-identity">
            <el-form label-position="top">
              <el-form-item label="计划月份"><el-date-picker v-model="form.planMonth" type="month" value-format="YYYY-MM" :disabled="editingDisabled" /></el-form-item>
            </el-form>
          </section>

          <section class="task-editor-section" :class="{ 'is-ai-applied': aiAppliedHighlight }">
            <div class="task-editor-toolbar">
              <div><span class="section-kicker">任务编制</span><h2>本月任务 <small>{{ form.items.length }} 项</small></h2><p>按“任务定义—交付验收—排期考核”逐条完成。</p></div>
              <div class="task-section-actions">
                <el-button v-if="editable && hasMonthPlanDraft" class="month-clear-button" :icon="Delete" :disabled="editingDisabled || saving || optimizingIndex >= 0" @click="clearDraft">一键清空</el-button>
                <el-radio-group v-model="taskViewMode" class="task-view-switch" aria-label="任务编辑视图" @change="changeTaskViewMode">
                  <el-radio-button value="table"><el-icon><Grid /></el-icon><span>表格</span></el-radio-button>
                  <el-radio-button value="cards"><el-icon><Tickets /></el-icon><span>任务卡</span></el-radio-button>
                </el-radio-group>
                <el-button type="primary" :icon="Plus" :disabled="editingDisabled" @click="addRow">新增任务</el-button>
              </div>
            </div>
            <div v-if="form.items.length && taskViewMode === 'table'" class="compact-task-table" role="table" aria-label="月计划任务表">
              <div class="compact-task-table__head" role="row">
                <span>序号</span><span>工作计划内容</span><span>权重（%）</span><span>考核标准（交付物、质量）</span><span>完成时间段</span><span>预估价值</span><span>操作</span>
              </div>
              <article v-for="(row, index) in form.items" :key="row.uiKey" class="compact-task-row" role="row" :data-task-key="row.uiKey">
                <div class="compact-task-cell compact-task-cell--index" role="cell"><span class="sheet-row-index">{{ String(index + 1).padStart(2, '0') }}</span></div>
                <label class="compact-task-cell compact-task-cell--content" role="cell">
                  <span class="compact-field-label">任务内容</span>
                  <div
                    class="sheet-field month-task-content-field"
                    :class="{ 'has-error': taskFieldError(index, 'taskContent'), 'is-editing': taskContentEditingKey === row.uiKey, 'is-empty': !hasTaskContent(row) }"
                    :data-field-id="`task-${index}-taskContent`"
                  >
                    <MonthPlanTaskContentView
                      v-if="hasTaskContent(row) && taskContentEditingKey !== row.uiKey"
                      class="month-task-content-preview"
                      :content="row.taskContent"
                      empty-text=""
                      @click="focusTaskContent(index, row)"
                    />
                    <el-input
                      v-show="taskContentEditingKey === row.uiKey || !hasTaskContent(row)"
                      v-model="row.taskContent"
                      class="month-task-content-input"
                      type="textarea"
                      :autosize="{ minRows: 1 }"
                      maxlength="5000"
                      :disabled="editingDisabled"
                      placeholder="工作范围和关键动作"
                      @focus="taskContentEditingKey = row.uiKey"
                      @blur="taskContentEditingKey = null"
                    />
                  </div>
                </label>
                <label class="compact-task-cell compact-task-cell--weight" role="cell">
                  <span class="compact-field-label">权重</span>
                  <div class="sheet-field" :class="{ 'has-error': taskFieldError(index, 'performanceWeight') }" :data-field-id="`task-${index}-performanceWeight`"><el-input-number v-model="row.performanceWeight" placeholder="0.00" :disabled="editingDisabled" :min="0" :max="100" :step="1" :precision="2" controls-position="right" /></div>
                </label>
                <div class="compact-task-cell compact-task-cell--standard" role="cell">
                  <label>
                    <span class="compact-field-label">交付物</span>
                    <div class="sheet-field" :class="{ 'has-error': taskFieldError(index, 'deliverable') }" :data-field-id="`task-${index}-deliverable`">
                      <el-input v-model="row.deliverable" type="textarea" :autosize="{ minRows: 1 }" maxlength="500" :disabled="editingDisabled" placeholder="填写交付物" />
                    </div>
                  </label>
                  <label>
                    <span class="compact-field-label">质量</span>
                    <div class="sheet-field" :class="{ 'has-error': taskFieldError(index, 'quality') }" :data-field-id="`task-${index}-quality`">
                      <el-input v-model="row.quality" type="textarea" :autosize="{ minRows: 1 }" maxlength="500" :disabled="editingDisabled" placeholder="填写质量标准" />
                    </div>
                  </label>
                </div>
                <div class="compact-task-cell compact-task-cell--period" role="cell">
                  <label><span class="compact-field-label">开始</span><div class="sheet-field" :class="{ 'has-error': taskFieldError(index, 'estimatedStartDate') }" :data-field-id="`task-${index}-estimatedStartDate`"><el-date-picker v-model="row.estimatedStartDate" type="date" format="M.D" value-format="YYYY-MM-DD" :disabled="editingDisabled" placeholder="开始" /></div></label>
                  <span class="period-separator">-</span>
                  <label><span class="compact-field-label">结束</span><div class="sheet-field" :class="{ 'has-error': taskFieldError(index, 'estimatedEndDate') }" :data-field-id="`task-${index}-estimatedEndDate`"><el-date-picker v-model="row.estimatedEndDate" type="date" format="M.D" value-format="YYYY-MM-DD" :disabled="editingDisabled" placeholder="结束" /></div></label>
                </div>
                <label class="compact-task-cell compact-task-cell--value" role="cell">
                  <span class="compact-field-label">预估价值</span>
                  <div class="sheet-field" :class="{ 'has-error': taskFieldError(index, 'estimatedValue') }" :data-field-id="`task-${index}-estimatedValue`"><el-input-number v-model="row.estimatedValue" placeholder="0.00" :disabled="editingDisabled" :min="0" :max="9999999999.99" :step="100" :precision="2" controls-position="right" /></div>
                </label>
                <div class="compact-task-cell compact-task-cell--actions" role="cell">
                  <el-tooltip :content="optimizingIndex === index ? '停止生成' : 'AI 优化'" placement="top"><el-button text circle :type="optimizingIndex === index ? 'danger' : 'primary'" :icon="MagicStick" :aria-label="optimizingIndex === index ? '停止优化当前任务' : 'AI 优化当前任务'" :disabled="editingDisabled && optimizingIndex !== index" @click.stop="optimizingIndex === index ? stopOptimization() : optimizeRow(index)" /></el-tooltip>
                  <el-tooltip content="复制" placement="top"><el-button text circle :icon="CopyDocument" aria-label="复制当前任务" :disabled="editingDisabled" @click.stop="duplicateRow(index)" /></el-tooltip>
                  <el-tooltip content="删除" placement="top"><el-button text circle type="danger" :icon="Delete" aria-label="删除当前任务" :disabled="editingDisabled" @click.stop="removeRow(index)" /></el-tooltip>
                </div>
              </article>
            </div>
            <div v-else-if="form.items.length" class="task-editor-list">
              <MonthPlanTaskEditorCard
                v-for="(item, index) in form.items"
                :key="item.uiKey"
                :item="item"
                :index="index"
                :task-key="item.uiKey"
                :expanded="expandedTaskKeys.has(item.uiKey)"
                :issues="issuesForItem(index)"
                :disabled="editingDisabled"
                :optimizing="optimizingIndex === index"
                :disabled-start-date="allowAnyDate"
                :disabled-end-date="allowAnyDate"
                @toggle-expanded="toggleTaskExpanded(item.uiKey)"
                @optimize="optimizeRow(index)"
                @stop-optimize="stopOptimization"
                @duplicate="duplicateRow(index)"
                @remove="removeRow(index)"
              />
            </div>
            <el-empty v-else description="还没有计划任务"><el-button type="primary" :disabled="editingDisabled" @click="addRow">添加第一个任务</el-button></el-empty>
            <button class="add-task-strip" type="button" :disabled="editingDisabled" @click="addRow">＋ 新增一项计划任务</button>
          </section>
        </main>

        <aside class="validation-aside" aria-label="月计划提交操作">
          <div class="submit-save-state" aria-live="polite">
            <span :class="`is-${autoSaveStatus}`"></span>
            <div class="submit-save-state__copy"><strong>{{ autoSaveState.text }}</strong><small v-if="lastSavedAt">最后保存 {{ lastSavedAt }}</small></div>
            <el-button v-if="autoSaveStatus === 'error'" link type="danger" @click="saveDraft(false)">重试</el-button>
          </div>
          <div class="submit-readiness">
            <div class="submit-readiness__head">
              <span>提交准备度</span>
              <strong>{{ readiness }}%</strong>
              <button v-if="visibleValidationIssues.length" class="submit-issue-link" type="button" @click="assistDrawerVisible = true">{{ visibleValidationIssues.length }} 项待处理</button>
              <el-tag v-else-if="validationFeedbackVisible" size="small" type="success">可以提交</el-tag>
            </div>
            <el-progress :percentage="readiness" :show-text="false" :stroke-width="7" :status="visibleValidationIssues.length ? 'warning' : 'success'" />
          </div>
          <PlanAiReviewCell class="submit-metrics" :review="aiReview" :loading="aiChecking" :stale="aiReviewStale" show-status @view="aiReviewDrawerVisible = true" />
          <div class="validation-aside__actions">
            <el-button :disabled="editingDisabled || saving" @click="saveAndReturn">保存并返回</el-button>
            <el-button plain type="primary" :loading="aiChecking" :disabled="editingDisabled || saving || submitting" @click="checkMonthPlanNow">AI检查</el-button>
            <el-button type="primary" :loading="submitting" :disabled="editingDisabled || saving || aiChecking" @click="submitApproval">提交审批</el-button>
          </div>
        </aside>
      </div>

    </template>

    <el-tooltip content="打开 AI 月计划助手" placement="left">
      <button class="ai-floating-entry" type="button" aria-label="打开 AI 月计划助手" :disabled="saving" @click="openAiWorkspace">
        <el-icon><ChatLineSquare /></el-icon><span>AI 助手</span>
      </button>
    </el-tooltip>

    <el-drawer v-model="assistDrawerVisible" title="辅助工具" size="min(460px, 92vw)" append-to-body>
      <div class="assist-drawer-stack">
        <AiReviewPanel class="month-aside-ai-panel" :review="aiReview" :stale="aiReviewStale" title="AI月计划检查" empty-text="AI会检查任务是否具体、交付物是否可核验，以及任务、期限和权重之间是否合理。" compact>
          <template #actions>
            <el-button v-if="aiChecking" type="danger" plain @click="stopReviewCheck">停止生成</el-button>
            <el-button v-else type="primary" plain :disabled="loading || saving || submitting" @click="checkMonthPlanNow">{{ aiReview ? '重新AI检查' : '立即AI检查' }}</el-button>
          </template>
        </AiReviewPanel>
        <section class="assist-tool-card assist-tool-card--plain">
          <div class="issue-title"><strong>提交前检查</strong><el-tag size="small" :type="validationFeedbackVisible ? (visibleValidationIssues.length ? 'danger' : 'success') : 'info'">{{ validationFeedbackVisible ? (visibleValidationIssues.length ? `${visibleValidationIssues.length} 项待处理` : '可以提交') : '尚未检查' }}</el-tag></div>
          <div v-if="visibleValidationIssues.length" class="issue-list">
            <section v-for="group in visibleValidationIssueGroups" :key="group.key" class="issue-group">
              <header><strong>{{ group.label }}</strong><span>{{ group.total }} 项</span></header>
              <button v-for="issue in group.issues" :key="issue.id" type="button" @click="locateValidationIssue(issue)"><span>!</span>{{ issue.message }}</button>
            </section>
          </div>
          <p v-else-if="validationFeedbackVisible" class="all-clear">任务字段完整，常规任务权重合计为 100%。</p>
        </section>
      </div>
    </el-drawer>

    <PlanAiReviewDrawer
      v-model="aiReviewDrawerVisible"
      plan-type="MONTH"
      :review="aiReview"
      :loading="aiChecking"
      :stale="aiReviewStale"
      :subject="`${form.planMonth || '--'} 月计划`"
      :context="[form.employeeName, form.departmentName].filter(Boolean).join(' · ')"
    />

    <el-dialog v-model="optimizeDialogVisible" title="应用 AI 单条优化建议" width="min(880px, 94vw)">
      <el-tag v-if="optimizeSuggestion?.draftVersionNo" class="mb12" type="success">已保存为 V{{ optimizeSuggestion.draftVersionNo }}</el-tag>
      <el-alert type="info" :closable="false" show-icon title="勾选需要应用的字段；未选择字段保持原值。" />
      <el-alert v-if="optimizePreviewFields.length && !hasOptimizeChanges" class="mt16" type="success" :closable="false" show-icon title="AI 建议与当前内容一致，无需应用修改。" />
      <el-checkbox-group v-if="optimizePreviewFields.length" v-model="selectedOptimizeFields" class="optimize-fields">
        <div v-for="field in optimizePreviewFields" :key="field.key" class="optimize-field" :class="{ 'is-changed': field.changed }">
          <div class="optimize-field-heading">
            <el-checkbox :value="field.key">{{ field.label }}</el-checkbox>
            <el-tag size="small" :type="field.changed ? 'warning' : 'info'">{{ field.changed ? '有变化' : '未变化' }}</el-tag>
          </div>
          <div><span>原内容</span><p>{{ displayOptimizeValue(field.originalValue, field.key) }}</p></div>
          <div><span>AI 优化后</span><p>{{ displayOptimizeValue(field.suggestedValue, field.key) }}</p></div>
        </div>
      </el-checkbox-group>
      <el-alert v-for="warning in optimizeSuggestion?.warnings || []" :key="warning" class="mt16" type="warning" :closable="false" :title="warning" />
      <template #footer>
        <el-button @click="ignoreOptimization">忽略</el-button>
        <el-button type="primary" :disabled="!selectedOptimizeFields.length" @click="applyOptimization">应用所选字段</el-button>
      </template>
    </el-dialog>
    <PlanDraftVersionDrawer v-model="versionDrawerVisible" plan-type="MONTH" :period-key="form.planMonth" :current-snapshot="currentVersionSnapshot" :editable="editable" @restore="restoreDraftVersion" @loaded="latestDraftVersionNo = $event" />
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import {
  clearEmployeeMonthPlanDraftApi,
  createEmployeeMonthPlanDraftApi,
  deleteEmployeeMonthPlanItemApi,
  getEmployeeMonthPlanDetailApi,
  saveEmployeeMonthPlanDraftApi,
  submitEmployeeMonthPlanApi,
  withdrawEmployeeMonthPlanApi,
  type EmployeeMonthPlanDraftClearResp,
  type EmployeeMonthPlanDetailResp,
  type EmployeePlanStatus,
  type SaveMonthPlanDraftReq,
} from '@/api/employee'
import { currentMonth } from '@/api/performance'
import { resolveAiConversation } from '@/api/aiConversations'
import { checkPlanAiReviewApi, getLatestAiReviewApi, type AiReview } from '@/api/aiReview'
import AiReviewPanel from '@/components/AiReviewPanel.vue'
import PlanAiReviewCell from '@/components/PlanAiReviewCell.vue'
import PlanAiReviewDrawer from '@/components/PlanAiReviewDrawer.vue'
import { notifyAiReviewResult } from '@/utils/aiReviewFeedback'
import { normalizeMonthPlanPeriods } from '@/utils/monthPlanPeriod'
import { listMonthPlansApi } from '@/api/planning'
import {
  MONTH_PLAN_CONTEXT_TYPES,
  aiRequestId,
  optimizeMonthPlanItemApi,
  recordAiSuggestionActionApi,
  type AiOptimizeResponse,
  type AiPlanItem,
  type MonthPlanContextType,
} from '@/api/employeeAi'
import { usePersonalMonthPlanRoutes } from '@/composables/usePersonalMonthPlanRoutes'
import { useCancelableAiRequest } from '@/composables/useCancelableAiRequest'
import { ArrowLeft, ChatLineSquare, Clock, CopyDocument, Delete, Grid, MagicStick, Plus, Tickets } from '@element-plus/icons-vue'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import MonthPlanTaskEditorCard from './components/MonthPlanTaskEditorCard.vue'
import PlanDraftVersionDrawer from './components/PlanDraftVersionDrawer.vue'
import { listPlanDraftVersionsApi, type PlanDraftVersionSummary } from '@/api/planDraftVersions'

interface EditableMonthPlanItem extends Omit<AiPlanItem, 'performanceWeight' | 'estimatedValue'> {
  /** BUG-R6-14：月计划条目 ID 是字符串主键。 */
  id?: string
  uiKey: string
  performanceWeight: number | null
  estimatedValue: number | null
}

type OptimizableField = 'taskName' | 'taskContent' | 'deliverable' | 'quality' | 'estimatedStartDate' | 'estimatedEndDate' | 'performanceWeight' | 'estimatedValue'
type TaskViewMode = 'table' | 'cards'

interface OptimizePreviewField {
  key: OptimizableField
  label: string
  originalValue: unknown
  suggestedValue: unknown
  changed: boolean
}

interface ValidationIssue {
  id: string
  itemIndex?: number
  field?: string
  message: string
}

const route = useRoute()
const router = useRouter()
const { isLeaderContext, basePath, editPath, aiPath } = usePersonalMonthPlanRoutes()

function readInitialPlanMonth() {
  const rawMonth = route.query.planMonth
  const month = Array.isArray(rawMonth) ? rawMonth[0] : rawMonth
  return typeof month === 'string' && /^\d{4}-\d{2}$/.test(month)
    ? month
    : currentMonth()
}

const loading = ref(false)
const saving = ref(false)
const submitting = ref(false)
const withdrawing = ref(false)
const clearing = ref(false)
const aiReviewDrawerVisible = ref(false)
const aiReview = ref<AiReview | null>(null)
const aiReviewStale = ref(false)
const { running: aiChecking, run: runReviewCheck, stop: stopReviewRequest } = useCancelableAiRequest()
const errorMessage = ref('')
const lastSavedAt = ref('')
const autoSaveStatus = ref<'idle' | 'saving' | 'saved' | 'readonly' | 'error'>('idle')
const dirty = ref(false)
let suppressChangeTracking = false
let changeVersion = 0
let autoSaveTimer: ReturnType<typeof setTimeout> | undefined
let skipNextLeaveConfirm = false
const MONTH_PLAN_TOPBAR_RETURN_KEY = 'month-plan-topbar-return'
const optimizingIndex = ref(-1)
const { run: runOptimization, stop: stopOptimizationRequest } = useCancelableAiRequest()
const optimizeDialogVisible = ref(false)
const optimizeSuggestion = ref<AiOptimizeResponse>()
const optimizePreviewFields = ref<OptimizePreviewField[]>([])
const selectedOptimizeFields = ref<OptimizableField[]>([])
const optimizationTargetIndex = ref(-1)
const aiJobDescription = ref('')
const selectedAiContextTypes = ref<MonthPlanContextType[]>([...MONTH_PLAN_CONTEXT_TYPES])
const aiConversationId = ref<number>()
const aiAppliedHighlight = ref(false)
const assistDrawerVisible = ref(false)
const taskViewMode = ref<TaskViewMode>(readInitialTaskViewMode())
const taskContentEditingKey = ref<string | null>(null)
const versionDrawerVisible = ref(false)
const latestDraftVersionNo = ref<number>()
const restoredFromVersionId = ref<number>()
const restoredVersionNo = ref<number>()
let optimizationRequestVersion = 0
let aiConversationRequest = 0
let aiAppliedTimer: ReturnType<typeof setTimeout> | undefined
const optimizableFields: Array<{ key: OptimizableField; label: string }> = [
  { key: 'taskContent', label: '任务内容' },
  { key: 'deliverable', label: '交付物' }, { key: 'quality', label: '质量' },
  { key: 'estimatedStartDate', label: '预估开始日期' }, { key: 'estimatedEndDate', label: '预估结束日期' },
  { key: 'performanceWeight', label: '绩效权重' }, { key: 'estimatedValue', label: '预估价值（元）' },
]
const hasOptimizeChanges = computed(() => optimizePreviewFields.value.some((field) => field.changed))

const form = reactive({
  id: null as string | null,
  planMonth: readInitialPlanMonth(),
  employeeName: '',
  departmentName: '',
  status: 'draft' as EmployeePlanStatus,
  approvalComment: '',
  items: [] as EditableMonthPlanItem[],
})
const expandedTaskKeys = reactive(new Set<string>())
let nextLocalTaskKey = 0
const createTaskUiKey = (id?: string) => id ? `saved-${id}` : `local-${++nextLocalTaskKey}`

function editableNumber(value: unknown) {
  const number = Number(value ?? 0)
  return Number.isFinite(number) && number !== 0 ? number : null
}

function numberForSave(value: unknown) {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number : 0
}

function toAiPlanItem(item: Omit<EditableMonthPlanItem, 'id' | 'uiKey'>): AiPlanItem {
  return {
    ...item,
    performanceWeight: numberForSave(item.performanceWeight),
    estimatedValue: numberForSave(item.estimatedValue),
  }
}

const editable = computed(() => form.status === 'draft' || form.status === 'rejected')
const currentVersionSnapshot = computed<Record<string, unknown>>(() => ({
  planMonth: form.planMonth,
  items: form.items.map(({ uiKey: _uiKey, ...item }) => ({ ...item })),
}))
const editingDisabled = computed(() => !editable.value || submitting.value || aiChecking.value || clearing.value)
const hasMonthPlanDraft = computed(() => Boolean(form.id || form.items.length))
const validationIssuesExpanded = ref(false)
const validationFeedbackVisible = ref(false)
const regularWeightTotal = computed(() => Number(form.items
  .reduce((sum, item) => sum + (Number(item.performanceWeight) || 0), 0)
  .toFixed(2)))
const validationIssues = computed<ValidationIssue[]>(() => {
  const issues: ValidationIssue[] = []
  if (!form.planMonth) issues.push({ id: 'plan-month', field: 'planMonth', message: '请选择计划月份' })
  if (!form.items.length) issues.push({ id: 'items-empty', message: '请至少新增一项计划任务' })
  const requiredFields: Array<[keyof EditableMonthPlanItem, string]> = [['taskContent', '任务内容'], ['deliverable', '交付物'], ['quality', '质量'], ['estimatedStartDate', '预估开始日期'], ['estimatedEndDate', '预估结束日期']]
  form.items.forEach((item, itemIndex) => {
    requiredFields.forEach(([field, label]) => { if (!String(item[field] || '').trim()) issues.push({ id: `${itemIndex}-${String(field)}`, itemIndex, field: String(field), message: `任务 ${itemIndex + 1}：请填写${label}` }) })
    if (!item.performanceWeight || item.performanceWeight <= 0 || item.performanceWeight > 100) issues.push({ id: `${itemIndex}-performanceWeight`, itemIndex, field: 'performanceWeight', message: `任务 ${itemIndex + 1}：请填写有效绩效权重` })
    if (Number(item.estimatedValue) < 0 || Number(item.estimatedValue) > 9999999999.99) issues.push({ id: `${itemIndex}-estimatedValue`, itemIndex, field: 'estimatedValue', message: `任务 ${itemIndex + 1}：预估价值需在 0 至 9999999999.99 元之间` })
    if (item.estimatedStartDate && item.estimatedEndDate && item.estimatedStartDate > item.estimatedEndDate) issues.push({ id: `${itemIndex}-estimatedEndDate-order`, itemIndex, field: 'estimatedEndDate', message: `任务 ${itemIndex + 1}：预估结束日期不能早于开始日期` })
  })
  if (form.items.length && regularWeightTotal.value !== 100) issues.push({ id: 'weight-total', field: 'performanceWeight', message: `常规任务权重合计需为 100%，当前为 ${regularWeightTotal.value}%` })
  return issues
})
const visibleValidationIssues = computed(() => validationFeedbackVisible.value ? validationIssues.value : [])
const validationIssueGroups = computed(() => [
  { key: 'plan', label: '计划信息', issues: visibleValidationIssues.value.filter((issue) => issue.itemIndex === undefined) },
  { key: 'tasks', label: '任务内容', issues: visibleValidationIssues.value.filter((issue) => issue.itemIndex !== undefined) },
].filter((group) => group.issues.length))
const visibleValidationIssueGroups = computed(() => validationIssueGroups.value.map((group) => ({
  ...group,
  total: group.issues.length,
  issues: validationIssuesExpanded.value ? group.issues : group.issues.slice(0, group.key === 'plan' ? 3 : 4),
})))
const hiddenValidationIssueCount = computed(() => validationIssueGroups.value
  .reduce((count, group) => count + Math.max(0, group.issues.length - (group.key === 'plan' ? 3 : 4)), 0))
const readiness = computed(() => {
  if (!form.items.length) return 0
  const planMonthReady = Boolean(form.planMonth && form.planMonth >= currentMonth())
  const planScore = planMonthReady ? 10 : 0
  const itemFieldCount = form.items.length * 7
  const completedItemFields = form.items.reduce((count, item) => count
    + (String(item.taskContent || '').trim() ? 1 : 0)
    + (String(item.deliverable || '').trim() ? 1 : 0)
    + (String(item.quality || '').trim() ? 1 : 0)
    + (String(item.estimatedStartDate || '').trim() ? 1 : 0)
    + (String(item.estimatedEndDate || '').trim() ? 1 : 0)
    + (Number(item.performanceWeight) > 0 ? 1 : 0)
    + (Number(item.estimatedValue) >= 0 ? 1 : 0), 0)
  const itemScore = itemFieldCount ? (completedItemFields / itemFieldCount) * 65 : 0
  const weightDistance = Math.min(Math.abs(regularWeightTotal.value - 100), 100)
  const weightScore = regularWeightTotal.value > 0 ? (1 - weightDistance / 100) * 25 : 0
  return Math.max(0, Math.min(100, Math.round(planScore + itemScore + weightScore)))
})
const autoSaveState = computed(() => {
  if (!editable.value || autoSaveStatus.value === 'readonly') return { text: '只读', type: 'info' as const }
  if (autoSaveStatus.value === 'saving') return { text: '自动保存中', type: 'warning' as const }
  if (autoSaveStatus.value === 'saved') return { text: '已自动保存', type: 'success' as const }
  if (autoSaveStatus.value === 'error') return { text: '自动保存失败', type: 'danger' as const }
  return { text: dirty.value ? '有未保存修改' : '尚未修改', type: 'info' as const }
})

function formatDate(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function allowAnyDate() {
  return false
}

function readInitialTaskViewMode(): TaskViewMode {
  if (typeof window === 'undefined') return 'table'
  try {
    const stored = window.localStorage.getItem('month-plan-task-view')
    if (stored === 'table' || stored === 'cards') return stored
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
  return window.matchMedia('(max-width: 980px)').matches ? 'cards' : 'table'
}

function defaultEstimatedStartDate() {
  if (!form.planMonth) return ''
  const today = formatDate(new Date())
  return form.planMonth === today.slice(0, 7) ? today : `${form.planMonth}-01`
}

function defaultEstimatedEndDate() {
  if (!form.planMonth) return ''
  const [year, month] = form.planMonth.split('-').map(Number)
  return formatDate(new Date(year, month, 0))
}

function getPlanId() {
  const rawId = route.params.id ?? route.query.id
  // BUG-R6-14: plan id is a UUID/hex string primary key, never a number.
  const id = String(Array.isArray(rawId) ? rawId[0] : rawId ?? '').trim()
  return id ? id : null
}

async function applyDetail(detail: EmployeeMonthPlanDetailResp | EmployeeMonthPlanDraftClearResp, preserveExpansion = false) {
  suppressChangeTracking = true
  try {
    const expandedIndexes = preserveExpansion
      ? form.items.flatMap((item, index) => expandedTaskKeys.has(item.uiKey) ? [index] : [])
      : []
    form.id = detail.id
    form.planMonth = detail.planMonth
    form.employeeName = detail.employeeName
    form.departmentName = detail.departmentName
    form.status = detail.status
    form.approvalComment = detail.approvalComment ?? ''
    const detailItems = detail.items.map((item) => ({
      id: item.id,
      uiKey: createTaskUiKey(item.id),
      workType: 'UNKNOWN' as const,
      taskName: item.taskName,
      taskContent: item.taskContent,
      deliverable: item.deliverable,
      quality: item.quality ?? '',
      estimatedStartDate: item.estimatedStartDate ?? '',
      estimatedEndDate: item.estimatedEndDate ?? '',
      performanceWeight: editableNumber(item.performanceWeight),
      estimatedValue: editableNumber(item.estimatedValue),
    }))
    form.items = normalizeMonthPlanPeriods(detail.planMonth, detailItems).map((item, index) => {
      const raw = detailItems[index]
      const emptyRow = !String(raw.taskName || '').trim()
        && !String(raw.taskContent || '').trim()
        && !String(raw.deliverable || '').trim()
        && !String(raw.quality || '').trim()
        && raw.performanceWeight == null
        && raw.estimatedValue == null
        && !String(raw.estimatedStartDate || '').trim()
        && !String(raw.estimatedEndDate || '').trim()
      return emptyRow ? { ...item, estimatedStartDate: '', estimatedEndDate: '' } : item
    })
    expandedTaskKeys.clear()
    expandedIndexes.forEach((index) => {
      const item = form.items[index]
      if (item) expandedTaskKeys.add(item.uiKey)
    })
    autoSaveStatus.value = editable.value ? 'idle' : 'readonly'
    await nextTick()
    dirty.value = false
  } finally {
    suppressChangeTracking = false
  }
}

function taskNameForSave(item: Pick<EditableMonthPlanItem, 'taskName' | 'taskContent'>) {
  const existing = String(item.taskName || '').trim()
  if (existing) return existing
  return String(item.taskContent || '').replace(/\s+/g, ' ').trim().slice(0, 30)
}

function hasTaskContent(item: Pick<EditableMonthPlanItem, 'taskContent'>) {
  return Boolean(String(item.taskContent || '').trim())
}

async function focusTaskContent(index: number, row: EditableMonthPlanItem) {
  if (editingDisabled.value) return
  taskContentEditingKey.value = row.uiKey
  await nextTick()
  const field = document.querySelector(`[data-field-id="task-${index}-taskContent"]`) as HTMLElement | null
  ;(field?.querySelector('textarea') as HTMLElement | null)?.focus()
}

function buildPayload(version?: Pick<SaveMonthPlanDraftReq, 'recordVersion' | 'versionRequestId' | 'restoredFromVersionId'>): SaveMonthPlanDraftReq {
  return {
    planMonth: form.planMonth,
    items: form.items.map((item) => ({
      id: item.id,
      taskName: taskNameForSave(item),
      taskContent: item.taskContent,
      deliverable: item.deliverable,
      quality: item.quality,
      estimatedStartDate: item.estimatedStartDate,
      estimatedEndDate: item.estimatedEndDate,
      performanceWeight: numberForSave(item.performanceWeight),
      estimatedValue: numberForSave(item.estimatedValue),
    })),
    ...version,
  }
}

function ensureFormItemPeriods() {
  form.items = normalizeMonthPlanPeriods(form.planMonth, form.items)
}

function validateDraft(showFeedback = false) {
  ensureFormItemPeriods()
  if (showFeedback) validationFeedbackVisible.value = true
  const firstIssue = validationIssues.value[0]
  if (!firstIssue) return true
  ElMessage.warning(firstIssue.message)
  void locateValidationIssue(firstIssue)
  return false
}

async function loadDetail() {
  let planId = getPlanId()
  loading.value = true
  errorMessage.value = ''
  try {
    if (!planId) {
      const existing = await findExistingActiveMonthPlan(form.planMonth)
      if (!existing) {
        autoSaveStatus.value = 'idle'
        await refreshLatestDraftVersion().catch(() => undefined)
        return
      }
      planId = existing.id
      await router.replace(editPath(planId))
      ElMessage.info('该月份已有月计划，已为你打开原计划')
    }
    await applyDetail(await getEmployeeMonthPlanDetailApi(planId))
    aiReview.value = form.id ? await getLatestAiReviewApi('MONTH_PLAN', form.id) : null
    aiReviewStale.value = false
    await refreshLatestDraftVersion().catch(() => undefined)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '月计划加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function findExistingActiveMonthPlan(planMonth: string) {
  const matches = await listMonthPlansApi({ mine: true, planMonth }).catch(() => [])
  let existing = (matches ?? []).find((plan) => plan.planMonth === planMonth && plan.status !== 'CANCELED')
  if (existing) return existing

  const allMine = await listMonthPlansApi({ mine: true }).catch(() => [])
  existing = (allMine ?? []).find((plan) => plan.planMonth === planMonth && plan.status !== 'CANCELED')
  return existing
}

async function saveDraft(showMessage = true, recordVersion = false, syncRoute = true) {
  if (!editable.value || saving.value) return false
  ensureFormItemPeriods()
  saving.value = true
  autoSaveStatus.value = 'saving'
  errorMessage.value = ''
  const versionAtStart = changeVersion
  const payload = buildPayload(recordVersion ? {
    recordVersion: true,
    versionRequestId: aiRequestId(),
    restoredFromVersionId: restoredFromVersionId.value,
  } : { recordVersion: false })
  try {
    if (!form.id) {
      const existing = await findExistingActiveMonthPlan(form.planMonth)
      if (existing) {
        if (existing.status === 'DRAFT' || existing.status === 'REJECTED') {
          const detail = await saveEmployeeMonthPlanDraftApi(existing.id, payload)
          await applyDetail(detail, true)
          if (syncRoute) await router.replace(editPath(detail.id))
          lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
          dirty.value = false
          autoSaveStatus.value = 'saved'
          if (recordVersion) {
            restoredFromVersionId.value = undefined
            restoredVersionNo.value = undefined
            await refreshLatestDraftVersion().catch(() => undefined)
          }
          if (showMessage) ElMessage.success('草稿已保存')
          return true
        }

        if (!syncRoute) {
          autoSaveStatus.value = 'error'
          errorMessage.value = '该月份已存在月计划，请打开原计划继续编辑'
          ElMessage.error(errorMessage.value)
          return false
        }
        await router.replace(editPath(existing.id))
        await applyDetail(await getEmployeeMonthPlanDetailApi(existing.id), true)
        autoSaveStatus.value = editable.value ? 'idle' : 'readonly'
        errorMessage.value = '该月份已存在月计划，请打开原计划继续编辑'
        if (showMessage) ElMessage.info('该月份已有月计划，已为你打开原计划')
        return false
      }
    }

    const detail = form.id
      ? await saveEmployeeMonthPlanDraftApi(form.id, payload)
      : await createEmployeeMonthPlanDraftApi(payload)
    await applyDetail(detail, true)
    const changedDuringSave = changeVersion !== versionAtStart
    if (!getPlanId() && syncRoute) {
      await router.replace(editPath(detail.id))
    }
    lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    dirty.value = changedDuringSave
    autoSaveStatus.value = changedDuringSave ? 'idle' : 'saved'
    if (recordVersion) {
      restoredFromVersionId.value = undefined
      restoredVersionNo.value = undefined
      await refreshLatestDraftVersion().catch(() => undefined)
    }
    if (showMessage) {
      if (changedDuringSave) ElMessage.info('已保存提交前的内容，最新修改将继续自动保存')
      else ElMessage.success('草稿已保存')
    }
    return true
  } catch (error) {
    const message = error instanceof Error ? error.message : ''
    if (!form.id && message.includes('已存在月计划')) {
      const existingPlans = await listMonthPlansApi({ mine: true, planMonth: form.planMonth }).catch(() => [])
      const existing = (existingPlans ?? []).find((plan) => plan.planMonth === form.planMonth && plan.status !== 'CANCELED')
      if (existing) {
        if (existing.status === 'DRAFT' || existing.status === 'REJECTED') {
          const detail = await saveEmployeeMonthPlanDraftApi(existing.id, payload)
          await applyDetail(detail, true)
          if (syncRoute) await router.replace(editPath(detail.id))
          lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
          dirty.value = false
          autoSaveStatus.value = 'saved'
          if (showMessage) ElMessage.success('草稿已保存')
          return true
        }
        if (!syncRoute) {
          autoSaveStatus.value = 'error'
          errorMessage.value = '该月份已存在月计划，请打开原计划继续编辑'
          ElMessage.error(errorMessage.value)
          return false
        }
        await router.replace(editPath(existing.id))
        await applyDetail(await getEmployeeMonthPlanDetailApi(existing.id), true)
        errorMessage.value = ''
        autoSaveStatus.value = editable.value ? 'idle' : 'readonly'
        if (showMessage) ElMessage.info('该月份已有月计划，已为你打开原计划')
        return false
      }
    }
    autoSaveStatus.value = 'error'
    errorMessage.value = message || '保存草稿失败，请稍后重试'
    return false
  } finally {
    saving.value = false
  }
}

async function clearDraft() {
  if (!editable.value || clearing.value || !hasMonthPlanDraft.value || optimizingIndex.value >= 0) return
  if (autoSaveTimer) {
    clearTimeout(autoSaveTimer)
    autoSaveTimer = undefined
  }
  try {
    await ElMessageBox.confirm('确认清空当前月计划草稿？清空后会删除当前草稿并回到空白月计划。', '一键清空草稿', {
      type: 'warning',
      confirmButtonText: '清空草稿',
      cancelButtonText: '取消',
    })
  } catch {
    scheduleAutoSave()
    return
  }

  clearing.value = true
  errorMessage.value = ''
  try {
    if (form.id) {
      const detail = await clearEmployeeMonthPlanDraftApi(form.id)
      await applyDetail(detail)
      await router.replace({ path: editPath(), query: { planMonth: detail.planMonth } })
      dirty.value = false
      autoSaveStatus.value = 'saved'
      lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    } else {
      suppressChangeTracking = true
      form.items = []
      expandedTaskKeys.clear()
      await nextTick()
      suppressChangeTracking = false
      dirty.value = false
      autoSaveStatus.value = 'idle'
      lastSavedAt.value = ''
    }
    restoredFromVersionId.value = undefined
    restoredVersionNo.value = undefined
    validationFeedbackVisible.value = false
    aiReviewStale.value = Boolean(aiReview.value)
    ElMessage.success('月计划草稿已清空')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清空月计划草稿失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  } finally {
    suppressChangeTracking = false
    clearing.value = false
  }
}

async function submitApproval() {
  if (restoredFromVersionId.value) { ElMessage.warning('请先保存已恢复的历史版本'); return }
  if (!editable.value || !validateDraft(true)) return
  try {
    await ElMessageBox.confirm('确认提交当前月计划审批？提交后将进入审批流程。', '提交月计划', {
      type: 'warning',
      confirmButtonText: '提交审批',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    const saved = await saveDraft(false)
    if (!saved || !form.id) return
    const result = await submitEmployeeMonthPlanApi(form.id)
    form.status = result.status
    autoSaveStatus.value = 'readonly'
    ElMessage.success('已提交审批')
    await router.push({ path: basePath.value, query: { month: form.planMonth } })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交审批失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  } finally {
    submitting.value = false
  }
}

async function checkMonthPlanNow() {
  if (restoredFromVersionId.value) { ElMessage.warning('请先保存已恢复的历史版本'); return }
  if (aiChecking.value || saving.value || submitting.value) return
  if (editable.value && !validateDraft(false)) return
  errorMessage.value = ''
  try {
    if (editable.value) {
      const saved = await saveDraft(false)
      if (!saved) return
    }
    if (!form.id) throw new Error('请先保存月计划后再检查')
    const result = await runReviewCheck(({ requestId, signal }) => checkPlanAiReviewApi(
      'MONTH_PLAN', form.id!, { requestId, signal },
    ))
    if (!result) return
    aiReview.value = result
    aiReviewStale.value = false
    assistDrawerVisible.value = false
    aiReviewDrawerVisible.value = true
    notifyAiReviewResult(aiReview.value, 'AI语义检查已完成，逐维度报告已显示在当前页面')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'AI检查失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  }
}

async function stopReviewCheck() {
  const confirmed = await stopReviewRequest()
  confirmed ? ElMessage.info('已停止本次生成') : ElMessage.warning('界面已停止等待，服务端取消未确认')
}

function markDirty() {
  if (!editable.value || loading.value || suppressChangeTracking) return
  changeVersion += 1
  dirty.value = true
  if (aiReview.value) aiReviewStale.value = true
}

function scheduleAutoSave() {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  if (!editable.value || loading.value || suppressChangeTracking || restoredFromVersionId.value || !form.planMonth || !form.items.length) return
  autoSaveTimer = setTimeout(() => {
    if (dirty.value && !saving.value && editable.value) void saveDraft(false)
  }, 3000)
}

function addRow() {
  form.items.push({
    uiKey: createTaskUiKey(),
    workType: 'UNKNOWN',
    taskName: '',
    taskContent: '',
    deliverable: '',
    quality: '',
    estimatedStartDate: defaultEstimatedStartDate(),
    estimatedEndDate: defaultEstimatedEndDate(),
    performanceWeight: null,
    estimatedValue: null,
  })
  markDirty()
  void expandAndLocateTask(form.items.length - 1, 'taskContent')
}

function duplicateRow(index: number) {
  const source = form.items[index]
  if (!source) return
  form.items.splice(index + 1, 0, { ...source, id: undefined, uiKey: createTaskUiKey(), taskName: '' })
  markDirty()
  void expandAndLocateTask(index + 1, 'taskContent')
  ElMessage.success(`已复制任务 ${index + 1}`)
}

function changeTaskViewMode(value: string | number | boolean | undefined) {
  const mode: TaskViewMode = value === 'cards' ? 'cards' : 'table'
  taskViewMode.value = mode
  try {
    if (typeof window !== 'undefined') window.localStorage.setItem('month-plan-task-view', mode)
  } catch {
    // The view still changes for the current visit when persistence is blocked.
  }
}

async function withdrawPlan() {
  if (!form.id || form.status !== 'submitted' || withdrawing.value) return
  try {
    await ElMessageBox.confirm('撤回后月计划恢复为草稿，可修改后重新提交。确认撤回？', '撤回月计划', {
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
    await withdrawEmployeeMonthPlanApi(form.id)
    await loadDetail()
    ElMessage.success('月计划已撤回为草稿')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '撤回月计划失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  } finally {
    withdrawing.value = false
  }
}

function toggleTaskExpanded(uiKey: string) {
  if (expandedTaskKeys.has(uiKey)) expandedTaskKeys.delete(uiKey)
  else expandedTaskKeys.add(uiKey)
}

async function expandAndLocateTask(index: number, focusField?: string) {
  const item = form.items[index]
  if (!item) return
  expandedTaskKeys.add(item.uiKey)
  if ((focusField || 'taskContent') === 'taskContent') taskContentEditingKey.value = item.uiKey
  await nextTick()
  const field = document.querySelector(`[data-field-id="task-${index}-${focusField || 'taskContent'}"]`) as HTMLElement | null
  field?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  if (!focusField) return
  setTimeout(() => (field?.querySelector('input, textarea') as HTMLElement | null)?.focus(), 350)
}

function taskFieldError(index: number, field: string) {
  return visibleValidationIssues.value.find((issue) => issue.itemIndex === index && issue.field === field)?.message || ''
}

function issuesForItem(index: number) {
  return visibleValidationIssues.value.filter((issue) => issue.itemIndex === index)
}

async function locateValidationIssue(issue: ValidationIssue) {
  if (issue.itemIndex === undefined) {
    document.querySelector(issue.field === 'performanceWeight' ? '.task-editor-section' : '.month-plan-identity')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    return
  }
  const targetItem = form.items[issue.itemIndex]
  if (!targetItem) return
  expandedTaskKeys.add(targetItem.uiKey)
  if (issue.field === 'taskContent') taskContentEditingKey.value = targetItem.uiKey
  await nextTick()
  const field = document.querySelector(`[data-field-id="task-${issue.itemIndex}-${issue.field}"]`) as HTMLElement | null
  field?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  setTimeout(() => (field?.querySelector('input, textarea') as HTMLElement | null)?.focus(), 350)
}

async function saveAndReturn() {
  const saved = await saveDraft(true, true, false)
  if (!saved) return
  skipNextLeaveConfirm = true
  await navigateBackToList()
}

async function confirmLeaveEditor() {
  if (!editable.value || !dirty.value) return true
  try {
    await ElMessageBox.confirm('当前月计划草稿还没有保存，是否先保存草稿？', '离开月计划编制', {
      type: 'warning',
      distinguishCancelAndClose: true,
      confirmButtonText: '保存草稿',
      cancelButtonText: '不保存离开',
      closeOnClickModal: false,
    })
    return await saveDraft(false, true, false)
  } catch (action) {
    return action === 'cancel'
  }
}

function restoreDraftVersion(snapshot: Record<string, unknown>, version: PlanDraftVersionSummary) {
  const items = Array.isArray(snapshot.items) ? snapshot.items : []
  suppressChangeTracking = true
  form.items = items.map((raw) => {
    const item = raw as Partial<EditableMonthPlanItem>
    return {
      id: typeof item.id === 'string' ? item.id : undefined,
      uiKey: createTaskUiKey(typeof item.id === 'string' ? item.id : undefined),
      workType: item.workType ?? 'UNKNOWN',
      taskName: String(item.taskName ?? ''),
      taskContent: String(item.taskContent ?? ''),
      deliverable: String(item.deliverable ?? ''),
      quality: String(item.quality ?? ''),
      estimatedStartDate: String(item.estimatedStartDate ?? ''),
      estimatedEndDate: String(item.estimatedEndDate ?? ''),
      performanceWeight: editableNumber(item.performanceWeight),
      estimatedValue: editableNumber(item.estimatedValue),
    }
  })
  expandedTaskKeys.clear()
  suppressChangeTracking = false
  restoredFromVersionId.value = version.id
  restoredVersionNo.value = version.versionNo
  markDirty()
  ElMessage.warning(`历史版本 V${version.versionNo} 已恢复，请确认后手动保存`)
}

async function refreshLatestDraftVersion() {
  if (!form.planMonth) return
  const versions = await listPlanDraftVersionsApi('MONTH', form.planMonth)
  latestDraftVersionNo.value = versions[0]?.versionNo
}

async function openAiWorkspace() {
  if (saving.value || loading.value) return
  if (!form.id) {
    const existing = await findExistingActiveMonthPlan(form.planMonth)
    if (existing) {
      await router.push(aiPath(existing.id))
      return
    }
    await router.push({ path: aiPath('new'), query: { planMonth: form.planMonth } })
    return
  }
  await router.push(aiPath(form.id))
}

async function revealAiAppliedChange() {
  if (route.query.aiApplied !== '1') return
  aiAppliedHighlight.value = true
  await nextTick()
  document.querySelector('.task-editor-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  aiAppliedTimer = setTimeout(() => { aiAppliedHighlight.value = false }, 2200)
  const query = { ...route.query }
  delete query.aiApplied
  await router.replace({ path: route.path, query })
}

async function optimizeRow(index: number) {
  const row = form.items[index]
  if (!row || editingDisabled.value || optimizingIndex.value >= 0) return
  const sourceSnapshot = { ...row }
  const request = ++optimizationRequestVersion
  const requestedMonth = form.planMonth
  optimizingIndex.value = index
  errorMessage.value = ''
  try {
    const { id: _id, uiKey: _uiKey, ...rawItem } = sourceSnapshot
    const item = toAiPlanItem({ ...rawItem, taskName: taskNameForSave(sourceSnapshot) })
    const response = await runOptimization(({ requestId, signal }) => optimizeMonthPlanItemApi({
      requestId,
      planMonth: form.planMonth,
      item,
      currentForm: {
        items: form.items.map(({ id: _itemId, uiKey: _uiKey, ...currentItem }) => toAiPlanItem({ ...currentItem, taskName: taskNameForSave(currentItem) })),
      },
      targetItemIndex: index,
      jobDescription: aiJobDescription.value || undefined,
      selectedContextTypes: [...selectedAiContextTypes.value],
      conversationId: aiConversationId.value,
    }, { requestId, signal }))
    if (!response) return
    if (request !== optimizationRequestVersion || requestedMonth !== form.planMonth || form.items[index] !== row || !sameOptimizableValues(row, sourceSnapshot)) {
      ElMessage.info('计划内容已变化，已忽略旧的 AI 优化结果')
      return
    }
    if (!response?.item || typeof response.item !== 'object') {
      throw new Error('AI 优化结果不完整，请重新优化')
    }
    const suggestedItems: AiPlanItem[] = form.items.map((item, itemIndex) => itemIndex === index
      ? {
          ...response.item,
          estimatedStartDate: response.item.estimatedStartDate || sourceSnapshot.estimatedStartDate,
          estimatedEndDate: response.item.estimatedEndDate || sourceSnapshot.estimatedEndDate,
        }
      : toAiPlanItem({ ...item, taskName: taskNameForSave(item) }))
    const normalizedItem = normalizeMonthPlanPeriods(form.planMonth, suggestedItems)[index]
    if (!normalizedItem) throw new Error('AI 优化结果不完整，请重新优化')
    const previewFields = buildOptimizePreview(sourceSnapshot, normalizedItem)
    optimizeSuggestion.value = { ...response, item: normalizedItem }
    latestDraftVersionNo.value = response.draftVersionNo
    optimizePreviewFields.value = previewFields
    optimizationTargetIndex.value = index
    selectedOptimizeFields.value = previewFields.filter((field) => field.changed).map((field) => field.key)
    optimizeDialogVisible.value = true
    await recordAiSuggestionActionApi(response.suggestionId, { actionCode: 'PREVIEW' })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'AI 单条优化失败'
    ElMessage.error(errorMessage.value)
  } finally {
    if (request === optimizationRequestVersion) optimizingIndex.value = -1
  }
}

async function applyOptimization() {
  if (!optimizeSuggestion.value || !optimizePreviewFields.value.length) return
  const task = form.items[optimizationTargetIndex.value]
  if (!task) { ElMessage.error('原计划明细已变化，请重新执行 AI 优化'); return }
  for (const field of selectedOptimizeFields.value) {
    const value = optimizePreviewFields.value.find((preview) => preview.key === field)?.suggestedValue
    if (field === 'performanceWeight') task.performanceWeight = editableNumber(value)
    else if (field === 'estimatedValue') task.estimatedValue = editableNumber(value)
    else task[field] = String(value ?? '') as never
  }
  await recordAiSuggestionActionApi(optimizeSuggestion.value.suggestionId, { actionCode: 'APPLY_FIELDS', appliedFields: selectedOptimizeFields.value })
  optimizeDialogVisible.value = false
  optimizationTargetIndex.value = -1
  optimizePreviewFields.value = []
  markDirty(); scheduleAutoSave(); ElMessage.success('AI 优化字段已应用')
}

async function ignoreOptimization() {
  if (optimizeSuggestion.value) await recordAiSuggestionActionApi(optimizeSuggestion.value.suggestionId, { actionCode: 'IGNORE' })
  optimizeDialogVisible.value = false
  optimizationTargetIndex.value = -1
  optimizePreviewFields.value = []
}

function normalizeOptimizeValue(value: unknown, field: OptimizableField) {
  if (field === 'performanceWeight' || field === 'estimatedValue') {
    const number = Number(value)
    return Number.isFinite(number) ? number : 0
  }
  return String(value ?? '').trim()
}

function sameOptimizableValues(left: EditableMonthPlanItem, right: EditableMonthPlanItem) {
  return optimizableFields.every(({ key }) => normalizeOptimizeValue(left[key], key) === normalizeOptimizeValue(right[key], key))
}

function buildOptimizePreview(source: EditableMonthPlanItem, suggestion: AiPlanItem): OptimizePreviewField[] {
  return optimizableFields.map(({ key, label }) => ({
    key,
    label,
    originalValue: source[key],
    suggestedValue: suggestion[key],
    changed: normalizeOptimizeValue(source[key], key) !== normalizeOptimizeValue(suggestion[key], key),
  }))
}

function displayOptimizeValue(value: unknown, field: OptimizableField) {
  if (field === 'performanceWeight') return value == null ? '未填写' : `${value}%`
  if (field === 'estimatedValue') return value == null ? '未填写' : `¥${Number(value ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  return String(value ?? '暂无')
}

async function stopOptimization() {
  optimizationRequestVersion += 1
  optimizingIndex.value = -1
  const confirmed = await stopOptimizationRequest()
  confirmed ? ElMessage.info('已停止本次生成') : ElMessage.warning('界面已停止等待，服务端取消未确认')
}

async function removeRow(index: number) {
  const row = form.items[index]
  if (!row) return
  try {
    await ElMessageBox.confirm('删除后将直接从数据库移除，确认继续？', '删除计划明细', { type: 'warning' })
  } catch {
    return
  }
  if (!row.id) {
    expandedTaskKeys.delete(row.uiKey)
    form.items.splice(index, 1)
    markDirty()
    autoSaveStatus.value = 'idle'
    ElMessage.success('已删除当前未保存明细')
    return
  }
  if (!form.id) return
  try {
    await deleteEmployeeMonthPlanItemApi(form.id, row.id)
    expandedTaskKeys.delete(row.uiKey)
    form.items.splice(index, 1)
    markDirty()
    autoSaveStatus.value = 'saved'
    lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    ElMessage.success('计划明细已删除')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '删除计划明细失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  }
}

async function navigateBackToList() {
  await router.push({ path: basePath.value, query: form.planMonth ? { month: form.planMonth } : undefined })
}

async function backToList() {
  if (!await confirmLeaveEditor()) return
  skipNextLeaveConfirm = true
  await navigateBackToList()
}

function consumeTopbarReturnFlag() {
  try {
    if (window.sessionStorage.getItem(MONTH_PLAN_TOPBAR_RETURN_KEY) !== '1') return false
    window.sessionStorage.removeItem(MONTH_PLAN_TOPBAR_RETURN_KEY)
    return true
  } catch {
    return false
  }
}

async function resolveMonthConversation() {
  if (!form.planMonth) return
  const request = ++aiConversationRequest
  const requestedMonth = form.planMonth
  try {
    const conversation = await resolveAiConversation('MONTH', requestedMonth)
    if (request === aiConversationRequest && requestedMonth === form.planMonth) {
      aiConversationId.value = conversation.id
    }
  } catch {
    if (request === aiConversationRequest) aiConversationId.value = undefined
  }
}

onMounted(async () => {
  await loadDetail()
  await revealAiAppliedChange()
})

watch(() => form.planMonth, () => {
  aiConversationId.value = undefined
  void resolveMonthConversation()
}, { immediate: true })

watch(form, () => {
  if (suppressChangeTracking || loading.value) return
  markDirty()
  scheduleAutoSave()
}, { deep: true })

onBeforeUnmount(() => {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  if (aiAppliedTimer) clearTimeout(aiAppliedTimer)
})

onBeforeRouteLeave(async (to) => {
  if (skipNextLeaveConfirm) {
    skipNextLeaveConfirm = false
    return true
  }
  if (to.path !== basePath.value) return true
  if (!consumeTopbarReturnFlag()) return true
  return await confirmLeaveEditor()
})
</script>

<style scoped>
.month-plan-editor-page { --month-submit-bar-clearance:88px; min-width:0; max-width:100%; padding-bottom:112px; overflow-x:hidden; background:#f6f7f5; }
.editor-context-actions { justify-content:flex-end; }
.rejection-guidance { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:16px 18px; border:1px solid #ecc5c5; border-left:4px solid #c65346; border-radius:8px; background:#fff6f6; }
.rejection-guidance div { display:grid; gap:5px; }
.rejection-guidance span { color:#b1473c; font-size:10px; font-weight:800; letter-spacing:.1em; }
.rejection-guidance strong { color:#563b32; font-size:13px; line-height:1.65; }
.rejection-guidance p { max-width:360px; margin:0; color:#80645e; font-size:12px; }
.editor-workbench { display:grid; grid-template-columns:minmax(0,1fr) minmax(320px,28%); gap:20px; align-items:start; min-width:0; max-width:100%; }
.editor-main,.task-editor-list { display:grid; gap:16px; min-width:0; }
.month-plan-identity,.task-editor-section { border:1px solid var(--line); border-radius:8px; background:#fff; }
.ai-floating-entry { position:fixed; right:max(16px,env(safe-area-inset-right)); bottom:max(16px,env(safe-area-inset-bottom)); z-index:20; display:inline-flex; align-items:center; gap:8px; height:44px; max-width:calc(100vw - 32px); padding:0 16px; border:0; border-radius:8px; color:#fff; background:#2563eb; box-shadow:0 10px 24px rgb(37 99 235 / 24%); font-size:14px; font-weight:750; cursor:pointer; transition:transform .18s ease, box-shadow .18s ease, background .18s ease; }
.ai-floating-entry:hover { background:#1d4ed8; box-shadow:0 16px 40px rgb(37 99 235 / 38%); transform:translateY(-1px); }
.ai-floating-entry:focus-visible { outline:3px solid rgb(37 99 235 / 28%); outline-offset:3px; }
.ai-floating-entry .el-icon { font-size:17px; }
.month-plan-identity { padding:20px; }
.section-kicker { display:block; color:#2d776c; font-size:11px; font-weight:800; letter-spacing:0; }
.month-plan-identity :deep(.el-form-item:last-child) { margin-bottom:0; }
.task-editor-section { min-width:0; max-width:100%; padding:16px; background:#f5f7f4; }
.task-editor-section.is-ai-applied { border-color:#2d776c; box-shadow:0 0 0 4px rgb(45 119 108 / 14%); }
.task-editor-toolbar { display:flex; align-items:flex-end; justify-content:space-between; gap:14px; margin-bottom:12px; }
.task-editor-toolbar h2 { margin:4px 0 0; color:var(--ink); font-size:18px; letter-spacing:0; }
.task-editor-toolbar h2 small { color:#697b75; font-size:12px; font-weight:650; }
.task-editor-toolbar p { margin:5px 0 0; color:var(--muted); font-size:12px; }
.task-section-actions { display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:8px; }
.month-clear-button { border-color:#ead2c8; color:#9a513c; background:#fff8f5; }
.month-clear-button:hover:not(.is-disabled) { border-color:#c76851; color:#88412f; background:#fff2ec; }
.task-view-switch { flex:0 0 auto; padding:3px; border:1px solid #d8e2dd; border-radius:7px; background:#eaf0ec; }
.task-view-switch :deep(.el-radio-button__inner) { display:inline-flex; align-items:center; gap:6px; min-height:32px; padding:7px 12px; border:0 !important; border-radius:5px !important; color:#586b65; background:transparent; box-shadow:none !important; font-size:12px; font-weight:700; }
.task-view-switch :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) { color:#145f54; background:#fff; box-shadow:0 1px 4px rgb(48 73 64 / 14%) !important; }
.task-view-switch :deep(.el-icon) { font-size:15px; }
.task-editor-list { gap:12px; }
.compact-task-table { --task-columns:44px minmax(190px,1.55fr) 76px minmax(250px,1.7fr) minmax(160px,1.15fr) 108px 82px; width:100%; min-width:0; border:1px solid #cfdad4; }
.compact-task-table__head,.compact-task-row { display:grid; grid-template-columns:var(--task-columns); gap:0; min-width:0; }
.compact-task-table__head { align-items:stretch; min-height:46px; border-bottom:1px solid #c7d4cd; color:#40564f; background:#edf2ee; font-size:16px; font-weight:800; }
.compact-task-table__head > span { display:grid; min-width:0; place-items:center; padding:5px 6px; border-right:1px solid #c7d4cd; text-align:center; line-height:1.25; }
.compact-task-table__head > span:last-child { border-right:0; }
.compact-task-row { min-height:48px; align-items:stretch; border-bottom:1px solid #d8e1dc; transition:background-color .16s ease; }
.compact-task-row:last-child { border-bottom:0; }
.compact-task-row:hover,.compact-task-row:focus-within { background:rgb(255 255 255 / 58%); }
.compact-task-cell { min-width:0; padding:6px; border-right:1px solid #d8e1dc; }
.compact-task-cell:last-child { border-right:0; }
.compact-task-cell label,.compact-task-cell--content,.compact-task-cell--weight,.compact-task-cell--value { display:grid; min-width:0; align-content:center; gap:3px; margin:0; }
.compact-task-cell--standard { display:grid; align-content:center; gap:6px; }
.compact-task-cell--standard > label + label { padding-top:6px; border-top:1px dashed #d8e1dc; }
.compact-task-cell--index { display:grid; place-items:center; }
.compact-task-cell--period { display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); align-content:center; align-items:center; gap:3px; }
.compact-task-cell--period .compact-field-label { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
.period-separator { color:#71847d; font-size:11px; }
.compact-task-cell--actions { display:flex; flex-wrap:wrap; align-content:center; justify-content:center; gap:0; }
.compact-task-cell--actions :deep(.el-button) { width:26px; height:28px; }
.compact-task-cell--actions :deep(.el-button + .el-button) { margin-left:0; }
.compact-field-label { color:#667a73; font-size:9px; font-weight:750; line-height:1.2; }
.compact-task-cell--content > .compact-field-label,.compact-task-cell--weight > .compact-field-label,.compact-task-cell--value > .compact-field-label { display:none; }
.sheet-row-index { display:inline-grid; width:30px; height:30px; place-items:center; border-radius:6px; color:#1d7468; background:#eef7f3; font:800 12px/1 "IBM Plex Mono",monospace; }
.sheet-field { min-width:0; }
.month-task-content-field { min-height:30px; }
.month-task-content-preview { min-height:30px; padding:5px 0; color:#263d36; font-size:13px; line-height:1.4; cursor:text; }
.month-task-content-preview :deep(strong) { color:#172f27; font-weight:850; }
.month-task-content-field.is-editing .month-task-content-preview,
.month-task-content-field.is-empty .month-task-content-preview { display:none; }
.compact-task-table :deep(.el-input__wrapper),.compact-task-table :deep(.el-textarea__inner) { --el-input-bg-color:transparent; border-radius:0; background:transparent; box-shadow:none; }
.compact-task-table :deep(.el-input__wrapper) { min-height:30px; padding:0; }
.compact-task-table :deep(.el-input__inner),.compact-task-table :deep(.el-textarea__inner) { color:#263d36; font-size:13px; }
.compact-task-table :deep(.el-textarea__inner) { min-height:30px !important; padding:5px 0; overflow:hidden; resize:none; line-height:1.4; }
.compact-task-cell:focus-within { background:rgb(67 136 120 / 5%); }
.compact-task-cell:has(.sheet-field.has-error) { background:rgb(198 83 70 / 4%); box-shadow:inset 0 0 0 1px #c65346; }
.compact-task-table :deep(.el-input-number__increase),.compact-task-table :deep(.el-input-number__decrease) { display:none; }
.compact-task-table :deep(.el-input-number .el-input__inner) { padding:0; text-align:left; }
.compact-task-table :deep(.el-input-number .el-input__inner::placeholder) { color:#9aaba4; opacity:1; }
.compact-task-table :deep(.el-date-editor .el-input__prefix),.compact-task-table :deep(.el-date-editor .el-input__suffix) { opacity:.32; }
.compact-task-table .sheet-field:focus-within :deep(.el-date-editor .el-input__prefix),.compact-task-table .sheet-field:focus-within :deep(.el-date-editor .el-input__suffix) { opacity:1; }
.compact-task-table :deep(.el-date-editor),.compact-task-table :deep(.el-input-number) { width:100%; }
.compact-task-table :deep(.el-input-number .el-input__inner) { text-align:right; }
.add-task-strip { width:100%; margin-top:10px; padding:10px; border:1px dashed #92aaa2; border-radius:6px; color:#2d776c; background:transparent; font-size:12px; font-weight:700; cursor:pointer; transition:.18s ease; }
.add-task-strip:hover { border-color:var(--blue); background:#fff; }
.add-task-strip:disabled { opacity:.5; cursor:not-allowed; }
.validation-aside { position:fixed; right:34px; bottom:0; left:302px; z-index:14; display:grid; grid-template-columns:minmax(150px,.65fr) minmax(260px,1.35fr) auto minmax(280px,.8fr); gap:18px; align-items:center; width:auto; max-width:var(--content-max,1540px); min-height:68px; margin-inline:auto; padding:10px 20px max(10px,env(safe-area-inset-bottom)); border-top:1px solid #cbd7d1; background:rgb(255 253 248 / 96%); box-shadow:0 -8px 24px rgb(35 57 49 / 9%); backdrop-filter:blur(14px); }
.submit-save-state { display:flex; min-width:0; align-items:center; gap:9px; }
.submit-save-state > span { width:9px; height:9px; flex:0 0 auto; border-radius:50%; background:#a5b1ad; }
.submit-save-state > span.is-saving { background:#c88738; animation:pulse 1.1s infinite; }
.submit-save-state > span.is-saved { background:#3b8f77; }
.submit-save-state > span.is-error { background:#c65346; }
.submit-save-state__copy { display:grid; min-width:0; flex:1; gap:2px; }
.submit-save-state__copy strong { overflow:hidden; color:#40544e; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.submit-save-state__copy small { overflow:hidden; color:var(--muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.submit-readiness { display:grid; min-width:0; gap:6px; }
.submit-readiness__head { display:flex; min-width:0; align-items:center; gap:9px; }
.submit-readiness__head > span { color:#52645f; font-size:11px; font-weight:750; white-space:nowrap; }
.submit-readiness__head > strong { color:#2d776c; font:800 17px/1 "IBM Plex Mono",monospace; }
.submit-issue-link { padding:0; border:0; color:#b1473c; background:transparent; font-size:11px; font-weight:750; white-space:nowrap; cursor:pointer; }
.submit-issue-link:hover { text-decoration:underline; text-underline-offset:3px; }
.submit-readiness :deep(.el-progress-bar__outer) { background:#e7ece9; }
.submit-metrics { display:flex; align-items:center; color:#667a73; font-size:11px; white-space:nowrap; }
.submit-metrics > span { padding:0 13px; }
.submit-metrics > span + span { border-left:1px solid #d9e1dd; }
.submit-metrics strong { color:#263d36; font:800 14px/1 "IBM Plex Mono",monospace; }
.issue-panel { margin-top:18px; }
.issue-title { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.issue-title strong { color:var(--ink); font-size:13px; }
.issue-list { display:grid; gap:12px; margin-top:12px; }
.issue-group { display:grid; gap:7px; }
.issue-group header { display:flex; align-items:center; justify-content:space-between; gap:10px; padding-bottom:5px; border-bottom:1px solid #edf0eb; }
.issue-group header strong { color:#52645f; font-size:11px; }
.issue-group header span { color:var(--muted); font-size:10px; }
.issue-list button { display:flex; gap:8px; width:100%; padding:9px 10px; border:1px solid #f0d2cc; border-radius:8px; color:#77483f; background:#fff8f6; text-align:left; font-size:11px; line-height:1.45; cursor:pointer; }
.issue-list button:hover { border-color:#c65346; }
.issue-list button span { flex:0 0 17px; height:17px; border-radius:50%; color:#fff; background:#c65346; text-align:center; font-weight:800; }
.issue-list .issue-disclosure { justify-self:start; width:auto; padding:0; border:0; background:transparent; }
.all-clear { margin:12px 0 0; color:#477566; font-size:11px; line-height:1.6; }
.month-aside-ai-panel { margin-top:18px; border-radius:8px; box-shadow:none; }
.month-aside-ai-panel :deep(.ai-review__head) { align-items:flex-start; flex-direction:column; gap:10px; }
.month-aside-ai-panel :deep(.ai-review__head-actions) { width:100%; justify-content:space-between; }
.month-aside-ai-panel :deep(.ai-review__head-actions .el-button) { width:100%; }
.month-aside-ai-panel :deep(.ai-review__title-row h3) { font-size:14px; }
.month-aside-ai-panel :deep(.ai-review__coverage) { overflow:auto; }
.validation-aside__actions { display:grid; grid-template-columns:repeat(3,minmax(92px,1fr)); gap:10px; }
.validation-aside__actions .el-button { width:100%; min-height:40px; margin:0; }
.month-plan-editor-page .editor-workbench { grid-template-columns:1fr; }
.month-plan-editor-page .ai-floating-entry { display:inline-flex; bottom:var(--month-submit-bar-clearance); }
.assist-drawer-stack { display:grid; gap:14px; }
.assist-tool-card { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:14px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.assist-tool-card > div { display:grid; min-width:0; gap:4px; }
.assist-tool-card strong { color:var(--ink); font-size:13px; }
.assist-tool-card span { color:var(--muted); font-size:12px; line-height:1.5; }
.assist-tool-card--plain { display:grid; }
@keyframes pulse { 50% { opacity:.35; } }
@media (prefers-reduced-motion:reduce) { .submit-save-state > span.is-saving { animation:none; } }
@media (prefers-reduced-motion:reduce) { .ai-floating-entry,.task-editor-section { transition:none; } }
.month-plan-ai-workspace {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(320px, .9fr);
  gap: 16px;
  align-items: stretch;
}

.month-plan-ai-workspace > :deep(.dashboard-section) {
  height: 100%;
}

.month-plan-rules .rule-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 8px 18px;
  margin-bottom: 0;
}

.optimize-fields { display:grid; gap:10px; margin-top:16px; }
.optimize-field { display:grid; grid-template-columns:120px 1fr 1fr; gap:10px; align-items:start; border:1px solid #e5e7eb; border-radius:8px; padding:10px; }
.optimize-field.is-changed { border-color:#f5c26b; background:#fffaf0; }
.optimize-field-heading { display:flex; flex-direction:column; align-items:flex-start; gap:6px; }
.optimize-field > div { background:#f8fafc; border-radius:8px; padding:8px; min-height:54px; }
.optimize-field > .optimize-field-heading { background:transparent; padding:0; }
.optimize-field span { color:#64748b; font-size:11px; }
.optimize-field p { margin:4px 0 0; color:#334155; font-size:13px; line-height:1.6; white-space:pre-wrap; word-break:break-word; }

@media (max-width: 1180px) {
  .month-plan-ai-workspace {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 1180px) {
  .editor-workbench { grid-template-columns: 1fr; }
  .month-plan-editor-page { padding-bottom:150px; }
  .validation-aside { grid-template-columns:minmax(150px,.7fr) minmax(250px,1.3fr) auto; gap:8px 16px; padding-inline:18px; }
  .submit-readiness { grid-column:1 / 3; grid-row:1; }
  .submit-metrics { grid-column:3; grid-row:1; }
  .submit-save-state { grid-column:1; grid-row:2; }
  .validation-aside__actions { grid-column:2 / 4; grid-row:2; justify-self:end; width:min(100%,520px); }
  .compact-task-table__head { display:none; }
  .compact-task-row { grid-template-columns:44px minmax(0,1.3fr) 82px minmax(0,1.4fr) 86px; }
  .compact-task-cell--index { grid-column:1; grid-row:1 / 3; }
  .compact-task-cell--content { grid-column:2; grid-row:1; }
  .compact-task-cell--weight { grid-column:3; grid-row:1; }
  .compact-task-cell--standard { grid-column:4; grid-row:1 / 3; }
  .compact-task-cell--period { grid-column:2; grid-row:2; }
  .compact-task-cell--value { grid-column:3; grid-row:2; }
  .compact-task-cell--actions { grid-column:5; grid-row:1 / 3; }
  .compact-task-cell--content > .compact-field-label,.compact-task-cell--weight > .compact-field-label,.compact-task-cell--value > .compact-field-label { display:block; }
}

@media (max-width: 980px) {
  .editor-workbench { grid-template-columns: 1fr; }
  .validation-aside { right:16px; left:16px; }
  .assist-tool-card { align-items:stretch; flex-direction:column; }
  .task-editor-toolbar { align-items:flex-start; flex-direction:column; }
  .task-section-actions { justify-content:flex-start; width:100%; }
}

@media (max-width: 720px) {
  .month-plan-editor-page { --month-submit-bar-clearance:128px; padding-bottom:132px; }
  .month-plan-identity,
  .task-editor-section {
    padding: 16px;
  }

  .validation-aside { right:16px; left:16px; grid-template-columns:auto minmax(0,1fr) auto; gap:8px 10px; min-height:0; padding:9px 12px max(9px,env(safe-area-inset-bottom)); }
  .submit-save-state { grid-column:1; grid-row:1; gap:6px; }
  .submit-save-state__copy small { display:none; }
  .submit-readiness { grid-column:2; grid-row:1; gap:4px; }
  .submit-readiness__head { gap:6px; }
  .submit-readiness__head > span { display:none; }
  .submit-readiness__head > strong { font-size:14px; }
  .submit-issue-link { font-size:10px; }
  .submit-metrics { grid-column:3; grid-row:1; font-size:10px; }
  .submit-metrics > span { padding:0 5px; }
  .submit-metrics strong { font-size:12px; }
  .validation-aside__actions { grid-column:1 / -1; grid-row:2; width:100%; }
  .validation-aside__actions { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .validation-aside__actions .el-button { min-height:36px; }

  .ai-floating-entry {
    right: 16px;
    height: 42px;
    padding: 0 14px;
  }

  .month-plan-rules .rule-list,
  .optimize-field {
    grid-template-columns: 1fr;
  }

  .task-editor-toolbar,
  .task-section-actions {
    align-items:stretch;
    flex-direction:column;
  }

  .task-section-actions :deep(.el-button-group),
  .task-section-actions > :deep(.el-button) {
    width:100%;
  }

  .task-section-actions :deep(.el-button-group .el-button) {
    width:50%;
  }

  .task-view-switch {
    display:flex;
    width:100%;
  }

  .task-view-switch :deep(.el-radio-button) {
    flex:1 1 50%;
  }

  .task-view-switch :deep(.el-radio-button__inner) {
    justify-content:center;
    width:100%;
  }

  .compact-task-row {
    grid-template-columns:86px minmax(0,1fr) 86px;
  }

  .compact-task-cell--index { grid-column:1; grid-row:1; }
  .compact-task-cell--content { grid-column:2; grid-row:1; }
  .compact-task-cell--actions { grid-column:3; grid-row:1; }
  .compact-task-cell--weight { grid-column:1; grid-row:2; }
  .compact-task-cell--period { grid-column:2; grid-row:2; }
  .compact-task-cell--value { grid-column:3; grid-row:2; }
  .compact-task-cell--standard { grid-column:1 / -1; grid-row:3; }
}
</style>
