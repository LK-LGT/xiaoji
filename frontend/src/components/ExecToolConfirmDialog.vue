/**
 * P1-3 执行类指令的两阶段确认对话框。
 *
 * 为什么是这个组件：执行类指令（催办 / 导出 / 统计）会**改变外部状态**
 * —— 发通知、出文件、落统计。把参数显式回显给用户确认，是把
 * 「机器误解」变成「人可拦截」的最低成本防线。
 *
 * 两阶段协议（组件强制）：
 * 1. ``prepare`` —— 只产出执行计划 + 一次性确认令牌，**无任何副作用**；
 * 2. ``confirm`` —— 携带令牌真正执行。
 *
 * ⚠️ 三条硬约束：
 * 1. **没有令牌就没有副作用** —— 组件不提供"跳过确认直接执行"的路径；
 * 2. **影响范围必须完整回显** —— 受影响的员工 / 部门逐条列出，
 *    不允许只显示一句"将影响 N 人"；
 * 3. **令牌一次性且短时效** —— 过期后必须重新走 prepare，
 *    组件不做"自动续期重试"，避免用户确认的其实不是他看到的那份计划。
 */
<template>
  <el-dialog
    v-model="visible"
    title="执行确认"
    width="min(680px, calc(100vw - 32px))"
    append-to-body
    align-center
    :close-on-click-modal="false"
    @closed="reset"
  >
    <!-- 第一阶段：生成计划 -->
    <template v-if="!plan">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        :title="`即将执行「${toolLabel}」`"
        :description="toolHint"
      />

      <el-form label-position="top" class="form-gap">
        <el-form-item label="考核任务 ID" required>
          <el-input v-model="form.task_id" placeholder="请输入考核任务 ID" />
        </el-form-item>

        <el-form-item v-if="tool === 'exec_remind'" label="催办对象（员工 ID，逗号分隔）" required>
          <el-input v-model="form.targetsText" placeholder="例如：emp001, emp002" />
        </el-form-item>

        <el-form-item v-if="tool === 'exec_remind'" label="催办附言">
          <el-input
            v-model="form.message"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            placeholder="留空则使用默认催办文案"
          />
        </el-form-item>

        <el-form-item v-if="tool === 'exec_export'" label="导出范围">
          <el-radio-group v-model="form.scope">
            <el-radio-button label="self">本人</el-radio-button>
            <el-radio-button label="department">本部门</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="tool === 'exec_stats'" label="统计范围">
          <el-radio-group v-model="form.scope" disabled>
            <el-radio-button label="department">本部门</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
    </template>

    <!-- 第二阶段：确认计划 -->
    <template v-else>
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="请核对以下执行计划"
        description="确认后才会真正执行。确认令牌一次性且 5 分钟内有效，过期需重新发起。"
      />

      <div class="plan-block">
        <div class="plan-row">
          <span class="plan-label">将要执行</span>
          <span class="plan-value">{{ plan.summary }}</span>
        </div>
        <div class="plan-row">
          <span class="plan-label">工具</span>
          <span class="plan-value">{{ toolLabel }}</span>
        </div>
        <div class="plan-row">
          <span class="plan-label">令牌有效期至</span>
          <span class="plan-value">{{ formatTime(plan.expiresAt) }}</span>
        </div>
      </div>

      <div v-if="impactRows.length" class="impact-block">
        <h4 class="impact-title">
          受影响对象
          <el-tag size="small" effect="plain">{{ impactRows.length }} 项</el-tag>
        </h4>
        <el-table :data="impactRows" size="small" max-height="240">
          <el-table-column label="姓名" min-width="120">
            <template #default="{ row }">{{ row.employee_name || '—' }}</template>
          </el-table-column>
          <el-table-column label="对象" min-width="140">
            <template #default="{ row }">{{ row.employee_id || row.department_id || '—' }}</template>
          </el-table-column>
          <el-table-column label="当前状态" min-width="110">
            <template #default="{ row }">
              <span class="muted">{{ row.status || '—' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <p v-else class="impact-empty">本计划未列出具体受影响对象。</p>
    </template>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        v-if="!plan"
        type="primary"
        :loading="preparing"
        @click="run(prepare)"
      >
        生成执行计划
      </el-button>
      <template v-else>
        <el-button :loading="preparing" @click="run(prepare)">重新生成</el-button>
        <el-button type="danger" :loading="confirming" @click="run(confirm)">
          确认执行
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  execToolsApi,
  type ExecPlan,
  type ExecToolName,
} from '@/api/p0p1'

const props = defineProps<{
  /** 执行类工具名 */
  tool: ExecToolName
  /** 预填的考核任务 ID */
  taskId?: string
  /** 预填的催办目标 */
  targets?: string[]
}>()

const emit = defineEmits<{
  (e: 'executed', result: Record<string, unknown>): void
  (e: 'update:modelValue', value: boolean): void
}>()

const visible = computed({
  get: () => visibleInternal.value,
  set: (v: boolean) => {
    visibleInternal.value = v
    emit('update:modelValue', v)
  },
})
const visibleInternal = ref(false)

const preparing = ref(false)
const confirming = ref(false)
const error = ref('')
const plan = ref<ExecPlan | null>(null)
/** 执行类工具令牌（会话换来的，绑定用户与任务）。 */
const bearerToken = ref('')

const form = reactive({
  task_id: '',
  targetsText: '',
  message: '',
  scope: 'self' as 'self' | 'department',
})

const toolLabel = computed(() => {
  const map: Record<ExecToolName, string> = {
    exec_remind: '催办提醒',
    exec_export: '数据导出',
    exec_stats: '部门统计',
  }
  return map[props.tool]
})

const toolHint = computed(() => {
  const map: Record<ExecToolName, string> = {
    exec_remind: '将向指定员工发送考核催办提醒。',
    exec_export: '将导出本人在该考核任务下的绩效数据。',
    exec_stats: '将统计本部门在该考核任务下的绩效分布。',
  }
  return map[props.tool]
})

/** 受影响对象（后端返回的是对象数组，不是 Record）。 */
const impactRows = computed(() => {
  const impact = plan.value?.impact
  if (!impact) return []
  if (Array.isArray(impact)) return impact as Record<string, unknown>[]
  return Object.values(impact as Record<string, unknown>)
})

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.replace('T', ' ').slice(0, 19)
  return d.toLocaleString('zh-CN', { hour12: false })
}

function reset() {
  plan.value = null
  bearerToken.value = ''
  error.value = ''
  form.task_id = props.taskId ?? ''
  form.targetsText = (props.targets ?? []).join(', ')
  form.message = ''
  form.scope = props.tool === 'exec_stats' ? 'department' : 'self'
}

async function run(fn: () => Promise<unknown>) {
  error.value = ''
  try {
    await fn()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    ElMessage.error(error.value)
  }
}

async function prepare() {
  if (!form.task_id.trim()) {
    throw new Error('请填写考核任务 ID')
  }
  const targets = form.targetsText
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)

  preparing.value = true
  try {
    // 第一步：用登录会话换一个执行类令牌（只含本次工具授权）。
    const token = await execToolsApi.token(
      props.tool,
      form.task_id.trim(),
      form.scope,
    )
    bearerToken.value = token.token

    // 第二步：生成执行计划 —— **无副作用**。
    const payload: {
      task_id: string
      targets?: string[]
      message?: string
      scope?: string
    } = { task_id: form.task_id.trim(), scope: form.scope }
    if (props.tool === 'exec_remind') {
      if (!targets.length) throw new Error('请至少填写一位催办对象')
      payload.targets = targets
      payload.message = form.message
    }
    plan.value = await execToolsApi.prepare(props.tool, payload, token.token)
  } finally {
    preparing.value = false
  }
}

async function confirm() {
  if (!plan.value) return
  confirming.value = true
  try {
    const result = await execToolsApi.confirm(
      props.tool,
      { task_id: form.task_id.trim(), confirm_token: plan.value.confirmToken },
      bearerToken.value,
    )
    ElMessage.success(`${toolLabel.value}已执行`)
    emit('executed', result)
    visible.value = false
  } finally {
    confirming.value = false
  }
}

watch(
  () => props.tool,
  () => {
    reset()
  },
)

defineExpose({
  open: () => {
    reset()
    visible.value = true
  },
})
</script>

<style scoped>
.form-gap {
  margin-top: 12px;
}
.plan-block {
  margin: 12px 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
}
.plan-row {
  display: flex;
  gap: 12px;
  padding: 4px 0;
  font-size: 13px;
}
.plan-label {
  width: 96px;
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
}
.plan-value {
  flex: 1;
  word-break: break-all;
}
.impact-block {
  margin-top: 12px;
}
.impact-title {
  margin: 0 0 8px;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.impact-empty {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
.muted {
  color: var(--el-text-color-secondary);
}
</style>
