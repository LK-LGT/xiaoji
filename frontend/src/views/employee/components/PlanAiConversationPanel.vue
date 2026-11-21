<template>
  <section class="conversation-panel" :class="{ 'conversation-panel--readonly': readonlyView }">
    <header class="conversation-header">
      <div class="conversation-scope">
        <span class="conversation-state" aria-hidden="true" />
        <div>
          <strong>{{ readonlyView ? '历史对话' : '当前对话' }}</strong>
          <span>{{ periodLabel }}</span>
        </div>
      </div>
      <div class="conversation-tools">
        <el-tooltip content="历史对话" placement="top">
          <el-button :icon="Clock" circle aria-label="历史对话" :disabled="loading" @click="openHistory" />
        </el-tooltip>
        <el-tooltip content="新对话" placement="top">
          <el-button :icon="Plus" circle aria-label="新对话" :disabled="busy || loading || readonlyView" @click="startNew" />
        </el-tooltip>
      </div>
    </header>

    <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
      <template #title>
        <span>{{ errorMessage }}</span>
        <el-button link type="primary" @click="reload">重试</el-button>
      </template>
    </el-alert>

    <div ref="messageViewport" class="conversation-messages" aria-live="polite">
      <el-button v-if="hasMore" link :loading="loadingOlder" @click="loadOlder">加载更早消息</el-button>
      <el-skeleton v-if="loading && !messages.length" :rows="4" animated />
      <el-empty v-else-if="!messages.length && !busy" :description="emptyDescription" :image-size="56" />
      <ol v-else class="message-timeline">
        <li v-if="hiddenMessageCount" class="message--hint">
          <span class="message-node" aria-hidden="true" />
          <article class="message-content message-content--hint">已收起 {{ hiddenMessageCount }} 条较早记录，可从历史对话中查看。</article>
        </li>
        <li v-for="message in visibleMessages" :key="message.id" :class="`message--${message.role.toLowerCase()}`">
          <span class="message-node" aria-hidden="true" />
          <article class="message-content">
            <div class="message-meta">
              <strong>{{ roleLabel(message.role) }}</strong>
              <span v-if="message.role !== 'EVENT' && operationLabel(message.operationCode)">{{ operationLabel(message.operationCode) }}</span>
              <time>{{ compactTime(message.createdAt) }}</time>
            </div>
            <p>{{ messageBody(message) }}</p>
            <el-tag v-if="message.status === 'PENDING'" size="small" effect="plain">发送中</el-tag>
            <el-tag v-else-if="message.status === 'CANCELLED'" size="small" type="info" effect="plain">已停止</el-tag>
            <el-alert v-else-if="message.status === 'FAILED'" type="error" :closable="false" :title="message.errorMessage || '本次请求失败，请重新发送'" />
            <details v-else-if="message.payload && message.role !== 'EVENT'" class="result-details">
              <summary>查看结果摘要</summary>
              <pre>{{ resultSummary(message) }}</pre>
              <el-button
                v-if="canPreviewResult(message)"
                type="primary"
                plain
                size="small"
                @click="emit('previewResult', message)"
              >
                查看并应用建议
              </el-button>
            </details>
          </article>
        </li>
        <li v-if="busy && !readonlyView" class="message--generating">
          <span class="message-node" aria-hidden="true" />
          <article class="message-content generating-card">
            <div class="generating-card__head">
              <span class="generating-spinner" aria-hidden="true" />
              <strong>AI 正在生成计划建议</strong>
            </div>
            <div class="generating-lines" aria-hidden="true"><span></span><span></span><span></span></div>
          </article>
        </li>
      </ol>
    </div>

    <div v-if="readonlyView" class="readonly-bar">
      <span>历史对话仅供查看，不会进入当前记忆。</span>
      <el-button type="primary" plain @click="returnToActive">返回当前对话</el-button>
    </div>
    <div v-else class="conversation-composer">
      <div v-if="composerToolsVisible" class="conversation-composer-tools">
        <slot name="composerTools" />
      </div>
      <el-input
        v-model="composer"
        type="textarea"
        :rows="2"
        maxlength="5000"
        show-word-limit
        resize="none"
        :placeholder="placeholder"
        :disabled="busy || disabled || !activeConversation"
        @keydown.enter.exact="sendFromKeyboard"
        @keydown.ctrl.enter="sendFromKeyboard"
      />
      <el-button v-if="busy" type="danger" plain :icon="VideoPause" @click="emit('stop')">
        停止生成
      </el-button>
      <el-button v-else type="primary" :icon="Promotion" :disabled="disabled || !composer.trim() || !activeConversation" @click="send">
        {{ sendLabel }}
      </el-button>
    </div>

    <el-drawer v-model="historyVisible" title="历史对话" size="360px" append-to-body>
      <el-skeleton v-if="historyLoading" :rows="5" animated />
      <el-empty v-else-if="!conversations.length" description="暂无历史对话" :image-size="56" />
      <nav v-else class="conversation-history" aria-label="历史对话">
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          type="button"
          :class="{ active: viewingConversation?.id === conversation.id }"
          @click="viewConversation(conversation)"
        >
          <strong>{{ conversation.title }}</strong>
          <span>{{ conversation.status === 'ACTIVE' ? '当前对话' : `结束于 ${conversation.endedAt}` }}</span>
        </button>
      </nav>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { Clock, Plus, Promotion, VideoPause } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, ref, watch } from 'vue'
import {
  listAiConversationMessages,
  listAiConversations,
  resolveAiConversation,
  startNewAiConversation,
  type AiConversation,
  type AiConversationMessage,
  type AiPlanType,
} from '@/api/aiConversations'

const props = withDefaults(defineProps<{
  planType: AiPlanType
  periodKey: string
  conversationId?: string
  busy?: boolean
  disabled?: boolean
  composerToolsVisible?: boolean
  emptyDescription?: string
  placeholder?: string
  sendLabel?: string
}>(), {
  busy: false,
  disabled: false,
  composerToolsVisible: false,
  emptyDescription: '从本次计划需求开始对话',
  placeholder: '继续补充要求或调整当前建议',
  sendLabel: '发送',
})

const emit = defineEmits<{
  'update:conversationId': [value: string]
  send: [value: string]
  stop: []
  previewResult: [value: AiConversationMessage]
}>()

const activeConversation = ref<AiConversation>()
const viewingConversation = ref<AiConversation>()
const conversations = ref<AiConversation[]>([])
const messages = ref<AiConversationMessage[]>([])
const composer = ref('')
const loading = ref(false)
const loadingOlder = ref(false)
const historyLoading = ref(false)
const historyVisible = ref(false)
const errorMessage = ref('')
const hasMore = ref(false)
const nextBeforeId = ref<string>()
const messageViewport = ref<HTMLElement>()
let scopeRequest = 0

const readonlyView = computed(() => Boolean(viewingConversation.value && viewingConversation.value.status === 'ENDED'))
const visibleMessages = computed(() => messages.value.slice(-5))
const hiddenMessageCount = computed(() => Math.max(0, messages.value.length - visibleMessages.value.length))
const periodLabel = computed(() => {
  const type = props.planType === 'MONTH' ? '月计划' : props.planType === 'WEEK' ? '周计划' : '日计划'
  return `${props.periodKey} ${type}`
})

async function resolveActive() {
  if (!props.periodKey) return
  const request = ++scopeRequest
  loading.value = true
  errorMessage.value = ''
  try {
    const conversation = await resolveAiConversation(props.planType, props.periodKey)
    if (request !== scopeRequest) return
    // ⚠️⚠️ **没有活跃会话时后端返回 null**（2026-11-21 修复）
    //
    // 原来的写法直接 `conversation.id` —— 首次使用（库里没会话）时
    // `null.id` 抛 TypeError ⇒ 面板弹「对话加载失败」+「重试」，
    // **输入框永久禁用**，用户完全没法用 AI 助手。
    //
    // ⇒ 没有会话就**自动新建一个** —— 首次使用不该要求用户先点「新建对话」。
    let active = conversation
    if (!active) {
      active = await startNewAiConversation(props.planType, props.periodKey)
      if (request !== scopeRequest) return
    }
    activeConversation.value = active
    viewingConversation.value = active
    emit('update:conversationId', active.id)
    await loadMessages(active.id)
  } catch (error) {
    if (request === scopeRequest) errorMessage.value = error instanceof Error ? error.message : '对话加载失败'
  } finally {
    if (request === scopeRequest) loading.value = false
  }
}

async function loadMessages(conversationId: string) {
  const page = await listAiConversationMessages(conversationId)
  messages.value = Array.isArray(page?.records) ? page.records : []
  hasMore.value = Boolean(page?.hasMore)
  nextBeforeId.value = typeof page?.nextBeforeId === 'string' ? page.nextBeforeId : undefined
  await scrollToBottom()
}

async function loadOlder() {
  if (!viewingConversation.value || !nextBeforeId.value || loadingOlder.value) return
  loadingOlder.value = true
  try {
    const page = await listAiConversationMessages(viewingConversation.value.id, nextBeforeId.value)
    messages.value = [...page.records, ...messages.value]
    hasMore.value = page.hasMore
    nextBeforeId.value = page.nextBeforeId
  } finally {
    loadingOlder.value = false
  }
}

async function openHistory() {
  historyVisible.value = true
  historyLoading.value = true
  try {
    conversations.value = await listAiConversations(props.planType, props.periodKey)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '历史对话加载失败')
  } finally {
    historyLoading.value = false
  }
}

async function viewConversation(conversation: AiConversation) {
  viewingConversation.value = conversation
  historyVisible.value = false
  loading.value = true
  try { await loadMessages(conversation.id) }
  catch (error) { errorMessage.value = error instanceof Error ? error.message : '消息加载失败' }
  finally { loading.value = false }
}

async function returnToActive() {
  if (!activeConversation.value) return
  await viewConversation(activeConversation.value)
}

async function startNew() {
  if (props.busy) return
  try {
    await ElMessageBox.confirm('当前对话将转为历史记录，新对话不会继承其记忆。', '新建对话', {
      type: 'warning',
      confirmButtonText: '新建对话',
      cancelButtonText: '取消',
    })
  } catch { return }
  loading.value = true
  try {
    const conversation = await startNewAiConversation(props.planType, props.periodKey)
    activeConversation.value = conversation
    viewingConversation.value = conversation
    messages.value = []
    hasMore.value = false
    nextBeforeId.value = undefined
    emit('update:conversationId', conversation.id)
    ElMessage.success('已开始新对话')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '新对话创建失败'
  } finally {
    loading.value = false
  }
}

function send() {
  const value = composer.value.trim()
  if (!value || props.busy || props.disabled || readonlyView.value) return
  composer.value = ''
  appendOptimisticUserMessage(value)
  emit('send', value)
}

function sendFromKeyboard(event: KeyboardEvent) {
  if (event.isComposing) return
  event.preventDefault()
  send()
}

function appendOptimisticUserMessage(value: string) {
  if (!activeConversation.value) return
  messages.value = [...messages.value, {
    id: `optimistic-${Date.now()}`,
    conversationId: activeConversation.value.id,
    role: 'USER',
    messageType: 'TEXT',
    operationCode: props.planType === 'WEEK' ? 'WEEK_PLAN_DRAFT' : props.planType === 'DAY' ? 'DAY_PLAN_DRAFT' : 'MONTH_PLAN_DRAFT',
    status: 'PENDING',
    textContent: value,
    createdAt: new Date().toISOString(),
  }]
  void scrollToBottom()
}

function reload() {
  void resolveActive()
}

function roleLabel(role: AiConversationMessage['role']) {
  return role === 'USER' ? '我' : role === 'ASSISTANT' ? 'AI 助手' : '操作记录'
}

function operationLabel(code?: string) {
  const labels: Record<string, string> = {
    MONTH_PLAN_DRAFT: '生成月计划', MONTH_PLAN_ITEM_OPTIMIZE: '优化计划事项', MONTH_PLAN_CHECK: '检查月计划',
    WEEK_PLAN_DRAFT: '生成周计划', WEEK_PLAN_ADJUST: '调整周计划', DAY_PLAN_DRAFT: '生成日计划', DAY_PLAN_ADJUST: '调整日计划',
    PREVIEW: '已查看建议', APPLY_ALL: '已应用到草稿', APPLY_ITEM: '已应用到草稿', APPLY_FIELDS: '已应用到草稿', ADOPT_WITH_EDIT: '修改后采纳', IGNORE: '已忽略',
  }
  return code ? labels[code] || code : ''
}

function messageBody(message: AiConversationMessage) {
  const raw = (message.textContent || '').trim()
  const mapped = operationLabel(message.operationCode || raw)
  if (message.role === 'EVENT') return mapped || '操作已记录'
  if (!raw) return mapped || '内容已记录'
  return mapped && raw === (message.operationCode || raw) ? mapped : raw
}

function compactTime(value?: string) {
  if (!value) return ''
  const normalized = value.replace('T', ' ')
  return normalized.length >= 16 ? normalized.slice(11, 16) : normalized
}

function resultSummary(message: AiConversationMessage) {
  const payload = message.payload || {}
  if (Array.isArray(payload.items)) {
    return payload.items.map((raw, index) => {
      const item = raw as Record<string, unknown>
      return [
        `${index + 1}. ${String(item.content || 'AI 未返回工作内容')}`,
        item.deliverable ? `交付物：${String(item.deliverable)}` : '',
        item.quality ? `质量：${String(item.quality)}` : '',
      ].filter(Boolean).join('\n')
    }).join('\n\n') || 'AI 未返回计划建议'
  }
  if (Array.isArray(payload.issues)) return `共 ${payload.issues.length} 条检查提示`
  if (payload.item) return '1 条计划事项优化建议'
  if (typeof payload.content === 'string') return payload.content
  return message.textContent || '结构化建议已保存'
}

function canPreviewResult(message: AiConversationMessage) {
  if (message.role !== 'ASSISTANT' || !message.payload) return false
  const payload = message.payload
  return Array.isArray(payload.items) || typeof payload.content === 'string'
}

async function scrollToBottom() {
  await nextTick()
  if (messageViewport.value) messageViewport.value.scrollTop = messageViewport.value.scrollHeight
}

watch(() => [props.planType, props.periodKey] as const, () => {
  activeConversation.value = undefined
  viewingConversation.value = undefined
  messages.value = []
  void resolveActive()
}, { immediate: true })

watch(() => props.busy, (busy, previous) => {
  if (!busy && previous && activeConversation.value && !readonlyView.value) {
    void loadMessages(activeConversation.value.id).catch((error) => {
      errorMessage.value = error instanceof Error ? error.message : '对话消息刷新失败'
    })
  }
})
</script>

<style scoped>
.conversation-panel { display:grid; min-height:420px; border:1px solid #d8e2de; border-radius:8px; background:#fff; overflow:hidden; }
.conversation-header { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 15px; border-bottom:1px solid #e4ebe8; background:#f8fbfa; }
.conversation-scope { display:flex; align-items:center; min-width:0; gap:10px; }
.conversation-state { width:4px; height:34px; border-radius:2px; background:#2d776c; }
.conversation-panel--readonly .conversation-state { background:#94a3b8; }
.conversation-scope div { display:grid; min-width:0; gap:2px; }
.conversation-scope strong { color:#1f3d37; font-size:14px; }
.conversation-scope span { overflow:hidden; color:#64748b; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.conversation-tools { display:flex; flex:0 0 auto; gap:8px; }
.conversation-messages { min-height:280px; max-height:560px; padding:18px 16px; overflow:auto; background:linear-gradient(180deg, #fbfdfc 0%, #f6faf8 100%); }
.message-timeline { display:grid; gap:13px; margin:0; padding:0; list-style:none; }
.message-timeline li { position:relative; display:flex; min-width:0; align-items:flex-start; gap:10px; padding:0; }
.message-node { display:none; }
.message-content { position:relative; min-width:0; max-width:82%; padding:11px 13px; border:1px solid #dfe8e4; border-radius:8px; background:#fff; box-shadow:0 6px 18px rgb(43 67 58 / 5%); }
.message--assistant,
.message--generating { justify-content:flex-start; }
.message--assistant .message-content,
.message--generating .message-content { border-top-left-radius:3px; border-color:#d6e5df; background:#fff; }
.message--assistant .message-content::before,
.message--generating .message-content::before {
  position:absolute;
  top:13px;
  left:-7px;
  width:12px;
  height:12px;
  border-bottom:1px solid #d6e5df;
  border-left:1px solid #d6e5df;
  background:inherit;
  content:"";
  transform:rotate(45deg);
}
.message--user { justify-content:flex-end; }
.message--user .message-content { border-color:#1f6258; border-top-right-radius:3px; background:#1f6258; color:#fff; box-shadow:0 10px 22px rgb(31 98 88 / 14%); }
.message--user .message-content::after {
  position:absolute;
  top:13px;
  right:-7px;
  width:12px;
  height:12px;
  border-top:1px solid #1f6258;
  border-right:1px solid #1f6258;
  background:#1f6258;
  content:"";
  transform:rotate(45deg);
}
.message--event,
.message--hint { justify-content:center; }
.message--event .message-content,
.message-content--hint { max-width:92%; padding:8px 11px; border-color:#e1e8e5; border-radius:999px; color:#64748b; background:#f8fafc; box-shadow:none; font-size:12px; text-align:center; }
.message-meta { display:flex; align-items:center; flex-wrap:wrap; gap:7px; color:#64748b; font-size:11px; }
.message-meta strong { color:#334155; font-size:12px; }
.message-meta time { margin-left:auto; font-variant-numeric:tabular-nums; }
.message--user .message-meta { justify-content:flex-end; color:rgb(255 255 255 / 72%); }
.message--user .message-meta strong,
.message--user .message-content p { color:#fff; }
.message--user .message-meta time { margin-left:0; }
.message-content p { margin:7px 0 0; overflow-wrap:anywhere; color:#334155; font-size:13px; line-height:1.65; white-space:pre-wrap; }
.result-details { margin-top:8px; color:#475569; font-size:12px; }
.result-details summary { cursor:pointer; }
.result-details pre { margin:8px 0 0; padding:8px; overflow:auto; border-radius:6px; background:#f1f5f3; font:12px/1.5 "Cascadia Mono", monospace; white-space:pre-wrap; }
.generating-card { display:grid; gap:12px; border-color:#cfe1da; background:#f6fbf8; box-shadow:0 8px 20px rgb(45 119 108 / 8%); }
.generating-card__head { display:flex; align-items:center; gap:9px; color:#1f5c51; }
.generating-card__head strong { font-size:13px; }
.generating-spinner {
  width:16px;
  height:16px;
  border:2px solid #c4ded6;
  border-top-color:#2d776c;
  border-radius:50%;
  animation:ai-spin .8s linear infinite;
}
.generating-lines { display:grid; gap:7px; }
.generating-lines span {
  height:8px;
  border-radius:999px;
  background:linear-gradient(90deg, #dbece5 0%, #f5fbf8 48%, #dbece5 100%);
  background-size:220% 100%;
  animation:ai-shimmer 1.2s ease-in-out infinite;
}
.generating-lines span:nth-child(2) { width:78%; animation-delay:.08s; }
.generating-lines span:nth-child(3) { width:56%; animation-delay:.16s; }
.conversation-composer { display:grid; grid-template-columns:minmax(0, 1fr) auto; align-items:end; gap:10px; padding:14px 15px; border-top:1px solid #e4ebe8; background:#fff; }
.conversation-composer-tools { grid-column:1 / -1; display:grid; gap:8px; margin-bottom:2px; }
.conversation-composer .el-button { min-width:88px; height:54px; }
.readonly-bar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px; border-top:1px solid #e2e8f0; color:#64748b; background:#f8fafc; font-size:12px; }
.conversation-history { display:grid; gap:8px; }
.conversation-history button { display:grid; gap:5px; width:100%; padding:11px; border:1px solid #e2e8f0; border-radius:7px; background:#fff; text-align:left; cursor:pointer; }
.conversation-history button.active { border-color:#2d776c; background:#f2f8f6; }
.conversation-history strong { color:#334155; }
.conversation-history span { color:#64748b; font-size:12px; }
@keyframes ai-spin { to { transform:rotate(360deg); } }
@keyframes ai-shimmer { 0% { background-position:120% 0; } 100% { background-position:-120% 0; } }
@media (prefers-reduced-motion:reduce) {
  .generating-spinner,
  .generating-lines span { animation:none; }
}
@media (max-width:680px) { .conversation-messages { max-height:420px; padding:14px 12px; }.message-content { max-width:90%; }.message--event .message-content,.message-content--hint { max-width:100%; border-radius:8px; }.conversation-composer { grid-template-columns:1fr; }.conversation-composer-tools { margin-bottom:0; }.conversation-composer .el-button { width:100%; height:40px; }.readonly-bar { align-items:stretch; flex-direction:column; } }
</style>
