import { defineStore } from 'pinia'
import {
  getMessageSummaryApi,
  markAllMessagesReadApi,
  markMessageReadApi,
  type MessageSummary,
  type UserMessage,
} from '@/api/messages'

const emptySummary = (): MessageSummary => ({
  unreadCount: 0,
  pendingTodoCount: 0,
  unreadNoticeCount: 0,
  menuBadges: {},
  unreadMessages: [],
})

/**
 * 归一化后端返回的汇总数据。
 *
 * 后端历史实现返回 snake_case（unread_count / messages），或只返回部分字段。
 * 若直接把响应写进 state，summary.menuBadges 会是 undefined，
 * AppShell 渲染时 `messageStore.badgeFor(path)` 会抛
 * "Cannot read properties of undefined (reading '/xxx')"，
 * 从而中断整棵组件树的 patch —— 表现为左侧菜单点击后右侧空白。
 *
 * 这里统一补齐缺失字段，保证渲染路径上的数据永远安全。
 */
function normalizeSummary(raw: unknown): MessageSummary {
  const base = emptySummary()
  if (!raw || typeof raw !== 'object') return base
  const data = raw as Record<string, unknown>

  const menuBadgesRaw = data.menuBadges ?? data.menu_badges
  const menuBadges: Record<string, number> = {}
  if (menuBadgesRaw && typeof menuBadgesRaw === 'object') {
    for (const [key, value] of Object.entries(menuBadgesRaw as Record<string, unknown>)) {
      const num = Number(value)
      if (Number.isFinite(num)) menuBadges[key] = num
    }
  }

  const unreadMessagesRaw = data.unreadMessages ?? data.messages
  const unreadMessages = Array.isArray(unreadMessagesRaw) ? (unreadMessagesRaw as UserMessage[]) : []

  const num = (...keys: string[]) => {
    for (const key of keys) {
      const value = Number(data[key])
      if (Number.isFinite(value)) return value
    }
    return 0
  }

  return {
    unreadCount: num('unreadCount', 'unread_count'),
    pendingTodoCount: num('pendingTodoCount', 'pending_todo_count'),
    unreadNoticeCount: num('unreadNoticeCount', 'unread_notice_count'),
    menuBadges,
    unreadMessages,
  }
}

let pollingTimer: number | undefined

export const useMessageStore = defineStore('messages', {
  state: () => ({
    summary: emptySummary(),
    loading: false,
    initialized: false,
  }),
  getters: {
    unreadCount: (state) => state.summary.unreadCount,
    unreadMessages: (state) => state.summary.unreadMessages,
  },
  actions: {
    async refresh(_silent = false) {
      if (this.loading) return
      this.loading = true
      try {
        this.summary = normalizeSummary(await getMessageSummaryApi())
        this.initialized = true
      } finally {
        this.loading = false
      }
    },
    badgeFor(path: string) {
      // 渲染路径上必须万无一失：任何异常数据都不能让整个布局 patch 中断。
      return this.summary?.menuBadges?.[path] ?? 0
    },
    async markRead(message: UserMessage) {
      if (message.status === 'UNREAD') await markMessageReadApi(message.id)
      await this.refresh(true)
    },
    async markAllRead() {
      const result = await markAllMessagesReadApi()
      await this.refresh(true)
      return result?.updated ?? 0
    },
    startPolling() {
      if (pollingTimer) return
      pollingTimer = window.setInterval(() => {
        void this.refresh(true).catch(() => undefined)
      }, 30_000)
    },
    stopPolling() {
      if (!pollingTimer) return
      window.clearInterval(pollingTimer)
      pollingTimer = undefined
    },
    reset() {
      this.summary = emptySummary()
      this.initialized = false
      this.stopPolling()
    },
  },
})
