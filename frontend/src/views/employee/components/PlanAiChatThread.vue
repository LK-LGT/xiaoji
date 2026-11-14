<template>
  <div ref="viewport" class="chat-thread" aria-live="polite">
    <div v-if="hasMore" class="load-older"><el-button link :loading="loadingOlder" @click="emit('load-older')">加载更早消息</el-button></div>
    <el-skeleton v-if="loading && !messages.length" :rows="5" animated />

    <section v-else-if="showStarters" class="chat-welcome">
      <div class="assistant-mark"><el-icon><MagicStick /></el-icon></div>
      <div>
        <strong>{{ periodKey }} 月计划</strong>
        <p>{{ welcomeText }}</p>
        <div class="starter-prompts">
          <button v-for="starter in starters" :key="starter.label" type="button" @click="emit('use-starter', starter)">
            {{ starter.label }}
          </button>
        </div>
      </div>
    </section>

    <ol v-else class="chat-message-list">
      <li v-for="message in messages" :key="message.id" :class="`message--${message.role.toLowerCase()}`">
        <template v-if="message.role === 'EVENT'">
          <span class="event-copy">{{ operationLabel(message.operationCode) || message.textContent }}</span>
        </template>
        <template v-else>
          <div v-if="message.role === 'ASSISTANT'" class="assistant-mark"><el-icon><MagicStick /></el-icon></div>
          <article class="message-bubble">
            <header v-if="message.role === 'ASSISTANT'">AI 助手</header>
            <p>{{ message.textContent || operationLabel(message.operationCode) }}</p>
            <span v-if="message.status === 'PENDING'" class="message-status">正在处理</span>
            <div v-else-if="message.status === 'CANCELLED'" class="message-status">已停止生成</div>
            <div v-else-if="message.status === 'FAILED'" class="message-error">
              <span>{{ message.errorMessage || '本次请求失败' }}</span>
              <el-button link type="primary" @click="emit('retry', message)">重试</el-button>
            </div>
            <MonthPlanAiResultCard
              v-if="asSuggestion(message)"
              :suggestion="asSuggestion(message)!"
              :stale="isStale(asSuggestion(message)!)"
              @preview="emit('preview-result', asSuggestion(message)!)"
              @continue="emit('continue-result', asSuggestion(message)!)"
            />
            <time :title="message.createdAt">{{ shortTime(message.createdAt) }}</time>
          </article>
        </template>
      </li>
    </ol>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { MagicStick } from '@element-plus/icons-vue'
import type { AiConversationMessage } from '@/api/aiConversations'
import type { AiGenerateResponse, MonthPlanAiOperationMode } from '@/api/employeeAi'
import MonthPlanAiResultCard from './MonthPlanAiResultCard.vue'

export interface MonthPlanStarter {
  label: string
  text: string
  operationMode: MonthPlanAiOperationMode
}

const props = withDefaults(defineProps<{
  messages: AiConversationMessage[]
  periodKey: string
  planState: 'EMPTY' | 'EXISTING' | 'REJECTED'
  starters: MonthPlanStarter[]
  latestDraftVersionNo?: number
  loading?: boolean
  loadingOlder?: boolean
  hasMore?: boolean
}>(), { loading: false, loadingOlder: false, hasMore: false })

const emit = defineEmits<{
  'load-older': []
  'use-starter': [starter: MonthPlanStarter]
  'preview-result': [suggestion: AiGenerateResponse]
  'continue-result': [suggestion: AiGenerateResponse]
  retry: [message: AiConversationMessage]
}>()

const viewport = ref<HTMLElement>()
const showStarters = computed(() => !props.messages.some((message) => message.role === 'USER'))
const welcomeText = computed(() => ({
  EMPTY: '当前计划还没有任务，可以从本月重点工作开始。',
  EXISTING: '已读取当前草稿，可以继续补充或优化。',
  REJECTED: '已读取审批反馈，可以围绕反馈继续修改。',
})[props.planState])

function asSuggestion(message: AiConversationMessage): AiGenerateResponse | undefined {
  const payload = message.payload
  if (message.role !== 'ASSISTANT' || !payload || !Array.isArray(payload.items) || !message.suggestionId) return
  return {
    suggestionId: message.suggestionId,
    items: payload.items as AiGenerateResponse['items'],
    warnings: Array.isArray(payload.warnings) ? payload.warnings.map(String) : [],
    missingContext: Array.isArray(payload.missingContext) ? payload.missingContext.map(String) : [],
    notice: String(payload.notice || ''),
    draftVersionId: Number(payload.draftVersionId || 0),
    draftVersionNo: Number(payload.draftVersionNo || 0),
    effectiveOperationMode: normalizeMode(payload.effectiveOperationMode),
    clarificationMessage: typeof payload.clarificationMessage === 'string' ? payload.clarificationMessage : undefined,
    generationProfile: payload.generationProfile === 'LEADER_DELIVERY_MANAGEMENT'
      ? 'LEADER_DELIVERY_MANAGEMENT' : 'EMPLOYEE_EXECUTION',
    generationProfileLabel: typeof payload.generationProfileLabel === 'string' ? payload.generationProfileLabel : undefined,
    knowledgeReferences: Array.isArray(payload.knowledgeReferences)
      ? payload.knowledgeReferences as AiGenerateResponse['knowledgeReferences'] : [],
  }
}

function normalizeMode(value: unknown): AiGenerateResponse['effectiveOperationMode'] {
  return ['GENERATE', 'SUPPLEMENT', 'OPTIMIZE', 'REVISE_REJECTION', 'CLARIFY'].includes(String(value))
    ? String(value) as AiGenerateResponse['effectiveOperationMode']
    : 'GENERATE'
}

function isStale(suggestion: AiGenerateResponse) {
  return Boolean(props.latestDraftVersionNo && suggestion.draftVersionNo && suggestion.draftVersionNo < props.latestDraftVersionNo)
}

function operationLabel(code?: string) {
  const labels: Record<string, string> = {
    PREVIEW: '已查看建议', APPLY_ALL: '已应用全部建议', APPLY_ITEM: '已应用所选任务',
    APPLY_FIELDS: '已应用所选内容', ADOPT_WITH_EDIT: '已修改并应用', IGNORE: '已放弃本次建议',
  }
  return code ? labels[code] || '' : ''
}

function shortTime(value: string) {
  const match = value?.match(/\d{2}:\d{2}/)
  return match?.[0] || ''
}

watch(() => props.messages.length, async () => {
  await nextTick()
  if (viewport.value) viewport.value.scrollTop = viewport.value.scrollHeight
})
</script>

<style scoped>
.chat-thread { width:100%; max-width:860px; min-height:0; margin-inline:auto; padding:18px 16px 24px; overflow:auto; }
.load-older { text-align:center; }
.chat-welcome { display:grid; grid-template-columns:36px minmax(0,1fr); gap:11px; max-width:760px; margin:clamp(18px, 4vh, 36px) auto 0; }
.chat-welcome strong { color:#203a35; font-size:18px; }
.chat-welcome p { margin:5px 0 12px; color:#64748b; font-size:14px; line-height:1.55; }
.assistant-mark { display:grid; flex:0 0 34px; width:34px; height:34px; place-items:center; border-radius:8px; color:#fff; background:#1f6258; }
.starter-prompts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
.starter-prompts button { max-width:100%; min-height:40px; padding:7px 10px; border:1px solid #ccd9d4; border-radius:6px; color:#31554d; background:#fff; font-size:13px; line-height:1.35; text-align:left; cursor:pointer; }
.starter-prompts button:hover { border-color:#2d776c; background:#f3f8f6; }
.chat-message-list { display:grid; gap:14px; margin:0; padding:0; list-style:none; }
.chat-message-list li { display:flex; align-items:flex-start; gap:12px; min-width:0; }
.chat-message-list .message--user { justify-content:flex-end; }
.chat-message-list .message--event { justify-content:center; }
.message-bubble { position:relative; min-width:0; max-width:min(78%, 760px); }
.message--assistant .message-bubble { flex:1; max-width:820px; padding-top:3px; }
.message--assistant .message-bubble header { margin-bottom:4px; color:#31554d; font-size:13px; font-weight:700; }
.message--user .message-bubble { padding:10px 13px; border-radius:8px 8px 3px 8px; color:#173c35; background:#dcece6; }
.message-bubble p { margin:0; overflow-wrap:anywhere; color:inherit; font-size:15px; line-height:1.6; white-space:pre-wrap; }
.message-bubble time { display:block; height:14px; margin-top:3px; color:#94a3a0; font-size:11px; text-align:right; }
.message--assistant .message-bubble time { text-align:left; }
.message-status { display:inline-block; margin-top:8px; color:#71817c; font-size:11px; }
.message-error { display:flex; align-items:center; gap:8px; margin-top:8px; color:#b34f42; font-size:12px; }
.event-copy { color:#84918d; font-size:12px; }
@media (max-width:680px) { .chat-thread { padding:20px 13px 28px; }.message-bubble { max-width:88%; }.message--assistant .message-bubble { max-width:calc(100% - 46px); }.starter-prompts { grid-template-columns:1fr; }.starter-prompts button { width:100%; } }
</style>
