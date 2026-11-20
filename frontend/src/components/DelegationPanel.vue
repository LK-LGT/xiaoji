/**
 * P1-4 代理授权（Delegation）面板。
 *
 * 用途：主管请假 / 外出期间，把指定范围的管理权限临时交给受托人；
 * 到期自动失效，也可以提前撤销。
 *
 * 四条硬约束：
 * 1. **有效期是硬闸门** —— 前端展示的"剩余时间"到点后状态变为已失效，
 *    组件**不提供任何"续期绕过"入口**（续期 = 重新创建一条委托）。
 * 2. **委托范围不得超过委托人自己的权限** —— 后端在创建时校验，
 *    前端只如实呈现错误，不做"绕过校验"的提示。
 * 3. **到期自动失效不依赖后台任务** —— 面板文案必须说清楚这一点，
 *    否则用户会以为"没人手动撤销就永远有效"。
 * 4. **可审计** —— 每条委托都显示委托人、受托人、事由、生效与失效时间。
 */
<template>
  <section class="delegation-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">代理授权</span>
        <h3 class="panel-title">委托与代理范围</h3>
        <p class="panel-subtitle">{{ summaryText }}</p>
      </div>
      <div class="head-actions">
        <el-button size="small" @click="run(load)">刷新</el-button>
        <el-button size="small" type="primary" plain @click="openCreate">
          新建委托
        </el-button>
      </div>
    </header>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <!-- 我当前通过委托获得的范围 -->
    <el-alert
      v-if="scope && scope.count > 0"
      type="success"
      :closable="false"
      show-icon
      class="scope-alert"
      :title="`我当前通过代理获得 ${scope.count} 名员工的管理范围（${scope.activeDelegations} 条生效委托）`"
    >
      <p class="scope-note">
        委托到期后该范围自动缩小，无需任何人手动撤销。
      </p>
    </el-alert>

    <el-skeleton v-if="loading && !rows.length" :rows="3" animated />

    <template v-else>
      <el-tabs v-model="tab" class="delegation-tabs">
        <el-tab-pane label="我委托出去的" name="delegator">
          <el-table :data="rows.filter((r) => r.delegatorId === myId)" size="small">
            <el-table-column label="受托人" min-width="140">
              <template #default="{ row }">{{ row.delegateId }}</template>
            </el-table-column>
            <el-table-column label="范围" min-width="130">
              <template #default="{ row }">{{ scopeTypeLabel(row.scopeType) }}</template>
            </el-table-column>
            <el-table-column label="事由" min-width="140">
              <template #default="{ row }">
                <span class="muted">{{ row.reason || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="有效期" min-width="200">
              <template #default="{ row }">
                {{ formatTime(row.startsAt) }} → {{ formatTime(row.endsAt) }}
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="statusType(row)" size="small" effect="plain">
                  {{ statusLabel(row) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90">
              <template #default="{ row }">
                <el-button
                  v-if="canRevoke(row)"
                  link
                  type="danger"
                  @click="run(() => revoke(row))"
                >
                  撤销
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty
            v-if="!rows.filter((r) => r.delegatorId === myId).length"
            :image-size="70"
            description="尚未委托任何人"
          />
        </el-tab-pane>

        <el-tab-pane label="委托给我的" name="delegate">
          <el-table :data="rows.filter((r) => r.delegateId === myId)" size="small">
            <el-table-column label="委托人" min-width="140">
              <template #default="{ row }">{{ row.delegatorId }}</template>
            </el-table-column>
            <el-table-column label="范围" min-width="130">
              <template #default="{ row }">{{ scopeTypeLabel(row.scopeType) }}</template>
            </el-table-column>
            <el-table-column label="事由" min-width="140">
              <template #default="{ row }">
                <span class="muted">{{ row.reason || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="有效期" min-width="200">
              <template #default="{ row }">
                {{ formatTime(row.startsAt) }} → {{ formatTime(row.endsAt) }}
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="statusType(row)" size="small" effect="plain">
                  {{ statusLabel(row) }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty
            v-if="!rows.filter((r) => r.delegateId === myId).length"
            :image-size="70"
            description="当前没有接受他人委托"
          />
        </el-tab-pane>
      </el-tabs>
    </template>

    <!-- 新建委托 -->
    <el-dialog
      v-model="createVisible"
      title="新建代理授权"
      width="min(560px, calc(100vw - 32px))"
      :close-on-click-modal="false"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="委托到期后权限自动失效"
        description="单次委托最长 90 天。委托范围不能超过你自己管理的范围 —— 系统会在创建时校验。"
      />
      <el-form label-position="top" class="create-form">
        <el-form-item label="受托人" required>
          <el-select
            v-model="form.delegate_id"
            filterable
            remote
            :remote-method="searchCandidates"
            :loading="searching"
            placeholder="搜索并选择受托人"
            class="full-width"
          >
            <el-option
              v-for="c in candidates"
              :key="c.id"
              :label="c.name"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="委托范围" required>
          <el-select v-model="form.scope_type" class="full-width">
            <el-option label="我的直属下属" value="direct_reports" />
            <el-option label="我管理的部门" value="department" />
            <el-option label="指定员工（白名单）" value="explicit" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.scope_type === 'explicit'" label="指定员工 ID（逗号分隔）" required>
          <el-input v-model="form.scope_value" placeholder="例如：emp001, emp002" />
        </el-form-item>
        <el-form-item label="生效时间" required>
          <el-date-picker
            v-model="form.starts_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="留空表示立即生效"
            class="full-width"
          />
        </el-form-item>
        <el-form-item label="失效时间" required>
          <el-date-picker
            v-model="form.ends_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="留空表示立即失效"
            class="full-width"
          />
        </el-form-item>
        <el-form-item label="委托事由">
          <el-input
            v-model="form.reason"
            type="textarea"
            :rows="2"
            maxlength="200"
            show-word-limit
            placeholder="例如：年假外出，期间由他人代为处理评分"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="run(create)">
          确认委托
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  delegationApi,
  type DelegationRow,
  type DelegationScope,
} from '@/api/p0p1'
import { http } from '@/api/http'
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
const error = ref('')
const rows = ref<DelegationRow[]>([])
const scope = ref<DelegationScope | null>(null)
const tab = ref('delegator')

const createVisible = ref(false)
const creating = ref(false)
const candidates = ref<{ id: string; name: string }[]>([])
const searching = ref(false)

const form = reactive({
  delegate_id: '',
  scope_type: 'direct_reports',
  scope_value: '',
  starts_at: '',
  ends_at: '',
  reason: '',
})

const myId = computed(() => authStore.user?.userId ?? '')

const summaryText = computed(() => {
  if (!rows.value.length) return '暂无委托记录'
  const active = rows.value.filter((r) => isActive(r)).length
  return `共 ${rows.value.length} 条委托，其中 ${active} 条生效中`
})

function isActive(row: DelegationRow): boolean {
  if (row.revokedAt) return false
  const now = Date.now()
  const start = new Date(row.startsAt).getTime()
  const end = new Date(row.endsAt).getTime()
  return now >= start && now <= end
}

function statusType(row: DelegationRow): string {
  if (row.revokedAt) return 'info'
  return isActive(row) ? 'success' : 'warning'
}

function statusLabel(row: DelegationRow): string {
  if (row.revokedAt) return '已撤销'
  if (isActive(row)) return '生效中'
  const now = Date.now()
  if (now < new Date(row.startsAt).getTime()) return '未开始'
  return '已到期'
}

function scopeTypeLabel(scopeType: string): string {
  const map: Record<string, string> = {
    direct_reports: '直属下属',
    department: '管理部门',
    explicit: '指定员工',
  }
  return map[scopeType] ?? scopeType
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.replace('T', ' ').slice(0, 19)
  return d.toLocaleString('zh-CN', { hour12: false })
}

function canRevoke(row: DelegationRow): boolean {
  return row.delegatorId === myId.value && !row.revokedAt && isActive(row)
}

async function run(fn: () => Promise<unknown>) {
  loading.value = true
  error.value = ''
  try {
    await fn()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

async function load() {
  const [list, myScope] = await Promise.all([
    delegationApi.list('all'),
    delegationApi.myScope(),
  ])
  rows.value = list
  scope.value = myScope
}

async function searchCandidates(keyword: string) {
  searching.value = true
  try {
    const data = await http.get<unknown, { id: string; name: string }[]>(
      '/review-center/people',
      { params: { keyword } },
    )
    candidates.value = (data ?? []).filter((c) => c.id !== myId.value)
  } catch {
    candidates.value = []
  } finally {
    searching.value = false
  }
}

function openCreate() {
  form.delegate_id = ''
  form.scope_type = 'direct_reports'
  form.scope_value = ''
  form.starts_at = ''
  form.ends_at = ''
  form.reason = ''
  createVisible.value = true
  void searchCandidates('')
}

async function create() {
  if (!form.delegate_id) throw new Error('请选择受托人')
  if (!form.starts_at || !form.ends_at) throw new Error('请填写生效与失效时间')
  creating.value = true
  try {
    await delegationApi.create({
      delegate_id: form.delegate_id,
      scope_type: form.scope_type,
      scope_value: form.scope_value,
      reason: form.reason,
      starts_at: form.starts_at,
      ends_at: form.ends_at,
    })
    ElMessage.success('代理授权已创建')
    createVisible.value = false
    await load()
  } finally {
    creating.value = false
  }
}

async function revoke(row: DelegationRow) {
  await ElMessageBox.confirm(
    '撤销后受托人立即失去该范围内的管理权限。确认撤销？',
    '确认撤销委托',
    { type: 'warning', confirmButtonText: '确认撤销' },
  )
  await delegationApi.revoke(row.id)
  ElMessage.success('委托已撤销')
  await load()
}

if (props.autoLoad) {
  void run(load)
}
</script>

<style scoped>
.delegation-panel {
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
.scope-alert {
  margin-bottom: 12px;
}
.scope-note {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.create-form {
  margin-top: 12px;
}
.full-width {
  width: 100%;
}
.muted {
  color: var(--el-text-color-secondary);
}
</style>
