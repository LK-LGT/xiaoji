<template>
  <section class="month-ai-workspace" :class="{ 'history-collapsed': historyCollapsed }">
    <header class="workspace-header">
      <div class="workspace-identity">
        <el-tooltip content="返回月计划" placement="bottom">
          <el-button :icon="ArrowLeft" circle aria-label="返回月计划" @click="returnToEditor" />
        </el-tooltip>
        <div>
          <strong>月计划 AI 助手</strong>
          <span>{{ plan?.planMonth || '-' }} · {{ plan?.employeeName || '当前员工' }}</span>
        </div>
      </div>
      <div class="workspace-tools">
        <el-tag v-if="contextInfo?.generationProfileLabel" size="small" type="success" effect="plain">{{ contextInfo.generationProfileLabel }}</el-tag>
        <span v-if="plan" class="workspace-safety-note">建议不会自动提交</span>
        <el-tooltip v-if="historyCollapsed" content="新对话" placement="bottom">
          <el-button :icon="Plus" circle aria-label="新对话" :disabled="busy || conversation.loading.value" @click="createConversation" />
        </el-tooltip>
        <el-tooltip :content="historyCollapsed ? '展开历史对话' : '收起历史对话'" placement="bottom">
          <el-button :icon="historyCollapsed ? Expand : Fold" circle :aria-label="historyCollapsed ? '展开历史对话' : '收起历史对话'" class="desktop-history-toggle" @click="historyCollapsed = !historyCollapsed" />
        </el-tooltip>
        <el-tooltip content="历史对话" placement="bottom">
          <el-button :icon="Clock" circle aria-label="历史对话" class="mobile-history-toggle" @click="mobileHistoryOpen = true" />
        </el-tooltip>
      </div>
    </header>

    <aside class="conversation-history-rail" :class="{ 'is-mobile-open': mobileHistoryOpen }">
      <header>
        <div><strong>对话记录</strong><span>{{ conversationList.length }}</span></div>
        <div class="history-actions">
          <el-tooltip content="新对话" placement="bottom">
            <el-button :icon="Plus" text circle aria-label="新对话" :disabled="busy || conversation.loading.value" @click="createConversation" />
          </el-tooltip>
          <el-button :icon="Close" text circle class="mobile-history-close" aria-label="关闭历史对话" @click="mobileHistoryOpen = false" />
        </div>
      </header>
      <el-skeleton v-if="conversation.historyLoading.value" :rows="5" animated />
      <nav v-else aria-label="历史对话">
        <button
          v-for="item in conversationList"
          :key="item.id"
          type="button"
          :class="{ active: conversation.viewingConversation.value?.id === item.id }"
          @click="selectConversation(item)"
        >
          <strong>{{ localizedConversationTitle(item.title) }}</strong>
          <span><i v-if="item.status === 'ACTIVE'" />{{ item.status === 'ACTIVE' ? '当前' : formatConversationTime(item.endedAt || item.updatedAt) }}</span>
        </button>
      </nav>
      <el-empty v-if="!conversation.historyLoading.value && !conversationList.length" description="暂无历史对话" :image-size="48" />
    </aside>
    <button v-if="mobileHistoryOpen" type="button" class="history-mask" aria-label="关闭历史对话" @click="mobileHistoryOpen = false" />

    <main class="workspace-main">
      <el-skeleton v-if="loading" :rows="8" animated />
      <el-result v-else-if="errorMessage && !plan" icon="error" title="月计划加载失败" :sub-title="errorMessage">
        <template #extra><el-button type="primary" @click="loadWorkspace">重试</el-button></template>
      </el-result>

      <MonthPlanAiDiff
        v-else-if="previewSuggestion"
        :visible="true"
        :operation-mode="previewMode"
        :suggestion="previewSuggestion"
        :current-items="currentItems"
        @close="previewSuggestion = undefined"
        @ignore="ignorePreview"
        @apply="applySuggestion"
      />

      <template v-else-if="plan">
        <div class="workspace-notices">
          <el-alert v-if="!editable" type="warning" :closable="false" show-icon title="当前月计划不是可编辑状态，仅可查看历史对话。" />
          <el-alert v-else-if="contextInfo && !contextInfo.enabled" type="warning" :closable="false" show-icon :title="contextInfo.notice" />
          <el-alert v-if="errorMessage || conversation.errorMessage.value" class="workspace-error" type="error" :closable="false" show-icon>
            <template #title>
              <span>{{ errorMessage || conversation.errorMessage.value }}</span>
              <el-button link type="primary" @click="reloadConversation">重试</el-button>
            </template>
          </el-alert>
        </div>

        <PlanAiChatThread
            :messages="conversation.messages.value"
            :period-key="plan.planMonth"
            :plan-state="planState"
            :starters="starters"
            :latest-draft-version-no="latestDraftVersionNo"
            :loading="conversation.loading.value"
            :loading-older="conversation.loadingOlder.value"
            :has-more="conversation.hasMore.value"
            @load-older="conversation.loadOlder"
            @use-starter="useStarter"
            @preview-result="openPreview"
            @continue-result="continueSuggestion"
          @retry="retryMessage"
        />

        <div v-if="conversation.readonlyView.value" class="readonly-composer">
          <span>历史对话仅供查看，不会进入当前记忆。</span>
          <el-button type="primary" plain @click="returnToActiveConversation">返回当前对话</el-button>
        </div>
        <div v-else class="workspace-composer">
          <div class="composer-options" aria-label="生成内容设置">
            <el-checkbox v-model="conciseOutput" :disabled="busy || !editable">精简生成</el-checkbox>
            <span>减少背景和重复说明，保留任务、交付物、质量、日期、权重与预估价值</span>
          </div>
          <div class="composer-example-row">
            <PlanAiExampleInput v-model="generationExample" :disabled="busy || !editable" />
          </div>
          <div class="composer-surface" :class="{ 'is-busy': busy }">
            <el-input
              ref="composerInput"
              :model-value="composer"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 7 }"
              maxlength="5000"
              resize="none"
              :placeholder="composerPlaceholder"
              :disabled="busy || !canSend"
              @update:model-value="changeComposer"
              @keydown.ctrl.enter.prevent="sendMessage"
            />
            <div class="composer-footer">
              <div class="composer-reference-ledger" aria-live="polite">
                <MonthPlanAiContextPopover
                  :context-info="contextInfo"
                  :selected-context-types="selectedContextTypes"
                  :selected-history-months="selectedHistoryMonths"
                  :selected-knowledge-types="selectedKnowledgeTypes"
                  :job-description="jobDescription"
                  :disabled="busy || !editable"
                  :refreshing="contextRefreshing"
                  @update:selected-context-types="selectedContextTypes = $event"
                  @update:selected-history-months="selectedHistoryMonths = $event"
                  @update:selected-knowledge-types="selectedKnowledgeTypes = $event"
                  @update:job-description="jobDescription = $event"
                  @refresh="refreshContext"
                />
                <span>{{ referenceLedger }}</span>
              </div>
              <el-tooltip :content="busy ? '停止生成' : '发送'" placement="top">
                <el-button v-if="busy" class="composer-command" type="danger" :icon="VideoPause" circle aria-label="停止生成" @click="stopGeneration" />
                <el-button v-else class="composer-command" type="primary" :icon="Promotion" circle aria-label="发送" :disabled="!composer.trim() || !canSend" @click="sendMessage" />
              </el-tooltip>
            </div>
          </div>
        </div>
      </template>
    </main>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Clock, Close, Expand, Fold, Plus, Promotion, VideoPause } from '@element-plus/icons-vue'
import { createEmployeeMonthPlanDraftApi, getEmployeeMonthPlanDetailApi, saveEmployeeMonthPlanDraftApi, type EmployeeMonthPlanDetailResp } from '@/api/employee'
import { currentMonth } from '@/api/performance'
import type { AiConversation, AiConversationMessage } from '@/api/aiConversations'
import {
  MONTH_PLAN_CONTEXT_TYPES,
  aiRequestId,
  generateMonthPlanApi,
  getMonthPlanAiContextApi,
  recordAiSuggestionActionApi,
  type AiContextResponse,
  type AiGenerationExample,
  type AiGenerateResponse,
  type AiPlanItem,
  type MonthPlanAiOperationMode,
  type MonthPlanContextType,
} from '@/api/employeeAi'
import { listPlanDraftVersionsApi } from '@/api/planDraftVersions'
import { useCancelableAiRequest } from '@/composables/useCancelableAiRequest'
import { usePersonalMonthPlanRoutes } from '@/composables/usePersonalMonthPlanRoutes'
import { usePlanAiConversation } from '@/composables/usePlanAiConversation'
import { normalizeMonthPlanPeriods } from '@/utils/monthPlanPeriod'
import { useAuthStore } from '@/stores/auth'
import MonthPlanAiContextPopover from './components/MonthPlanAiContextPopover.vue'
import MonthPlanAiDiff, { type AiPlanDraftItem } from './components/MonthPlanAiDiff.vue'
import PlanAiChatThread, { type MonthPlanStarter } from './components/PlanAiChatThread.vue'
import PlanAiExampleInput from './components/PlanAiExampleInput.vue'

type PreviewMode = Exclude<MonthPlanAiOperationMode, 'AUTO' | 'CLARIFY'>
type WorkspaceMonthPlan = Omit<EmployeeMonthPlanDetailResp, 'id'> & { id?: number }

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const { basePath, editPath } = usePersonalMonthPlanRoutes()
const plan = ref<WorkspaceMonthPlan>()
const loading = ref(true)
const errorMessage = ref('')
const contextInfo = ref<AiContextResponse>()
const contextRefreshing = ref(false)
const selectedContextTypes = ref<MonthPlanContextType[]>([...MONTH_PLAN_CONTEXT_TYPES])
const selectedHistoryMonths = ref<string[]>([])
const selectedKnowledgeTypes = ref<string[]>([])
const contextSelectionInitialized = ref(false)
const conciseOutput = ref(false)
const generationExample = ref<AiGenerationExample>()
const jobDescription = ref('')
const latestDraftVersionNo = ref<number>()
const historyCollapsed = ref(false)
const mobileHistoryOpen = ref(false)
const composer = ref('')
const hintedText = ref('')
const operationHint = ref<MonthPlanAiOperationMode>('AUTO')
const composerInput = ref<{ focus: () => void }>()
const previewSuggestion = ref<AiGenerateResponse>()
const { running: busy, run: runAiRequest, stop: stopAiRequest } = useCancelableAiRequest()
const planMonth = computed(() => plan.value?.planMonth || '')
const conversation = usePlanAiConversation('MONTH', planMonth)

const editable = computed(() => plan.value?.status === 'draft' || plan.value?.status === 'rejected')
const isLeaderPlan = computed(() => contextInfo.value?.generationProfile === 'LEADER_DELIVERY_MANAGEMENT'
  || basePath.value.startsWith('/leader/'))
const canSend = computed(() => editable.value && Boolean(conversation.activeConversation.value) && contextInfo.value?.enabled !== false)
const planState = computed<'EMPTY' | 'EXISTING' | 'REJECTED'>(() => {
  if (plan.value?.status === 'rejected') return 'REJECTED'
  return plan.value?.items.length ? 'EXISTING' : 'EMPTY'
})
const currentItems = computed<AiPlanDraftItem[]>(() => mapCurrentItems())
const referenceLedger = computed(() => `参考 ${selectedContextTypes.value.length} 项 · 历史 ${selectedHistoryMonths.value.length} 月 · 知识 ${selectedKnowledgeTypes.value.length} 类`)
const previewMode = computed<PreviewMode>(() => {
  const mode = previewSuggestion.value?.effectiveOperationMode
  return mode && !['AUTO', 'CLARIFY'].includes(mode) ? mode as PreviewMode : 'GENERATE'
})
const conversationList = computed(() => {
  const rows = [...conversation.conversations.value]
  const active = conversation.activeConversation.value
  if (active && !rows.some((item) => item.id === active.id)) rows.unshift(active)
  return rows
})
const composerPlaceholder = computed(() => {
  if (planState.value === 'REJECTED') return '继续说明如何处理审批反馈'
  if (isLeaderPlan.value) return planState.value === 'EMPTY'
    ? '描述本月项目交付、关键里程碑和管理重点'
    : '继续补充交付目标、协同要求或风险控制重点'
  return planState.value === 'EMPTY'
    ? '描述本月最重要的目标和具体执行任务'
    : '继续补充任务要求或调整当前计划'
})
const starters = computed<MonthPlanStarter[]>(() => {
  if (isLeaderPlan.value) {
    if (planState.value === 'REJECTED') {
      return [
        { label: '按审批意见修改', text: `请根据审批意见调整项目交付与管理计划：${plan.value?.approvalComment || ''}`, operationMode: 'REVISE_REJECTION' },
        { label: '补强落地与验收', text: '完善关键交付、项目落地结果和可核验验收出口', operationMode: 'OPTIMIZE' },
        { label: '补充风险与协同', text: '结合审批意见补充资源协调、跨团队协同和风险闭环', operationMode: 'SUPPLEMENT' },
      ]
    }
    if (planState.value === 'EMPTY') {
      return [
        { label: '生成领导月计划', text: '围绕项目交付、关键里程碑、资源协调和风险控制生成本月管理计划', operationMode: 'GENERATE' },
        { label: '围绕项目落地起草', text: '根据部门目标和项目资料规划本月需要落地的阶段成果', operationMode: 'GENERATE' },
        { label: '规划管理重点', text: '结合团队职责规划本月交付管理、协同机制和风险治理重点', operationMode: 'GENERATE' },
      ]
    }
    return [
      { label: '优化交付结构', text: '将当前计划优化为清晰的项目交付、里程碑和管理结果', operationMode: 'OPTIMIZE' },
      { label: '补充风险与协同', text: '补充当前计划遗漏的资源协调、跨团队协同和风险闭环', operationMode: 'SUPPLEMENT' },
      { label: '完善落地验收', text: '完善各项交付的落地结果和可核验验收出口', operationMode: 'OPTIMIZE' },
    ]
  }
  if (planState.value === 'REJECTED') {
    return [
      { label: '按审批意见修改', text: `请根据审批意见修改当前计划：${plan.value?.approvalComment || ''}`, operationMode: 'REVISE_REJECTION' },
      { label: '完善交付与质量', text: '完善每项任务的交付物和可核验质量标准', operationMode: 'OPTIMIZE' },
      { label: '检查遗漏任务', text: '结合审批意见检查并补充遗漏的任务', operationMode: 'SUPPLEMENT' },
    ]
  }
  if (planState.value === 'EMPTY') {
    return [
      { label: '生成本月计划', text: '根据我的岗位和本月目标生成一份完整月计划', operationMode: 'GENERATE' },
      { label: '围绕部门目标起草', text: '根据部门目标拆解我本月需要完成的任务', operationMode: 'GENERATE' },
      { label: '从岗位职责规划', text: '结合岗位职责规划本月重点工作和交付物', operationMode: 'GENERATE' },
    ]
  }
  return [
    { label: '优化当前计划', text: '优化当前计划，让任务、交付物和质量标准更具体', operationMode: 'OPTIMIZE' },
    { label: '补充遗漏任务', text: '检查当前计划并补充遗漏的重要任务', operationMode: 'SUPPLEMENT' },
    { label: '完善验收标准', text: '完善每项任务的交付物和可核验质量标准', operationMode: 'OPTIMIZE' },
  ]
})

function planId() {
  const raw = Array.isArray(route.params.id) ? route.params.id[0] : route.params.id
  const value = Number(raw)
  return Number.isInteger(value) && value > 0 ? value : undefined
}

function queryMonth() {
  const raw = route.query.planMonth || route.query.month
  const value = Array.isArray(raw) ? raw[0] : raw
  return typeof value === 'string' && /^\d{4}-\d{2}$/.test(value) ? value : currentMonth()
}

function createEmptyWorkspacePlan(planMonth: string): WorkspaceMonthPlan {
  return {
    planMonth,
    employeeName: authStore.user?.realName || '当前员工',
    departmentName: authStore.user?.departmentName || '',
    projectName: '',
    positionName: '',
    status: 'draft',
    resultStatus: 'not_submitted',
    updatedAt: '',
    approvalComment: '',
    items: [],
    deliverables: [],
    resultSummary: {
      submittedCount: 0,
      confirmedCount: 0,
      rejectedCount: 0,
      latestVersion: '',
      overallCompletionRate: 0,
    },
    confirmRecords: [],
  }
}

async function loadWorkspace() {
  const id = planId()
  loading.value = true
  errorMessage.value = ''
  contextSelectionInitialized.value = false
  try {
    plan.value = id ? await getEmployeeMonthPlanDetailApi(id) : createEmptyWorkspacePlan(queryMonth())
    jobDescription.value = plan.value.positionName || ''
    const [context, versions] = await Promise.all([
      getMonthPlanAiContextApi(plan.value.planMonth),
      listPlanDraftVersionsApi('MONTH', plan.value.planMonth),
    ])
    applyContextInfo(context)
    latestDraftVersionNo.value = versions[0]?.versionNo
    if (!conversation.activeConversation.value) await conversation.resolveActive()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '月计划加载失败'
  } finally {
    loading.value = false
  }
}

async function refreshContext() {
  if (!plan.value || contextRefreshing.value) return
  contextRefreshing.value = true
  try {
    const context = await getMonthPlanAiContextApi(
      plan.value.planMonth,
      composer.value,
      [...selectedKnowledgeTypes.value],
    )
    applyContextInfo(context)
  } catch (error) {
    const detail = error instanceof Error ? `：${error.message}` : ''
    ElMessage.error(`参考资料刷新失败${detail}`)
  } finally {
    contextRefreshing.value = false
  }
}

function applyContextInfo(context: AiContextResponse) {
  contextInfo.value = context
  const availableMonths = (context.historyMonthOptions || [])
    .filter((item) => item.available)
    .map((item) => item.month)
  const availableKnowledgeTypes = (context.knowledgeTypeOptions || [])
    .filter((item) => item.available)
    .map((item) => item.type)
  if (!contextSelectionInitialized.value) {
    selectedHistoryMonths.value = availableMonths
    selectedKnowledgeTypes.value = availableKnowledgeTypes
    contextSelectionInitialized.value = true
  } else {
    selectedHistoryMonths.value = selectedHistoryMonths.value.filter((month) => availableMonths.includes(month))
    selectedKnowledgeTypes.value = selectedKnowledgeTypes.value.filter((type) => availableKnowledgeTypes.includes(type))
  }
  const selectedParents = new Set(selectedContextTypes.value)
  if (selectedHistoryMonths.value.length) selectedParents.add('HISTORY_PLANS')
  else selectedParents.delete('HISTORY_PLANS')
  if (selectedKnowledgeTypes.value.length) selectedParents.add('ENTERPRISE_KNOWLEDGE')
  else selectedParents.delete('ENTERPRISE_KNOWLEDGE')
  selectedContextTypes.value = MONTH_PLAN_CONTEXT_TYPES.filter((type) => selectedParents.has(type))
}

function mapCurrentItems(): AiPlanDraftItem[] {
  return (plan.value?.items || []).map((item) => ({
    id: item.id,
    workType: 'UNKNOWN',
    taskName: item.taskName || '',
    taskContent: item.taskContent || '',
    deliverable: item.deliverable || '',
    quality: item.quality || '',
    estimatedStartDate: item.estimatedStartDate || '',
    estimatedEndDate: item.estimatedEndDate || '',
    performanceWeight: Number(item.performanceWeight || 0),
    estimatedValue: Number(item.estimatedValue || 0),
  }))
}

function stripIds(items: AiPlanDraftItem[]): AiPlanItem[] {
  return items.map(({ id: _id, ...item }) => item)
}

function useStarter(starter: MonthPlanStarter) {
  composer.value = starter.text
  hintedText.value = starter.text
  operationHint.value = starter.operationMode
  focusComposer()
}

function changeComposer(value: string) {
  composer.value = value
  if (value !== hintedText.value) operationHint.value = 'AUTO'
}

async function sendMessage() {
  const text = composer.value.trim()
  if (!text || busy.value || !canSend.value || !plan.value) return
  await sendQuickGenerate(text)
}

async function sendQuickGenerate(text: string) {
  const active = conversation.activeConversation.value
  if (!text || !active || busy.value || !canSend.value || !plan.value) return
  const requestMode = operationHint.value
  const optimistic: AiConversationMessage = {
    id: -Date.now(), conversationId: active.id, role: 'USER', messageType: 'TEXT',
    operationCode: 'MONTH_PLAN_DRAFT', status: 'PENDING', textContent: text,
    createdAt: new Date().toLocaleString('zh-CN', { hour12: false }),
  }
  conversation.messages.value = [...conversation.messages.value, optimistic]
  composer.value = ''
  hintedText.value = ''
  operationHint.value = 'AUTO'
  errorMessage.value = ''
  try {
    const response = await runAiRequest(({ requestId, signal }) => generateMonthPlanApi({
      requestId,
      planMonth: plan.value!.planMonth,
      intentText: text,
      currentForm: { items: stripIds(currentItems.value) },
      jobDescription: jobDescription.value || undefined,
      selectedContextTypes: [...selectedContextTypes.value],
      selectedHistoryMonths: [...selectedHistoryMonths.value],
      selectedKnowledgeTypes: [...selectedKnowledgeTypes.value],
      conversationId: active.id,
      operationMode: requestMode,
      conciseOutput: conciseOutput.value,
      exampleFileName: generationExample.value?.fileName,
      exampleContent: generationExample.value?.content,
    }, { requestId, signal }))
    if (!response) return
    await conversation.refreshMessages(active.id)
    if (response.draftVersionNo) latestDraftVersionNo.value = response.draftVersionNo
    const context = await getMonthPlanAiContextApi(
      plan.value.planMonth,
      text,
      [...selectedKnowledgeTypes.value],
    )
    applyContextInfo(context)
  } catch (error) {
    optimistic.status = 'FAILED'
    optimistic.errorMessage = error instanceof Error ? error.message : 'AI 月计划生成失败'
    conversation.messages.value = [...conversation.messages.value]
  }
}

async function stopGeneration() {
  await stopAiRequest()
  if (conversation.activeConversation.value) await conversation.refreshMessages(conversation.activeConversation.value.id)
}

function retryMessage(message: AiConversationMessage) {
  composer.value = message.textContent || ''
  hintedText.value = ''
  operationHint.value = 'AUTO'
  focusComposer()
}

function focusComposer() {
  previewSuggestion.value = undefined
  void nextTick(() => {
    const input = composerInput.value as ({ focus?: () => void; $el?: HTMLElement } | undefined)
    input?.$el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    input?.focus?.()
  })
}

function continueSuggestion(suggestion: AiGenerateResponse) {
  const prompt = '请继续调整刚才的月计划建议：'
  composer.value = prompt
  hintedText.value = prompt
  operationHint.value = suggestion.effectiveOperationMode === 'SUPPLEMENT' ? 'SUPPLEMENT' : 'OPTIMIZE'
  focusComposer()
  ElMessage.info('已载入这条建议，请补充具体调整要求')
}

async function openPreview(suggestion: AiGenerateResponse) {
  previewSuggestion.value = {
    ...suggestion,
    items: normalizeMonthPlanPeriods(plan.value?.planMonth || '', suggestion.items || []),
  }
  await recordAiSuggestionActionApi(suggestion.suggestionId, { actionCode: 'PREVIEW' })
}

async function applySuggestion(value: { mode: 'replace' | 'append'; indexes: number[]; items: AiPlanDraftItem[] }) {
  if (!plan.value || !previewSuggestion.value) return
  const source = previewSuggestion.value
  const proposedItems = value.mode === 'append' ? [...currentItems.value, ...value.items] : value.items
  const nextItems = normalizeMonthPlanPeriods(plan.value.planMonth, proposedItems)
  try {
    const draftPayload = {
      planMonth: plan.value.planMonth,
      items: nextItems.map((item) => ({
        id: item.id,
        taskName: item.taskName,
        taskContent: item.taskContent,
        deliverable: item.deliverable,
        quality: item.quality,
        estimatedStartDate: item.estimatedStartDate,
        estimatedEndDate: item.estimatedEndDate,
        performanceWeight: Number(item.performanceWeight),
        estimatedValue: Number(item.estimatedValue),
      })),
      recordVersion: true,
      versionRequestId: aiRequestId(),
    }
    const saved = plan.value.id
      ? await saveEmployeeMonthPlanDraftApi(plan.value.id, draftPayload)
      : await createEmployeeMonthPlanDraftApi(draftPayload)
    plan.value = saved
    const actionCode = value.indexes.length === source.items.length ? 'APPLY_ALL' : 'APPLY_ITEM'
    await recordAiSuggestionActionApi(source.suggestionId, {
      actionCode,
      appliedFields: value.indexes.map((index) => `items[${index}]`),
    })
    ElMessage.success('AI 建议已应用到月计划草稿')
    await router.push({ path: editPath(plan.value.id), query: { aiApplied: '1' } })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '应用 AI 建议失败')
  }
}

async function ignorePreview() {
  if (previewSuggestion.value?.suggestionId) {
    await recordAiSuggestionActionApi(previewSuggestion.value.suggestionId, { actionCode: 'IGNORE' })
  }
  previewSuggestion.value = undefined
}

async function createConversation() {
  try {
    await ElMessageBox.confirm('当前对话将转为历史记录，新对话不会继承其记忆。', '新建对话', {
      type: 'warning', confirmButtonText: '新建对话', cancelButtonText: '取消',
    })
  } catch { return }
  await conversation.startNew()
  mobileHistoryOpen.value = false
}

async function selectConversation(item: AiConversation) {
  await conversation.viewConversation(item)
  mobileHistoryOpen.value = false
}

async function returnToActiveConversation() {
  await conversation.returnToActive()
}

async function reloadConversation() {
  errorMessage.value = ''
  await conversation.resolveActive()
  await conversation.loadHistory()
}

function returnToEditor() {
  if (plan.value?.id) {
    void router.push(editPath(plan.value.id))
    return
  }
  if (plan.value?.planMonth) {
    void router.push({ path: editPath(), query: { planMonth: plan.value.planMonth } })
    return
  }
  void router.push(basePath.value)
}

function formatConversationTime(value?: string) {
  if (!value) return '历史对话'
  return value.replace('T', ' ').slice(5, 16)
}

function localizedConversationTitle(value: string) {
  return value.replace(/\s+Month plan$/i, ' 月计划')
}

onMounted(loadWorkspace)
</script>

<style scoped>
.month-ai-workspace { display:grid; grid-template:50px minmax(0,1fr) / 204px minmax(0,1fr); min-height:calc(100vh - 104px); height:calc(100vh - 104px); overflow:hidden; border:1px solid #d8e1dd; border-radius:8px; background:#fff; }
.workspace-header { grid-column:1 / -1; display:flex; align-items:center; justify-content:space-between; gap:12px; padding:0 12px; border-bottom:1px solid #dfe7e3; background:#fbfcfa; }
.workspace-identity,.workspace-tools { display:flex; align-items:center; gap:9px; min-width:0; }
.workspace-identity > div { display:grid; min-width:0; gap:2px; }
.workspace-identity strong { overflow:hidden; color:#203a35; font-size:15px; text-overflow:ellipsis; white-space:nowrap; }
.workspace-identity span { overflow:hidden; color:#71817c; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.workspace-safety-note { color:#71817c; font-size:11px; white-space:nowrap; }
.conversation-history-rail { grid-row:2; min-width:0; overflow:auto; border-right:1px solid #dfe7e3; background:#f6f8f6; transition:width .18s ease, opacity .18s ease; }
.conversation-history-rail > header { position:sticky; top:0; z-index:2; display:flex; align-items:center; justify-content:space-between; min-height:44px; padding:6px 8px 6px 11px; border-bottom:1px solid #e1e8e4; color:#40544e; background:#f6f8f6; font-size:13px; }
.conversation-history-rail > header > div { display:flex; align-items:center; gap:7px; }
.conversation-history-rail > header span { color:#8a9692; font-size:11px; font-weight:500; }
.history-actions { gap:2px!important; }
.history-actions :deep(.el-button) { width:28px; height:28px; padding:0; }
.conversation-history-rail nav { display:grid; }
.conversation-history-rail nav button { display:grid; gap:3px; width:100%; min-height:52px; padding:8px 10px; border:0; border-left:3px solid transparent; border-bottom:1px solid #e5ebe8; color:#40544e; background:transparent; text-align:left; cursor:pointer; }
.conversation-history-rail nav button:hover { background:#fff; }
.conversation-history-rail nav button.active { border-left-color:#2d776c; background:#fff; }
.conversation-history-rail nav strong { overflow:hidden; font-size:13px; line-height:1.4; text-overflow:ellipsis; white-space:nowrap; }
.conversation-history-rail nav span { display:flex; align-items:center; gap:5px; color:#83908c; font-size:11px; }
.conversation-history-rail nav i { width:6px; height:6px; border-radius:50%; background:#2d776c; }
.history-collapsed { grid-template-columns:0 minmax(0,1fr); }
.history-collapsed .conversation-history-rail { width:0; opacity:0; pointer-events:none; }
.workspace-main { grid-column:2; grid-row:2; display:grid; grid-template-rows:auto minmax(0,1fr) auto; min-width:0; min-height:0; overflow:hidden; }
.history-collapsed .workspace-main { grid-column:2; }
.workspace-main > :deep(.change-review) { grid-row:1 / -1; display:block; padding:22px max(22px, calc((100% - 1180px) / 2)); overflow:auto; }
.workspace-notices { display:grid; gap:6px; padding:8px 14px 0; }
.workspace-composer { position:sticky; bottom:0; z-index:3; display:flex; align-items:center; flex-direction:column; justify-content:center; padding:8px 14px 10px; border-top:1px solid #e0e7e4; background:rgb(255 255 255 / 96%); }
.composer-options { display:flex; align-items:center; width:min(860px,100%); min-height:36px; margin-bottom:6px; padding:6px 10px; border:1px solid #d9e4df; border-radius:8px; gap:9px; color:#667873; background:#f5f8f6; font-size:12px; }
.composer-options :deep(.el-checkbox) { margin-right:0; }
.composer-options :deep(.el-checkbox__label) { color:#29473f; font-size:12px; font-weight:700; }
.composer-example-row { display:flex; width:min(860px,100%); margin-bottom:6px; }
.composer-surface { width:min(860px,100%); border:1px solid #cfdad5; border-radius:8px; background:#fff; box-shadow:0 6px 18px rgb(32 58 53 / 7%); transition:border-color .16s ease, box-shadow .16s ease; }
.composer-surface:focus-within { border-color:#5f9489; box-shadow:0 8px 24px rgb(32 58 53 / 9%), 0 0 0 2px rgb(45 119 108 / 10%); }
.composer-surface.is-busy { border-color:#d9c6c2; }
.workspace-composer :deep(.el-textarea__inner) { min-height:54px!important; padding:10px 12px 6px; border-radius:8px 8px 0 0; box-shadow:none; font-size:15px; line-height:1.55; }
.workspace-composer :deep(.el-textarea__inner:focus) { box-shadow:none; }
.composer-footer { display:flex; align-items:center; justify-content:space-between; gap:9px; min-height:42px; padding:3px 6px 5px 7px; }
.composer-reference-ledger { display:flex; align-items:center; min-width:0; gap:7px; color:#667873; font-size:12px; }
.composer-reference-ledger > span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.composer-command { flex:none; width:36px; height:36px; }
.readonly-composer { display:flex; align-items:center; justify-content:center; gap:16px; padding:14px 20px; border-top:1px solid #e0e7e4; color:#71817c; background:#f8faf9; font-size:12px; }
.mobile-history-toggle,.mobile-history-close,.history-mask { display:none; }
@media (max-width:900px) {
  .month-ai-workspace,.history-collapsed { grid-template-columns:minmax(0,1fr); }
  .workspace-main,.history-collapsed .workspace-main { grid-column:1; }
  .conversation-history-rail { position:fixed; inset:64px auto 0 0; z-index:30; width:min(300px,86vw); box-shadow:12px 0 30px rgb(32 58 53 / 18%); transform:translateX(-105%); opacity:1; pointer-events:auto; transition:transform .2s ease; }
  .conversation-history-rail.is-mobile-open { transform:translateX(0); }
  .mobile-history-toggle,.mobile-history-close { display:inline-flex; }
  .desktop-history-toggle { display:none; }
  .history-mask { position:fixed; inset:0; z-index:29; display:block; border:0; background:rgb(20 35 31 / 32%); }
}
@media (max-width:640px) {
  .month-ai-workspace { min-height:calc(100vh - 88px); height:calc(100vh - 88px); border-radius:0; }
  .workspace-header { padding:0 10px; }
  .workspace-tools > .el-tag { display:none; }
  .workspace-safety-note { display:none; }
  .workspace-composer { padding:8px 9px 10px; }
  .composer-footer { align-items:flex-end; }
  .composer-reference-ledger { align-items:flex-start; flex:1; }
  .composer-reference-ledger > span { white-space:normal; line-height:1.4; }
  .composer-command { width:38px; height:38px; }
  .readonly-composer { align-items:stretch; flex-direction:column; }
}
@media (prefers-reduced-motion:reduce) { .conversation-history-rail { transition:none; } }
</style>
