import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import {
  listAiConversationMessages,
  listAiConversations,
  resolveAiConversation,
  startNewAiConversation,
  type AiConversation,
  type AiConversationMessage,
  type AiPlanType,
} from '@/api/aiConversations'

export function usePlanAiConversation(planType: AiPlanType, periodKey: MaybeRefOrGetter<string>) {
  const activeConversation = ref<AiConversation>()
  const viewingConversation = ref<AiConversation>()
  const conversations = ref<AiConversation[]>([])
  const messages = ref<AiConversationMessage[]>([])
  const loading = ref(false)
  const loadingOlder = ref(false)
  const historyLoading = ref(false)
  const errorMessage = ref('')
  const hasMore = ref(false)
  const nextBeforeId = ref<number>()
  let scopeRequest = 0

  const readonlyView = computed(() => viewingConversation.value?.status === 'ENDED')

  async function resolveActive() {
    const period = toValue(periodKey)
    if (!period) return
    const request = ++scopeRequest
    loading.value = true
    errorMessage.value = ''
    try {
      const conversation = await resolveAiConversation(planType, period)
      if (request !== scopeRequest) return
      activeConversation.value = conversation
      viewingConversation.value = conversation
      await refreshMessages(conversation.id)
    } catch (error) {
      if (request === scopeRequest) {
        errorMessage.value = error instanceof Error ? error.message : '对话加载失败'
      }
    } finally {
      if (request === scopeRequest) loading.value = false
    }
  }

  async function refreshMessages(conversationId = viewingConversation.value?.id) {
    if (!conversationId) return
    const page = await listAiConversationMessages(conversationId)
    if (viewingConversation.value?.id !== conversationId) return
    messages.value = page.records
    hasMore.value = page.hasMore
    nextBeforeId.value = page.nextBeforeId
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

  async function loadHistory() {
    const period = toValue(periodKey)
    if (!period) return
    historyLoading.value = true
    try {
      conversations.value = await listAiConversations(planType, period)
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '历史对话加载失败'
    } finally {
      historyLoading.value = false
    }
  }

  async function viewConversation(conversation: AiConversation) {
    viewingConversation.value = conversation
    loading.value = true
    errorMessage.value = ''
    try {
      await refreshMessages(conversation.id)
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '消息加载失败'
    } finally {
      loading.value = false
    }
  }

  async function returnToActive() {
    if (activeConversation.value) await viewConversation(activeConversation.value)
  }

  async function startNew() {
    const period = toValue(periodKey)
    if (!period) return
    loading.value = true
    errorMessage.value = ''
    try {
      const conversation = await startNewAiConversation(planType, period)
      activeConversation.value = conversation
      viewingConversation.value = conversation
      messages.value = []
      hasMore.value = false
      nextBeforeId.value = undefined
      await loadHistory()
      return conversation
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '新对话创建失败'
    } finally {
      loading.value = false
    }
  }

  watch(() => toValue(periodKey), () => {
    scopeRequest += 1
    activeConversation.value = undefined
    viewingConversation.value = undefined
    conversations.value = []
    messages.value = []
    hasMore.value = false
    nextBeforeId.value = undefined
    void resolveActive().then(loadHistory)
  }, { immediate: true })

  return {
    activeConversation,
    viewingConversation,
    conversations,
    messages,
    loading,
    loadingOlder,
    historyLoading,
    errorMessage,
    hasMore,
    readonlyView,
    resolveActive,
    refreshMessages,
    loadOlder,
    loadHistory,
    viewConversation,
    returnToActive,
    startNew,
  }
}
