/**
 * P0-1 互评（Peer Review）面板。
 *
 * 一个组件覆盖互评的**三种视角**（后端按登录者身份返回不同可见范围）：
 *   1. **被评人 / 普通员工**：只看到进度条（不泄露他人分数与意见）；
 *   2. **互评人**：能看到并提交属于自己的那一项；
 *   3. **主管 / HR**：分派互评人、查看回收进度、催办逾期。
 *
 * 三条硬约束（组件层面保证）：
 * 1. **普通员工视角永远拿不到他人互评分数** —— 组件只渲染接口返回的字段，
 *    不做任何"拼装"；后端对非互评人/主管返回 `score: null`。
 * 2. **互评意见提交后不可改** —— 已提交项渲染为只读态，按钮禁用。
 * 3. **互评不直接改分** —— 互评意见是主管评分的参考材料，
 *    组件里不出现"自动采纳并改分"的入口。
 */
<template>
  <section class="peer-review-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">互评</span>
        <h3 class="panel-title">互评进度与意见</h3>
        <p class="panel-subtitle">
          {{ summaryText }}
        </p>
      </div>
      <div class="head-actions">
        <el-button
          v-if="canRemind"
          size="small"
          :loading="reminding"
          @click="run(remindOverdue)"
        >
          催办逾期
        </el-button>
        <el-button
          v-if="canAssign"
          size="small"
          type="primary"
          plain
          @click="assignVisible = true"
        >
          分派互评人
        </el-button>
      </div>
    </header>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-skeleton v-if="loading" :rows="3" animated />

    <template v-else-if="list">
      <!-- 进度条：任何有权限的人都能看到"到齐了没有"，但看不到内容 -->
      <div class="progress-block">
        <el-progress
          :percentage="progressPercent"
          :status="list.complete ? 'success' : undefined"
          :stroke-width="10"
        />
        <p class="progress-note">
          共 {{ list.total }} 项，已提交 {{ list.submitted }} 项，待提交
          {{ list.pending }} 项
        </p>
      </div>

      <el-table :data="list.items" size="small" class="peer-table">
        <el-table-column label="互评人" width="140">
          <template #default="{ row }">
            <span>{{ row.reviewer_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="plain">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分数" width="90">
          <template #default="{ row }">
            <span v-if="row.score !== null && row.score !== undefined">
              {{ row.score }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="意见">
          <template #default="{ row }">
            <span v-if="row.comment">{{ row.comment }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="截止" width="150">
          <template #default="{ row }">
            <span :class="{ overdue: isOverdue(row) }">
              {{ formatTime(row.due_at) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button
              v-if="isMine(row) && row.status !== 'submitted'"
              link
              type="primary"
              @click="startSubmit(row)"
            >
              填写互评
            </el-button>
            <span v-else-if="isMine(row)" class="muted">已提交</span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!list.items.length"
        :image-size="70"
        description="本条记录尚未分派互评人"
      />
    </template>

    <!-- 互评提交弹窗 -->
    <el-dialog
      v-model="submitVisible"
      title="提交互评意见"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="互评意见是主管评分的参考材料，不会直接改变任何人的分数。"
      />
      <el-form label-position="top" class="submit-form">
        <el-form-item label="互评分（0–100，可留空表示只给意见）">
          <el-input-number
            v-model="submitForm.score"
            :min="0"
            :max="100"
            :precision="1"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="互评意见" required>
          <el-input
            v-model="submitForm.comment"
            type="textarea"
            :rows="4"
            maxlength="2000"
            show-word-limit
            placeholder="请基于可观察到的事实填写客观意见（至少 5 个字）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="submitVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="run(submitPeer)">
          提交互评
        </el-button>
      </template>
    </el-dialog>

    <!-- 分派互评人弹窗（主管 / HR） -->
    <el-dialog
      v-model="assignVisible"
      title="分派互评人"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top">
        <el-form-item label="互评人（多人用逗号分隔员工 ID）" required>
          <el-input
            v-model="assignForm.reviewerIds"
            placeholder="例如：emp001, emp002"
          />
        </el-form-item>
        <el-form-item label="互评截止时间">
          <el-date-picker
            v-model="assignForm.dueAt"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="留空表示不设截止"
            class="full-width"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignVisible = false">取消</el-button>
        <el-button type="primary" :loading="assigning" @click="run(assignPeers)">
          确认分派
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  peerReviewApi,
  type PeerReviewItem,
  type PeerReviewList,
} from '@/api/p0p1'
import { useAuthStore } from '@/stores/auth'

const props = withDefaults(
  defineProps<{
    /** 考核记录 ID */
    recordId: string | null
    /** 考核任务 ID（催办用，仅 HR 可用） */
    taskId?: string | null
    /** 当前登录者 ID —— 留空则从登录态取 */
    currentUserId?: string | null
    /** 是否允许分派互评人（主管 / HR） */
    canAssign?: boolean
    /** 是否允许催办逾期（HR） */
    canRemind?: boolean
    /** 是否自动加载 */
    autoLoad?: boolean
  }>(),
  { autoLoad: true },
)

const emit = defineEmits<{
  (e: 'loaded', list: PeerReviewList): void
  (e: 'submitted'): void
}>()

const authStore = useAuthStore()

const loading = ref(false)
const error = ref('')
const list = ref<PeerReviewList | null>(null)

const submitVisible = ref(false)
const submitting = ref(false)
const submittingRow = ref<PeerReviewItem | null>(null)
const submitForm = ref<{ score: number | null; comment: string }>({
  score: null,
  comment: '',
})

const assignVisible = ref(false)
const assigning = ref(false)
const assignForm = ref<{ reviewerIds: string; dueAt: string | null }>({
  reviewerIds: '',
  dueAt: null,
})

const reminding = ref(false)

const summaryText = computed(() => {
  if (!list.value) return '加载中…'
  if (!list.value.total) return '本条记录尚未分派互评人'
  if (list.value.complete) return '互评已全部回收'
  return `互评进行中，已回收 ${list.value.submitted}/${list.value.total}`
})

const progressPercent = computed(() => {
  const total = list.value?.total ?? 0
  if (!total) return 0
  return Math.round(((list.value?.submitted ?? 0) / total) * 100)
})

function statusType(status: string): string {
  if (status === 'submitted') return 'success'
  if (status === 'overdue') return 'danger'
  return 'warning'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待填写',
    assigned: '待填写',
    submitted: '已提交',
    overdue: '已逾期',
  }
  return map[status] ?? status
}

function isMine(row: PeerReviewItem): boolean {
  const me = props.currentUserId ?? authStore.user?.userId ?? ''
  return !!me && row.reviewer_id === me
}

function isOverdue(row: PeerReviewItem): boolean {
  if (row.status === 'submitted') return false
  if (!row.due_at) return false
  return new Date(row.due_at).getTime() < Date.now()
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 16)
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
  if (!props.recordId) return
  const data = await peerReviewApi.list(props.recordId)
  list.value = data
  emit('loaded', data)
}

function startSubmit(row: PeerReviewItem) {
  submittingRow.value = row
  submitForm.value = { score: row.score ?? null, comment: row.comment ?? '' }
  submitVisible.value = true
}

async function submitPeer() {
  const row = submittingRow.value
  if (!row) return
  const comment = submitForm.value.comment.trim()
  if (comment.length < 5) {
    throw new Error('互评意见至少 5 个字')
  }
  submitting.value = true
  try {
    await peerReviewApi.submit(row.id, {
      score: submitForm.value.score,
      comment,
    })
    ElMessage.success('互评已提交')
    submitVisible.value = false
    emit('submitted')
    await load()
  } finally {
    submitting.value = false
  }
}

async function assignPeers() {
  if (!props.recordId) return
  const ids = assignForm.value.reviewerIds
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (!ids.length) {
    throw new Error('请至少填写一位互评人')
  }
  assigning.value = true
  try {
    await peerReviewApi.assign(props.recordId, {
      reviewer_ids: ids,
      due_at: assignForm.value.dueAt,
    })
    ElMessage.success('互评人已分派')
    assignVisible.value = false
    assignForm.value = { reviewerIds: '', dueAt: null }
    await load()
  } finally {
    assigning.value = false
  }
}

async function remindOverdue() {
  if (!props.taskId) {
    throw new Error('缺少考核任务 ID，无法催办')
  }
  reminding.value = true
  try {
    const res = await peerReviewApi.remind(props.taskId)
    ElMessage.success(`已发出 ${res.reminded} 条催办`)
  } finally {
    reminding.value = false
  }
}

watch(
  () => props.recordId,
  () => {
    if (props.autoLoad) void load()
  },
  { immediate: true },
)
</script>

<style scoped>
.peer-review-panel {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 16px;
  margin-top: 16px;
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
.progress-block {
  margin-bottom: 12px;
}
.progress-note {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.muted {
  color: var(--el-text-color-placeholder);
}
.overdue {
  color: var(--el-color-danger);
}
.submit-form {
  margin-top: 12px;
}
.full-width {
  width: 100%;
}
</style>
