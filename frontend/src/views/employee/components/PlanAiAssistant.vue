<template>
  <section class="plan-ai">
    <el-alert v-if="error" type="warning" :closable="false" :title="error" />
    <el-alert v-else-if="context?.enabled === false" type="info" :closable="false" title="AI 服务暂未启用，请联系管理员配置。" />
    <div v-if="hasDraft" class="draft-toolbar">
      <span>{{ mode === 'week' ? '周计划建议已生成' : '日计划建议已生成' }}</span>
      <el-button type="primary" plain :icon="View" @click="visible = true">查看上次建议</el-button>
    </div>

    <PlanAiExampleInput v-if="mode === 'day'" v-model="generationExample" :disabled="busy || context?.enabled === false" />

    <PlanAiConversationPanel
      :plan-type="mode === 'week' ? 'WEEK' : 'DAY'"
      :period-key="date"
      :conversation-id="conversationId"
      :busy="busy"
      :disabled="!context || context.enabled === false"
      :composer-tools-visible="mode === 'week'"
      :empty-description="emptyDescription"
      :placeholder="composerPlaceholder"
      :send-label="composerSendLabel"
      @update:conversation-id="updateConversationId"
      @send="sendConversationMessage"
      @stop="stopGeneration"
      @preview-result="previewConversationResult"
    >
      <template #composerTools>
        <div class="week-generation-options" aria-label="周计划生成设置">
          <el-checkbox v-model="conciseOutput" :disabled="busy || context?.enabled === false">精简生成</el-checkbox>
          <span>减少背景和重复说明，精简工作内容、交付物和质量</span>
        </div>
        <PlanAiExampleInput v-model="generationExample" :disabled="busy || context?.enabled === false" />
      </template>
    </PlanAiConversationPanel>

    <el-drawer v-model="visible" title="AI 建议预览" size="560px">
      <el-tag v-if="(mode === 'week' ? weekDraft : dayDraft)?.draftVersionNo" class="version-badge" type="success">已保存为 V{{ (mode === 'week' ? weekDraft : dayDraft)?.draftVersionNo }}</el-tag>
      <div v-if="mode === 'week'" class="week-suggestions">
        <label v-for="(item, index) in weekDraft?.items" :key="index" class="task-option">
          <el-checkbox v-model="selected[index]" :aria-label="`选择第 ${index + 1} 条建议`" />
          <article>
            <div class="task-option__index">建议任务 {{ index + 1 }}</div>
            <dl>
              <dt>工作内容</dt><dd :class="{ missing: !item.content }">{{ item.content || 'AI 未返回工作内容' }}</dd>
              <dt>交付物</dt><dd :class="{ missing: !item.deliverable }">{{ item.deliverable || 'AI 未返回交付物' }}</dd>
              <dt>质量</dt><dd :class="{ missing: !item.quality }">{{ item.quality || 'AI 未返回质量标准' }}</dd>
              <dt>统计周期</dt><dd>{{ date }} 至 {{ weekEnd(date) }}</dd>
              <dt>月计划来源</dt><dd>{{ item.monthPlanItemId ? `#${item.monthPlanItemId}` : '无' }}</dd>
            </dl>
          </article>
        </label>
      </div>
      <div v-else-if="dayDraft" class="day-fields">
        <article class="day-content-preview">
          <span>工作内容</span>
          <p>{{ dayDraft.content || 'AI 未返回工作内容' }}</p>
        </article>
      </div>
      <el-input v-model="instruction" class="adjust" placeholder="继续调整，例如：精简内容并按执行顺序分点" @keyup.enter="adjust" />
      <template #footer>
        <el-button @click="ignore">忽略</el-button>
        <el-button v-if="busy" type="danger" plain @click="stopGeneration">停止生成</el-button>
        <el-button v-else :disabled="!instruction.trim()" @click="adjust()">让 AI 调整</el-button>
        <el-button type="primary" :disabled="mode === 'week' && !selected.some(Boolean)" @click="apply">{{ mode === 'week' ? '采纳所选内容' : '采纳到草稿' }}</el-button>
      </template>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { View } from '@element-plus/icons-vue'
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { AiConversationMessage } from '@/api/aiConversations'
import { adjustDayPlanAiApi, adjustWeekPlanAiApi, generateDayPlanAiApi, generateWeekPlanAiApi, getDayPlanAiContextApi, getWeekPlanAiContextApi, recordAiSuggestionActionApi, type AiContextResponse, type AiGenerationExample, type DayPlanAiDraft, type DayPlanAiForm, type WeekPlanAiDraft, type WeekPlanAiItem } from '@/api/employeeAi'
import { useCancelableAiRequest } from '@/composables/useCancelableAiRequest'
import PlanAiConversationPanel from './PlanAiConversationPanel.vue'
import PlanAiExampleInput from './PlanAiExampleInput.vue'

const props = defineProps<{ mode: 'week' | 'day'; date: string; form: { items?: WeekPlanAiItem[]; relatedWeekPlanItemIds?: string[]; content?: string; remark?: string } }>()
const emit = defineEmits<{ apply: [WeekPlanAiItem[] | DayPlanAiForm]; 'version-created': [versionNo: number] }>()
const context = ref<AiContextResponse>()
const instruction = ref('')
const { running: busy, run: runAiRequest, stop: stopAiRequest } = useCancelableAiRequest()
const error = ref('')
const visible = ref(false)
const weekDraft = ref<WeekPlanAiDraft>()
const dayDraft = ref<DayPlanAiDraft>()
const selected = ref<boolean[]>([])
const conversationId = ref<string>()
const generationExample = ref<AiGenerationExample>()
const conciseOutput = ref(false)
const hasDraft = computed(() => props.mode === 'week' ? Boolean(weekDraft.value) : Boolean(dayDraft.value))
const emptyDescription = computed(() => props.mode === 'week' ? '描述本周工作重点和交付要求，生成周计划建议' : '描述今日工作重点和交付要求，生成日计划建议')
const composerPlaceholder = computed(() => hasDraft.value ? '继续补充要求或调整当前建议' : (props.mode === 'week' ? '描述本周工作重点和交付要求' : '描述今日工作重点和交付要求'))
const composerSendLabel = computed(() => hasDraft.value ? '继续调整' : (props.mode === 'week' ? '生成周计划' : '生成日计划'))

function weekEnd(value: string) {
  const dateValue = new Date(`${value}T00:00:00`)
  dateValue.setDate(dateValue.getDate() + 6)
  return `${dateValue.getFullYear()}-${String(dateValue.getMonth() + 1).padStart(2, '0')}-${String(dateValue.getDate()).padStart(2, '0')}`
}

async function load() {
  if (!props.date) return
  try { context.value = props.mode === 'week' ? await getWeekPlanAiContextApi(props.date) : await getDayPlanAiContextApi(props.date) }
  catch (e) { error.value = e instanceof Error ? e.message : 'AI 上下文加载失败' }
}
async function generate(message?: string) {
  const requestText = typeof message === 'string' ? message.trim() : ''
  if (!requestText) return
  error.value = ''
  try {
    if (props.mode === 'week') {
      const result = await runAiRequest(({ requestId, signal }) => generateWeekPlanAiApi({ requestId, weekStart: props.date, intentText: requestText, currentForm: { items: props.form.items ?? [] }, conversationId: conversationId.value, conciseOutput: conciseOutput.value, exampleFileName: generationExample.value?.fileName, exampleContent: generationExample.value?.content }, { requestId, signal }))
      if (!result) return
      weekDraft.value = result
      emit('version-created', weekDraft.value.draftVersionNo)
      selected.value = weekDraft.value.items.map(() => true)
    } else {
      const result = await runAiRequest(({ requestId, signal }) => generateDayPlanAiApi({ requestId, planDate: props.date, intentText: requestText, currentForm: { relatedWeekPlanItemIds: [...(props.form.relatedWeekPlanItemIds ?? [])], content: props.form.content ?? '', remark: '' }, conversationId: conversationId.value, exampleFileName: generationExample.value?.fileName, exampleContent: generationExample.value?.content }, { requestId, signal }))
      if (!result) return
      dayDraft.value = result
      emit('version-created', dayDraft.value.draftVersionNo)
    }
    visible.value = true
  } catch (e) { error.value = e instanceof Error ? e.message : 'AI 生成失败' }
}
async function adjust(message?: string) {
  const requestText = typeof message === 'string' ? message.trim() : instruction.value.trim()
  if (!requestText) return
  error.value = ''
  try {
    if (props.mode === 'week' && weekDraft.value) {
      const result = await runAiRequest(({ requestId, signal }) => adjustWeekPlanAiApi({ requestId, weekStart: props.date, draft: { items: weekDraft.value!.items }, instruction: requestText, conversationId: conversationId.value, conciseOutput: conciseOutput.value }, { requestId, signal }))
      if (!result) return
      weekDraft.value = result
      emit('version-created', weekDraft.value.draftVersionNo)
      selected.value = weekDraft.value.items.map(() => true)
    } else if (dayDraft.value) {
      const result = await runAiRequest(({ requestId, signal }) => adjustDayPlanAiApi({ requestId, planDate: props.date, draft: { ...dayDraft.value!, remark: '' }, instruction: requestText, conversationId: conversationId.value }, { requestId, signal }))
      if (!result) return
      dayDraft.value = result
      emit('version-created', dayDraft.value.draftVersionNo)
    }
    instruction.value = ''
  } catch (e) { error.value = e instanceof Error ? e.message : 'AI 调整失败' }
}
async function stopGeneration() {
  const confirmed = await stopAiRequest()
  confirmed ? ElMessage.info('已停止本次生成') : ElMessage.warning('界面已停止等待，服务端取消未确认')
}
function sendConversationMessage(message: string) {
  const hasDraft = props.mode === 'week' ? Boolean(weekDraft.value) : Boolean(dayDraft.value)
  if (hasDraft) void adjust(message)
  else void generate(message)
}

function previewConversationResult(message: AiConversationMessage) {
  const payload = message.payload || {}
  if (props.mode === 'week') {
    const items = Array.isArray(payload.items) ? payload.items.map(normalizeWeekItem).filter(Boolean) as WeekPlanAiItem[] : []
    if (!items.length) {
      ElMessage.warning('这条 AI 消息没有可应用的周计划建议')
      return
    }
    weekDraft.value = {
      suggestionId: message.suggestionId || String(payload.suggestionId || ''),
      items,
      warnings: stringArray(payload.warnings),
      missingContext: stringArray(payload.missingContext),
      notice: String(payload.notice || ''),
      draftVersionId: Number(payload.draftVersionId || 0),
      draftVersionNo: Number(payload.draftVersionNo || 0),
    }
    selected.value = items.map(() => true)
  } else {
    if (typeof payload.content !== 'string') {
      ElMessage.warning('这条 AI 消息没有可应用的日计划建议')
      return
    }
    dayDraft.value = {
      suggestionId: message.suggestionId || String(payload.suggestionId || ''),
      // BUG-R6-14：周计划条目主键是字符串 UUID，禁止 `Number(...)` 归一化。
      relatedWeekPlanItemIds: Array.isArray(payload.relatedWeekPlanItemIds)
        ? payload.relatedWeekPlanItemIds.map((item) => String(item).trim()).filter(Boolean)
        : [],
      content: payload.content,
      remark: typeof payload.remark === 'string' ? payload.remark : '',
      warnings: stringArray(payload.warnings),
      missingContext: stringArray(payload.missingContext),
      notice: String(payload.notice || ''),
      draftVersionId: Number(payload.draftVersionId || 0),
      draftVersionNo: Number(payload.draftVersionNo || 0),
    }
  }
  instruction.value = ''
  visible.value = true
}

function normalizeWeekItem(raw: unknown): WeekPlanAiItem | null {
  if (!raw || typeof raw !== 'object') return null
  const item = raw as Record<string, unknown>
  // BUG-R6-14：条目主键是字符串 UUID，禁止 `Number(...)` 归一化。
  const rawMonthPlanItemId = item.monthPlanItemId
  const monthPlanItemId = rawMonthPlanItemId === null || rawMonthPlanItemId === undefined
    ? ''
    : String(rawMonthPlanItemId).trim()
  return {
    id: item.id === null || item.id === undefined ? undefined : String(item.id),
    monthPlanItemId: monthPlanItemId || null,
    content: String(item.content || ''),
    deliverable: String(item.deliverable || ''),
    quality: String(item.quality || ''),
    plannedFinishDate: String(item.plannedFinishDate || weekEnd(props.date)),
  }
}

function stringArray(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}
function updateConversationId(value: string) {
  if (conversationId.value !== value) {
    void stopAiRequest()
    weekDraft.value = undefined
    dayDraft.value = undefined
    visible.value = false
    selected.value = []
  }
  conversationId.value = value
}
async function apply() {
  const draft = props.mode === 'week' ? weekDraft.value : dayDraft.value
  if (!draft) return
  if (props.mode === 'week') emit('apply', (draft as WeekPlanAiDraft).items.filter((_, index) => selected.value[index]))
  else {
    const day = draft as DayPlanAiDraft
    emit('apply', { relatedWeekPlanItemIds: [...(props.form.relatedWeekPlanItemIds ?? [])], content: day.content, remark: '' })
  }
  visible.value = false
  await recordAiSuggestionActionApi(draft.suggestionId, { actionCode: 'APPLY_FIELDS' })
}
async function ignore() {
  const draft = props.mode === 'week' ? weekDraft.value : dayDraft.value
  visible.value = false
  if (draft) await recordAiSuggestionActionApi(draft.suggestionId, { actionCode: 'IGNORE' })
}
watch(() => props.date, () => {
  void stopAiRequest()
  conversationId.value = undefined
  weekDraft.value = undefined
  dayDraft.value = undefined
  generationExample.value = undefined
  conciseOutput.value = false
  visible.value = false
  void load()
})
onMounted(load)
</script>

<style scoped>
.plan-ai { display: grid; gap: 12px; padding: 0; border: 0; background: transparent; }
.draft-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 12px; border:1px solid #d7e5df; border-radius:8px; color:#24544c; background:#f4faf7; font-size:13px; }
.week-generation-options { display:flex; align-items:center; flex-wrap:wrap; gap:8px 10px; width:100%; padding:9px 11px; border:1px solid #d8e5df; border-radius:8px; color:#48615a; background:#f7faf8; font-size:12px; }
.week-generation-options span { color:#6a7b75; line-height:1.45; }
.plan-ai header { display: flex; align-items: center; justify-content: space-between; }
.plan-ai h2 { margin: 0; font-size: 17px; }
.plan-ai > p { margin: 0; color: var(--muted); font-size: 12px; }
.week-suggestions { display: grid; gap: 10px; }
.task-option { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; align-items: start; padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: #fff; cursor: pointer; }
.task-option article { min-width: 0; }
.task-option__index { margin-bottom: 10px; color: #2d776c; font-size: 12px; font-weight: 700; }
.task-option dl { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 8px 10px; margin: 0; }
.task-option dt { color: var(--muted); font-size: 12px; }
.task-option dd { margin: 0; overflow-wrap: anywhere; color: var(--ink); line-height: 1.5; }
.task-option dd.missing { color: var(--el-color-warning-dark-2); }
.day-fields { display: grid; gap: 14px; }
.day-content-preview {
  display: grid;
  gap: 10px;
  padding: 16px;
  border: 1px solid #dbe7e1;
  border-radius: 8px;
  background: #f8fbfa;
}

.day-content-preview span {
  color: #176b45;
  font-size: 12px;
  font-weight: 800;
}

.day-content-preview p {
  margin: 0;
  color: #243b35;
  line-height: 1.7;
  white-space: pre-wrap;
}
.adjust { margin-top: 18px; }
</style>
