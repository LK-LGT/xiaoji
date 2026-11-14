<template>
  <el-config-provider :locale="zhCn">
    <router-view v-if="$route.meta.public || $route.meta.passwordChange" />
    <div v-else class="app-shell">
    <aside class="sidebar" :class="{ 'sidebar--open': sidebarOpen }">
      <div class="brand">
        <div class="brand-mark">成</div>
        <div class="brand-text">
          <strong>成果计划</strong>
          <span>工作闭环平台</span>
        </div>
        <button class="sidebar-close" type="button" aria-label="关闭导航" @click="sidebarOpen = false">×</button>
      </div>

      <div class="sidebar-caption">当前工作空间</div>
      <el-dropdown
        v-if="userWorkspaces.length > 1"
        class="workspace-dropdown"
        trigger="click"
        @command="switchWorkspace"
      >
        <button class="workspace-switcher workspace-switcher--button" type="button" aria-label="切换工作空间">
          <span class="workspace-dot"></span>
          <span class="workspace-switcher__copy">
            <strong>{{ activeWorkspace?.title || '未配置工作台' }}</strong>
            <small>{{ authStore.user?.realName || '当前用户' }}</small>
          </span>
          <el-icon class="workspace-chevron"><ArrowDown /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu class="workspace-menu">
            <el-dropdown-item
              v-for="workspace in userWorkspaces"
              :key="workspace.id"
              :command="workspace.homePath"
              :disabled="workspace.id === activeWorkspace?.id"
            >
              <span class="workspace-menu__dot"></span>
              <span>{{ workspace.title }}</span>
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <div v-else class="workspace-switcher">
        <span class="workspace-dot"></span>
        <div class="workspace-switcher__copy">
          <strong>{{ activeWorkspace?.title || '未配置工作台' }}</strong>
          <small>{{ authStore.user?.realName || '当前用户' }}</small>
        </div>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <div v-if="activeWorkspace" class="nav-group">
          <div class="nav-title">{{ activeWorkspace.title }}</div>
          <router-link
            v-for="item in activeItems"
            :key="item.path"
            class="nav-link"
            :to="item.path"
            @click="sidebarOpen = false"
          >
            <span class="nav-icon">{{ item.icon }}</span><span>{{ item.label }}</span>
            <span v-if="menuBadge(item.path)" class="nav-badge">{{ badgeText(menuBadge(item.path)) }}</span>
          </router-link>
        </div>
      </nav>

      <div class="sidebar-footer">
        <div class="sidebar-footer__line">
          <span class="status-dot" :class="{ 'status-dot--error': !systemHealthy }"></span>{{ healthLabel }}
        </div>
        <span>v{{ appVersion }} · 2026</span>
      </div>
    </aside>

    <div v-if="sidebarOpen" class="sidebar-overlay" @click="sidebarOpen = false"></div>
    <main class="main">
      <header class="topbar">
        <div class="topbar-left">
          <button class="mobile-menu" type="button" aria-label="打开导航" @click="sidebarOpen = true">☰</button>
          <button v-if="canGoBack" class="topbar-back" type="button" aria-label="返回上一级" @click="goToBreadcrumbParent">
            <el-icon><ArrowLeft /></el-icon>
            <span>返回</span>
          </button>
          <nav class="breadcrumbs" aria-label="面包屑导航">
            <template
              v-for="(item, index) in breadcrumbNavigation.breadcrumbs"
              :key="`${item.path || route.path}-${index}`"
            >
              <router-link v-if="item.path" :to="item.path">{{ item.title }}</router-link>
              <strong v-else aria-current="page">{{ item.title }}</strong>
              <b v-if="index < breadcrumbNavigation.breadcrumbs.length - 1" aria-hidden="true">/</b>
            </template>
          </nav>
        </div>
        <div class="topbar-actions">
          <div ref="notificationWrap" class="notification-wrap">
            <button
              class="topbar-icon"
              type="button"
              :aria-expanded="notificationOpen"
              aria-controls="notification-panel"
              :aria-label="messageStore.unreadCount ? `打开消息中心，${messageStore.unreadCount} 条未读消息` : '打开消息中心'"
              @click="toggleNotification"
            >
              <el-icon><Bell /></el-icon>
              <span v-if="messageStore.unreadCount" class="topbar-message-count">
                {{ badgeText(messageStore.unreadCount) }}
              </span>
            </button>
            <div v-if="notificationOpen" id="notification-panel" class="notification-panel" role="dialog" aria-label="消息中心">
              <div class="notification-panel__head">
                <div>
                  <strong>消息中心</strong>
                  <small>{{ messageStore.unreadCount ? `${messageStore.unreadCount} 条未读` : '没有新消息' }}</small>
                </div>
                <button v-if="messageStore.unreadCount" type="button" class="notification-read-all" @click="markAllRead">
                  全部已读
                </button>
              </div>
              <div class="notification-panel__list">
                <button
                  v-for="message in messageStore.unreadMessages"
                  :key="message.id"
                  class="notification-item"
                  type="button"
                  @click="openNotification(message)"
                >
                  <span class="notification-item__dot" :class="`is-${message.messageType.toLowerCase()}`"></span>
                  <span>
                    <span class="notification-item__title">
                      <strong>{{ message.title }}</strong>
                      <em v-if="message.overdue">逾期</em>
                    </span>
                    <small>{{ message.content || '进入对应页面查看详情' }}</small>
                    <time>{{ formatMessageTime(message.createdAt) }}</time>
                  </span>
                  <b>›</b>
                </button>
                <div v-if="messageStore.loading" class="notification-empty">正在加载消息…</div>
                <div v-else-if="!messageStore.unreadMessages.length" class="notification-empty">
                  <el-icon><Check /></el-icon>
                  <strong>消息已全部读完</strong>
                  <span>新的审批和处理结果会在这里提醒你。</span>
                </div>
              </div>
              <button type="button" class="notification-panel__footer" @click="openMessageCenter">
                查看全部消息<span>›</span>
              </button>
            </div>
          </div>
          <span class="topbar-divider"></span>
          <button class="user-chip" type="button" aria-label="进入用户中心" @click="router.push('/profile')">
            <span class="user-avatar">{{ (authStore.user?.realName || '用').slice(0, 1) }}</span>
            <span class="topbar-user">{{ authStore.user?.realName || '当前用户' }}</span>
          </button>
          <el-button class="logout-button" size="small" @click="logout">退出</el-button>
        </div>
      </header>
      <router-view />
    </main>
    </div>
  </el-config-provider>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowDown, ArrowLeft, Bell, Check } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useRoute, useRouter } from 'vue-router'
import type { UserMessage } from '@/api/messages'
import { getHealth } from '@/api/system'
import { resolveBreadcrumbNavigation } from '@/navigation/breadcrumbs'
import {
  availableWorkspaces,
  visibleWorkspaceItems,
  workspaceForPath,
} from '@/navigation/workspaces'
import { useAuthStore } from '@/stores/auth'
import { useMessageStore } from '@/stores/messages'
import packageInfo from '../package.json'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const messageStore = useMessageStore()
const sidebarOpen = ref(false)
const notificationOpen = ref(false)
const notificationWrap = ref<HTMLElement>()
const systemHealthy = ref(true)
const appVersion = packageInfo.version
let healthTimer: number | undefined
const MONTH_PLAN_TOPBAR_RETURN_KEY = 'month-plan-topbar-return'

const userPermissions = computed(() => authStore.user?.permissions || [])
const userWorkspaces = computed(() => availableWorkspaces(userPermissions.value))
const activeWorkspace = computed(() => workspaceForPath(route.path) || userWorkspaces.value[0])
const activeItems = computed(() => activeWorkspace.value
  ? visibleWorkspaceItems(activeWorkspace.value, userPermissions.value)
  : [])
const breadcrumbNavigation = computed(() => resolveBreadcrumbNavigation(
  { path: route.path, query: route.query },
  activeWorkspace.value?.homePath,
))
const canGoBack = computed(() => Boolean(breadcrumbNavigation.value.parentPath))
const healthLabel = computed(() => systemHealthy.value ? '系统运行正常' : '服务连接异常')

function goToBreadcrumbParent() {
  const parentPath = breadcrumbNavigation.value.parentPath
  if (!parentPath) return
  if (isMonthPlanEditorReturn(parentPath)) {
    try {
      window.sessionStorage.setItem(MONTH_PLAN_TOPBAR_RETURN_KEY, '1')
    } catch {
      // Ignore storage failures; navigation should still work.
    }
  }
  void router.push(parentPath)
}

function isMonthPlanEditorReturn(parentPath: string) {
  return (
    parentPath === '/employee/month-plans' &&
    /^\/employee\/month-plans\/(?:new|\d+)\/edit$/.test(route.path)
  ) || (
    parentPath === '/leader/my-month-plans' &&
    /^\/leader\/my-month-plans\/(?:new|\d+)\/edit$/.test(route.path)
  )
}

watch(() => authStore.token, async (token) => {
  if (!token) {
    messageStore.reset()
    return
  }
  await messageStore.refresh().catch(() => undefined)
  messageStore.startPolling()
}, { immediate: true })

onMounted(() => {
  document.addEventListener('click', closeNotificationOnOutsideClick)
  void refreshHealth()
  healthTimer = window.setInterval(refreshHealth, 60_000)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', closeNotificationOnOutsideClick)
  if (healthTimer) window.clearInterval(healthTimer)
  messageStore.stopPolling()
})

async function refreshHealth() {
  try {
    const health = await getHealth()
    systemHealthy.value = health.status === 'UP'
  } catch {
    systemHealthy.value = false
  }
}

async function logout() {
  await authStore.logout()
  messageStore.reset()
  notificationOpen.value = false
  ElMessage.closeAll()
  ElMessage.success('退出成功')
  await router.replace('/login')
}

async function switchWorkspace(command: string | number | object) {
  if (typeof command !== 'string') return
  sidebarOpen.value = false
  await router.push(command)
}

async function toggleNotification() {
  notificationOpen.value = !notificationOpen.value
  if (notificationOpen.value) await messageStore.refresh(true)
}

async function openNotification(message: UserMessage) {
  await messageStore.markRead(message)
  notificationOpen.value = false
  if (message.route) await router.push(message.route)
}

async function markAllRead() {
  const updated = await messageStore.markAllRead()
  ElMessage.success(updated ? `已将 ${updated} 条消息标为已读` : '当前没有未读消息')
}

async function openMessageCenter() {
  notificationOpen.value = false
  await router.push('/messages')
}

function menuBadge(path: string) {
  return messageStore.badgeFor(path)
}

function badgeText(count: number) {
  return count > 99 ? '99+' : String(count)
}

function formatMessageTime(value?: string) {
  if (!value) return '刚刚'
  return value.replace('T', ' ').slice(0, 16)
}

function closeNotificationOnOutsideClick(event: MouseEvent) {
  if (!notificationOpen.value || notificationWrap.value?.contains(event.target as Node)) return
  notificationOpen.value = false
}
</script>
