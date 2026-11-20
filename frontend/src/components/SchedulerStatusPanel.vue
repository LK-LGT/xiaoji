/**
 * P1-2 周期调度（Period Scheduler）状态面板。
 *
 * 用途：HR / 系统管理员查看考核周期的**自动开启**、**截止倒计时**与
 * **逾期告警**的运行态，并能手动触发一轮调度。
 *
 * 三条硬约束：
 * 1. **手动触发是有副作用的**（会推进任务状态并写通知）——
 *    必须二次确认，确认框里明确写出"会推进任务状态并发送提醒"。
 * 2. **非 HR 不可见** —— 后端 `scheduler/status` 与 `/run` 都要求 HR，
 *    组件在无权限时**静默降级**（不渲染），而不是渲染后再报错。
 * 3. **运行态只反映"调度器"本身** —— 不代表考核任务已全部完成，
 *    面板文案不得暗示"一切正常"。
 */
<template>
  <section v-if="visible" class="scheduler-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">周期调度</span>
        <h3 class="panel-title">考核周期调度运行态</h3>
        <p class="panel-subtitle">
          {{ summaryText }}
        </p>
      </div>
      <div class="head-actions">
        <el-button size="small" :loading="loading" @click="run(load)">刷新</el-button>
        <el-button
          size="small"
          type="primary"
          :loading="running"
          @click="run(triggerRun)"
        >
          立即执行一轮
        </el-button>
      </div>
    </header>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-skeleton v-if="loading && !status" :rows="2" animated />

    <template v-else-if="status">
      <el-alert
        v-if="!status.enabled"
        type="warning"
        :closable="false"
        show-icon
        title="周期调度当前未启用"
        description="自动开启任务、截止倒计时与逾期提醒不会自动执行，可手动触发一轮或联系管理员启用。"
      />

      <div class="status-grid">
        <div class="status-card">
          <span class="status-label">调度器</span>
          <strong class="status-value">
            <el-tag :type="status.enabled ? 'success' : 'info'" size="small" effect="dark">
              {{ status.enabled ? '已启用' : '未启用' }}
            </el-tag>
          </strong>
          <span class="status-hint">
            {{ status.running ? '后台线程运行中' : '后台线程未运行' }}
          </span>
        </div>

        <div class="status-card">
          <span class="status-label">运行次数</span>
          <strong class="status-value">{{ status.runCount }}</strong>
          <span class="status-hint">间隔 {{ status.intervalSeconds }} 秒</span>
        </div>

        <div class="status-card">
          <span class="status-label">上次运行</span>
          <strong class="status-value">{{ formatTime(status.lastRunAt) }}</strong>
          <span class="status-hint">
            <template v-if="status.consecutiveFailures > 0" class="fail-hint">
              连续失败 {{ status.consecutiveFailures }} 次
            </template>
            <template v-else>无连续失败</template>
          </span>
        </div>

        <div class="status-card">
          <span class="status-label">下次运行</span>
          <strong class="status-value">{{ formatTime(status.nextRunAt) }}</strong>
          <span class="status-hint">{{ status.started ? '已启动' : '未启动' }}</span>
        </div>
      </div>

      <!-- 上一轮结果 -->
      <div v-if="lastRun" class="last-run">
        <h4 class="section-title">上一轮执行结果</h4>
        <div class="run-metrics">
          <span>自动开启任务 <strong>{{ runValue('started_tasks') }}</strong> 个</span>
          <span>倒计时提醒 <strong>{{ runValue('countdown_total') }}</strong> 条</span>
          <span>逾期提醒 <strong>{{ runValue('overdue_total') }}</strong> 条</span>
          <span v-if="lastRun.ran_at" class="muted">执行于 {{ runTime('ran_at') }}</span>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { schedulerApi, type SchedulerStatus } from '@/api/p0p1'
import { useAuthStore } from '@/stores/auth'

const props = withDefaults(
  defineProps<{
    /** 是否自动加载 */
    autoLoad?: boolean
  }>(),
  { autoLoad: true },
)

const authStore = useAuthStore()

const loading = ref(false)
const running = ref(false)
const error = ref('')
const status = ref<SchedulerStatus | null>(null)
const lastRun = ref<Record<string, unknown> | null>(null)

/** 只有 HR 能看调度状态（后端 `scheduler/status` 要求 HR）。 */
const visible = computed(() => (authStore.user?.roles || []).includes('hr'))

const summaryText = computed(() => {
  if (!status.value) return '加载中…'
  if (!status.value.enabled) return '周期调度未启用'
  return `已运行 ${status.value.runCount} 轮，下次 ${formatTime(status.value.nextRunAt)}`
})

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.replace('T', ' ').slice(0, 19)
  return d.toLocaleString('zh-CN', { hour12: false })
}

/** `lastRun` 是后端返回的动态结构，字段按未知类型取出后转成字符串。 */
function runValue(key: string): string {
  const raw = lastRun.value?.[key]
  if (raw === null || raw === undefined) return '—'
  return String(raw)
}

function runTime(key: string): string {
  const raw = lastRun.value?.[key]
  return formatTime(typeof raw === 'string' ? raw : null)
}

async function run(fn: () => Promise<unknown>) {
  loading.value = true
  error.value = ''
  try {
    await fn()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function load() {
  if (!visible.value) return
  const data = await schedulerApi.status()
  status.value = data
  if (data.lastResult && typeof data.lastResult === 'object') {
    lastRun.value = data.lastResult as Record<string, unknown>
  }
}

async function triggerRun() {
  await ElMessageBox.confirm(
    '手动触发会推进到期的考核任务状态，并投递截止倒计时与逾期提醒。确认执行？',
    '确认执行一轮周期调度',
    { type: 'warning', confirmButtonText: '确认执行' },
  )
  running.value = true
  try {
    const data = await schedulerApi.run()
    lastRun.value = data
    ElMessage.success('周期调度已执行一轮')
    await load()
  } finally {
    running.value = false
  }
}

watch(
  () => props.autoLoad,
  () => {
    if (props.autoLoad) void run(load)
  },
  { immediate: true },
)
</script>

<style scoped>
.scheduler-panel {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
  background: var(--el-bg-color);
}
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.head-text {
  min-width: 0;
}
.eyebrow {
  font-size: 12px;
  color: var(--el-color-info);
  letter-spacing: 1px;
}
.panel-title {
  margin: 2px 0 4px;
  font-size: 16px;
}
.panel-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-top: 12px;
}
.status-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.status-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.status-value {
  font-size: 15px;
}
.status-hint {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
.fail-hint {
  color: var(--el-color-danger);
}
.section-title {
  margin: 16px 0 6px;
  font-size: 13px;
}
.run-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
}
.muted {
  color: var(--el-text-color-placeholder);
}
</style>
