<template>
  <section class="page-panel feishu-sync-page" v-loading="loading">
    <div class="page-header">
      <div>
        <span class="eyebrow">FEISHU ORGANIZATION SYNC</span>
        <h1 class="page-title">飞书组织同步</h1>
        <p class="page-subtitle">
          触发部门与员工同步，查看批次进度、失败分类、数据质量与对账结果。同步在后台执行，页面可安全离开。
        </p>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :loading="refreshing" @click="refreshAll">刷新</el-button>
        <el-button :icon="RefreshRight" :loading="recovering" @click="recoverStale">恢复僵死批次</el-button>
        <el-button type="primary" :icon="Refresh" :loading="triggering" @click="triggerSync('full')">全量同步</el-button>
        <el-dropdown @command="triggerSync">
          <el-button :icon="ArrowDown" :disabled="triggering">
            增量同步<el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="incremental">增量同步</el-dropdown-item>
              <el-dropdown-item command="full">全量同步</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <el-alert
      v-if="status && !status.configured"
      type="warning"
      show-icon
      :closable="false"
      title="飞书同步尚未配置"
      description="缺少 app_id / app_secret / tenant_id，无法触发同步。请在服务端环境变量中补齐后重启服务。"
      class="mt16"
    />
    <el-alert
      v-else-if="status?.running"
      type="info"
      show-icon
      :closable="false"
      :title="runningTitle"
      description="正在后台拉取飞书部门与员工数据，完成后此处会自动更新。"
      class="mt16"
    />

    <div class="metric-grid mt16">
      <div class="metric">
        <span>链路状态</span>
        <strong>{{ status?.running ? '运行中' : status?.configured ? '就绪' : '未配置' }}</strong>
        <small>{{ schedulerSummary }}</small>
      </div>
      <div class="metric success">
        <span>上次成功</span>
        <strong>{{ formatTime(status?.lastSuccessAt) }}</strong>
        <small>{{ status?.lastBatchStatus ? `最近批次：${statusLabel(status.lastBatchStatus)}` : '暂无批次记录' }}</small>
      </div>
      <div class="metric warning">
        <span>待处理失败</span>
        <strong>{{ errorSummary.total }}</strong>
        <small>{{ errorSummary.deadLetter }} 条死信 · {{ errorSummary.pending }} 条待重试</small>
      </div>
      <div class="metric" :class="reconciliationMetricType">
        <span>数据质量</span>
        <strong>{{ reconciliationSummary.title }}</strong>
        <small>{{ reconciliationSummary.detail }}</small>
      </div>
    </div>

    <!--
      ⚠️ 「配了但没跑」是最容易漏的坑：
      .env 里 FEISHU_SYNC_ENABLED=true（配置开关），
      但调度线程没起来（进程内 running=false）——
      前者读配置，后者读线程，两者不一致时只有这里能看出来。
    -->
    <el-alert
      v-if="schedulerMisconfigured"
      type="warning"
      show-icon
      :closable="false"
      title="定时同步已配置，但调度线程未运行"
      description="请确认 .env 中 FEISHU_SYNC_ENABLED=true 后已重启服务进程；配置改动需要重启才会生效。"
      class="mt16"
    />

    <div class="section-card mt16">
      <div class="section-header">
        <div>
          <h2>同步游标</h2>
          <p>各资源上一次成功推进到的位置，用于增量同步的断点续传。</p>
        </div>
      </div>
      <el-table :data="cursorPage.records" empty-text="暂无游标记录" v-loading="cursorLoading">
        <el-table-column prop="resourceType" label="资源类型" min-width="140" />
        <el-table-column label="检查点" min-width="200">
          <template #default="{ row }">
            <code class="cursor-value">{{ row.checkpointValue || row.cursorValue || '—' }}</code>
          </template>
        </el-table-column>
        <el-table-column label="页令牌" min-width="180">
          <template #default="{ row }"><code class="cursor-value">{{ row.pageToken || '—' }}</code></template>
        </el-table-column>
        <el-table-column label="快照完整" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="row.snapshotComplete ? 'success' : 'info'" size="small">
              {{ row.snapshotComplete ? '完整' : '不完整' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lastBatchId" label="最近批次" min-width="180">
          <template #default="{ row }"><code class="cursor-value">{{ row.lastBatchId || '—' }}</code></template>
        </el-table-column>
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatTime(row.updatedAt) }}</template>
        </el-table-column>
      </el-table>
      <div class="pagination-row">
        <span>共 {{ cursorPage.total }} 个游标</span>
        <el-pagination
          v-model:current-page="cursorQuery.page"
          :page-size="cursorQuery.pageSize"
          layout="prev, pager, next"
          :total="cursorPage.total"
          @current-change="loadCursors"
        />
      </div>
      <el-alert
        v-if="incompleteCursorCount > 0"
        type="warning"
        show-icon
        :closable="false"
        title="快照不完整，已跳过离职判定"
        description="最近一次同步有部门拉取失败，系统不会把缺失员工标记为离职/停用，以免接口抖动造成误停用。请修复失败部门后重跑全量同步。"
        class="mt16"
      />
    </div>

    <div class="section-card mt16">
      <div class="section-header">
        <div>
          <h2>同步批次</h2>
          <p>点击批次行可查看该批次的详细进度与对账结果。</p>
        </div>
        <div class="toolbar">
          <el-select v-model="batchQuery.status" clearable placeholder="状态" style="width: 130px" @change="loadBatches">
            <el-option v-for="option in batchStatusOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-select v-model="batchQuery.mode" clearable placeholder="模式" style="width: 120px" @change="loadBatches">
            <el-option label="全量" value="full" />
            <el-option label="增量" value="incremental" />
          </el-select>
        </div>
      </div>
      <el-table :data="batchPage.records" empty-text="暂无同步批次" v-loading="batchLoading" @row-click="openBatchDetail">
        <el-table-column prop="batchNo" label="批次号" min-width="190">
          <template #default="{ row }"><code class="cursor-value">{{ row.batchNo }}</code></template>
        </el-table-column>
        <el-table-column label="状态" width="120" align="center">
          <template #default="{ row }"><el-tag :type="batchStatusType(row.status)" size="small">{{ batchStatusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="模式" width="80" align="center">
          <template #default="{ row }">{{ row.mode === 'full' ? '全量' : '增量' }}</template>
        </el-table-column>
        <el-table-column label="触发" width="100" align="center">
          <template #default="{ row }">{{ triggerLabel(row.triggerType) }}</template>
        </el-table-column>
        <el-table-column label="阶段" min-width="180">
          <template #default="{ row }">
            <div class="phase-cell">
              <span>{{ row.phase || '—' }}</span>
              <small v-if="row.phaseDetail">{{ row.phaseDetail }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="部门" width="150" align="center">
          <template #default="{ row }">
            <span class="ok-text">{{ row.departmentsSynced }}</span> /
            <span class="danger-text">{{ row.departmentsFailed }}</span> /
            <span class="muted-text">{{ row.departmentsSkipped }}</span>
          </template>
        </el-table-column>
        <el-table-column label="员工" width="150" align="center">
          <template #default="{ row }">
            <span class="ok-text">{{ row.employeesSynced }}</span> /
            <span class="danger-text">{{ row.employeesFailed }}</span> /
            <span class="muted-text">{{ row.employeesSkipped }}</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="100" align="right">
          <template #default="{ row }">{{ row.durationMs != null ? `${(row.durationMs / 1000).toFixed(1)}s` : '—' }}</template>
        </el-table-column>
        <el-table-column label="开始时间" width="180">
          <template #default="{ row }">{{ formatTime(row.startedAt) }}</template>
        </el-table-column>
      </el-table>
      <div class="pagination-row">
        <span>共 {{ batchPage.total }} 个批次</span>
        <el-pagination
          v-model:current-page="batchQuery.page"
          :page-size="batchQuery.pageSize"
          layout="prev, pager, next"
          :total="batchPage.total"
          @current-change="loadBatches"
        />
      </div>
    </div>

    <div class="section-card mt16">
      <div class="section-header">
        <div>
          <h2>失败记录与死信</h2>
          <p>可自动重试的失败按指数退避重排；其它分类进入死信，等待人工处理。</p>
        </div>
        <div class="toolbar">
          <el-select v-model="errorQuery.status" clearable placeholder="状态" style="width: 130px" @change="loadErrors">
            <el-option label="待重试" value="pending" />
            <el-option label="重试中" value="retrying" />
            <el-option label="已解决" value="resolved" />
            <el-option label="已失败" value="failed" />
            <el-option label="死信" value="dead_letter" />
          </el-select>
          <el-select v-model="errorQuery.errorCategory" clearable placeholder="错误分类" style="width: 140px" @change="loadErrors">
            <el-option v-for="option in errorCategoryOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-button type="primary" plain :icon="RefreshRight" :loading="retrying" @click="retryFailed">立即重试到期项</el-button>
        </div>
      </div>
      <el-table :data="errorPage.records" empty-text="暂无失败记录" v-loading="errorLoading">
        <el-table-column label="资源" width="160">
          <template #default="{ row }">
            <div class="phase-cell">
              <span>{{ row.resourceType === 'employee' ? '员工' : '部门' }}</span>
              <small v-if="row.resourceId"><code class="cursor-value">{{ row.resourceId }}</code></small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="280">
          <template #default="{ row }">
            <div class="phase-cell">
              <span>{{ row.message }}</span>
              <small v-if="row.errorCode">错误码 {{ row.errorCode }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="分类" width="120" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.errorCategory" :type="errorCategoryType(row.errorCategory)" size="small">
              {{ row.errorCategoryInfo?.label || row.errorCategory }}
            </el-tag>
            <span v-else class="muted-text">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }"><el-tag :type="errorStatusType(row.status)" size="small">{{ errorStatusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="重试次数" width="90" align="center">
          <template #default="{ row }">{{ row.retryCount }}</template>
        </el-table-column>
        <el-table-column label="下次重试" width="180">
          <template #default="{ row }">{{ row.nextRetryAt ? formatTime(row.nextRetryAt) : '—' }}</template>
        </el-table-column>
        <el-table-column label="发生时间" width="180">
          <template #default="{ row }">{{ formatTime(row.createdAt) }}</template>
        </el-table-column>
      </el-table>
      <div class="pagination-row">
        <span>共 {{ errorPage.total }} 条失败</span>
        <el-pagination
          v-model:current-page="errorQuery.page"
          :page-size="errorQuery.pageSize"
          layout="prev, pager, next"
          :total="errorPage.total"
          @current-change="loadErrors"
        />
      </div>
    </div>

    <div class="section-card mt16">
      <div class="section-header">
        <div>
          <h2>最近一次对账</h2>
          <p>比对远端飞书与本地组织数据，定位缺失、冗余与冲突。</p>
        </div>
      </div>
      <el-empty v-if="!reconciliation" description="暂无对账结果" />
      <template v-else>
        <div class="metric-grid">
          <div class="metric">
            <span>远端 / 本地部门</span>
            <strong>{{ reconciliation.remote_departments }} / {{ reconciliation.local_departments }}</strong>
            <small>缺失 {{ reconciliation.missing_local_departments.length }} · 冗余 {{ reconciliation.stale_local_departments.length }}</small>
          </div>
          <div class="metric">
            <span>远端 / 本地员工</span>
            <strong>{{ reconciliation.remote_employees }} / {{ reconciliation.local_employees }}</strong>
            <small>缺失 {{ reconciliation.missing_local_employees.length }} · 冗余 {{ reconciliation.stale_local_employees.length }}</small>
          </div>
          <div class="metric" :class="reconciliation.snapshot_complete ? 'success' : 'warning'">
            <span>快照完整</span>
            <strong>{{ reconciliation.snapshot_complete ? '是' : '否' }}</strong>
            <small>{{ reconciliation.failed_departments.length ? `失败部门 ${reconciliation.failed_departments.length} 个` : '全部部门成功' }}</small>
          </div>
          <div class="metric" :class="reconciliation.conflict_count ? 'danger' : 'success'">
            <span>身份冲突</span>
            <strong>{{ reconciliation.conflict_count }}</strong>
            <small>未绑定 {{ reconciliation.unbound_count }} 人</small>
          </div>
        </div>

        <el-collapse v-if="reconciliation.conflicts?.length" class="mt16">
          <el-collapse-item title="冲突明细" name="conflicts">
            <el-table :data="reconciliation.conflicts" size="small">
              <el-table-column prop="conflict" label="类型" min-width="160" />
              <el-table-column prop="identity_type" label="标识类型" width="140" />
              <el-table-column prop="identity_value" label="标识值" min-width="200" />
              <el-table-column prop="employee_id" label="员工 ID" min-width="160" />
            </el-table>
          </el-collapse-item>
        </el-collapse>

        <h3 class="section-title mt16">数据质量指标</h3>
        <div class="metric-grid">
          <div class="metric" :class="reconciliation.data_quality?.no_department_count ? 'warning' : ''">
            <span>未分配部门</span>
            <strong>{{ reconciliation.data_quality?.no_department_count ?? 0 }}</strong>
            <small>员工未挂到任何部门</small>
          </div>
          <div class="metric" :class="reconciliation.data_quality?.no_manager_count ? 'warning' : ''">
            <span>未分配上级</span>
            <strong>{{ reconciliation.data_quality?.no_manager_count ?? 0 }}</strong>
            <small>员工缺少汇报关系</small>
          </div>
          <div class="metric" :class="reconciliation.data_quality?.cyclic_manager_count ? 'danger' : ''">
            <span>汇报关系成环</span>
            <strong>{{ reconciliation.data_quality?.cyclic_manager_count ?? 0 }}</strong>
            <small>上下级关系存在循环</small>
          </div>
          <div class="metric" :class="reconciliation.data_quality?.no_login_account_count ? 'warning' : ''">
            <span>无登录账号</span>
            <strong>{{ reconciliation.data_quality?.no_login_account_count ?? 0 }}</strong>
            <small>缺少可登录账号</small>
          </div>
        </div>
      </template>
    </div>

    <el-drawer v-model="detailVisible" title="同步批次详情" size="min(720px, 100%)" destroy-on-close>
      <template v-if="detailBatch">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="批次号">{{ detailBatch.batchNo }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="batchStatusType(detailBatch.status)" size="small">{{ batchStatusLabel(detailBatch.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="模式">{{ detailBatch.mode === 'full' ? '全量' : '增量' }}</el-descriptions-item>
          <el-descriptions-item label="触发方式">{{ triggerLabel(detailBatch.triggerType) }}</el-descriptions-item>
          <el-descriptions-item label="阶段">{{ detailBatch.phase || '—' }}</el-descriptions-item>
          <el-descriptions-item label="阶段说明">{{ detailBatch.phaseDetail || '—' }}</el-descriptions-item>
          <el-descriptions-item label="部门成功 / 失败 / 跳过">
            {{ detailBatch.departmentsSynced }} / {{ detailBatch.departmentsFailed }} / {{ detailBatch.departmentsSkipped }}
          </el-descriptions-item>
          <el-descriptions-item label="员工成功 / 失败 / 跳过">
            {{ detailBatch.employeesSynced }} / {{ detailBatch.employeesFailed }} / {{ detailBatch.employeesSkipped }}
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ formatTime(detailBatch.startedAt) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ formatTime(detailBatch.finishedAt) }}</el-descriptions-item>
          <el-descriptions-item label="耗时">
            {{ detailBatch.durationMs != null ? `${(detailBatch.durationMs / 1000).toFixed(1)}s` : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="触发人">{{ detailBatch.triggeredBy || '系统' }}</el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="detailBatch.errorMessage"
          type="error"
          show-icon
          :closable="false"
          :title="detailBatch.errorMessage"
          :description="detailBatch.errorCategoryInfo?.label"
          class="mt16"
        />
      </template>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, Refresh, RefreshRight } from '@element-plus/icons-vue'
import {
  getLatestReconciliation,
  getOrganizationSyncStatus,
  getSyncSchedulerStatus,
  listSyncBatches,
  listSyncCursors,
  listSyncErrors,
  recoverStaleSyncBatches,
  retryFailedSyncItems,
  triggerOrganizationSync,
  type PageResult,
  type SchedulerStatus,
  type SyncBatch,
  type SyncBatchStatus,
  type SyncCursor,
  type SyncErrorCategory,
  type SyncErrorItem,
  type SyncReconciliation,
  type SyncStatus,
} from '@/api/feishuOrganization'

const loading = ref(false)
const refreshing = ref(false)
const triggering = ref(false)
const recovering = ref(false)
const retrying = ref(false)
const batchLoading = ref(false)
const errorLoading = ref(false)
const cursorLoading = ref(false)

const status = ref<SyncStatus | null>(null)
const scheduler = ref<SchedulerStatus | null>(null)
const reconciliation = ref<SyncReconciliation | null>(null)
const detailVisible = ref(false)
const detailBatch = ref<SyncBatch | null>(null)

const batchQuery = reactive({ page: 1, pageSize: 20, status: '' as string | '', mode: '' as string | '' })
const errorQuery = reactive({ page: 1, pageSize: 20, status: '' as string | '', errorCategory: '' as string | '' })
// ⚠️ **BUG-R6-46**：游标表改走**分页**端点 `listSyncCursors`（每部门一条，条数随部门数增长）。
const cursorQuery = reactive({ page: 1, pageSize: 20 })
const batchPage = ref<PageResult<SyncBatch>>({ records: [], total: 0, page: 1, pageSize: 20 })
const errorPage = ref<PageResult<SyncErrorItem>>({ records: [], total: 0, page: 1, pageSize: 20 })
const cursorPage = ref<PageResult<SyncCursor>>({ records: [], total: 0, page: 1, pageSize: 20 })

/** 快照不完整的游标数量 —— 用来决定要不要显示「已跳过离职判定」告警。 */
const incompleteCursorCount = computed(
  () => status.value?.cursors?.filter((cursor) => !cursor.snapshotComplete).length ?? 0,
)

const batchStatusOptions: Array<{ value: SyncBatchStatus; label: string }> = [
  { value: 'pending', label: '待执行' },
  { value: 'running', label: '执行中' },
  { value: 'ok', label: '成功' },
  { value: 'partial_failed', label: '部分失败' },
  { value: 'failed', label: '失败' },
  // ⚠️ **BUG-R6-47**：「已被后续成功批次取代」的历史异常痕迹。
  { value: 'superseded', label: '已被取代' },
]

const errorCategoryOptions: Array<{ value: SyncErrorCategory; label: string }> = [
  { value: 'retryable', label: '可重试' },
  { value: 'config', label: '配置问题' },
  { value: 'manual', label: '需人工处理' },
  { value: 'permanent', label: '永久失败' },
  { value: 'unknown', label: '未知' },
]

const errorSummary = computed(() => {
  const records = errorPage.value.records
  return {
    total: errorPage.value.total,
    deadLetter: records.filter((item) => item.status === 'dead_letter').length,
    pending: records.filter((item) => item.status === 'pending' || item.status === 'retrying').length,
  }
})

const reconciliationSummary = computed(() => {
  const data = reconciliation.value?.data_quality
  if (!data) return { title: '暂无数据', detail: '尚未产生对账结果', type: '' }
  const issues =
    (data.no_department_count ?? 0) +
    (data.no_manager_count ?? 0) +
    (data.cyclic_manager_count ?? 0) +
    (data.no_login_account_count ?? 0)
  if (issues === 0) return { title: '正常', detail: '未发现数据质量问题', type: 'success' }
  return { title: `${issues} 项待关注`, detail: '存在组织归属或汇报关系问题', type: 'warning' }
})

const reconciliationMetricType = computed(() => reconciliationSummary.value.type)

const runningTitle = computed(() => {
  const batchId = status.value?.runningBatchId
  return batchId ? `同步进行中（批次 ${batchId}）` : '同步进行中'
})

function batchStatusLabel(value: SyncBatchStatus) {
  return batchStatusOptions.find((item) => item.value === value)?.label || value
}

function batchStatusType(value: SyncBatchStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (value === 'ok') return 'success'
  if (value === 'partial_failed') return 'warning'
  if (value === 'failed') return 'danger'
  // ⚠️ **BUG-R6-47**：`superseded` 是「历史异常痕迹」，
  // 不该和真正的失败一样标红 —— 用中性灰，避免看板上一片告警。
  return 'info'
}

function statusLabel(value: SyncBatchStatus) {
  return batchStatusLabel(value)
}

function triggerLabel(value: string) {
  if (value === 'manual') return '手动'
  if (value === 'scheduled') return '定时'
  if (value === 'manual_retry') return '手动重试'
  return value
}

function errorStatusLabel(value: string) {
  const map: Record<string, string> = {
    pending: '待重试',
    retrying: '重试中',
    resolved: '已解决',
    failed: '已失败',
    dead_letter: '死信',
  }
  return map[value] || value
}

function errorStatusType(value: string): 'success' | 'warning' | 'danger' | 'info' {
  if (value === 'resolved') return 'success'
  if (value === 'pending' || value === 'retrying') return 'warning'
  if (value === 'dead_letter' || value === 'failed') return 'danger'
  return 'info'
}

function errorCategoryType(value: SyncErrorCategory): 'success' | 'warning' | 'danger' | 'info' {
  if (value === 'retryable') return 'warning'
  if (value === 'manual' || value === 'permanent') return 'danger'
  if (value === 'config') return 'info'
  return 'success'
}

function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function loadStatus() {
  status.value = await getOrganizationSyncStatus()
}

/**
 * 调度器状态单独拉一次。
 *
 * ⚠️ 为什么不能用 `status.scheduledEnabled` 代替：
 * 那个字段读的是**配置**（.env 里的开关），
 * 而这里读的是**进程内线程**是否真的活着。
 * 「配了但没跑」只有两者对比才能发现。
 */
async function loadScheduler() {
  try {
    scheduler.value = await getSyncSchedulerStatus()
  } catch {
    // 调度器端点不可用不应拖垮整个同步控制台。
    scheduler.value = null
  }
}

/** 定时同步的一句话描述。 */
const schedulerSummary = computed(() => {
  const s = scheduler.value
  if (!s) {
    return status.value?.scheduledEnabled
      ? `定时同步已开启（每 ${Math.round((status.value?.scheduleIntervalSeconds || 0) / 60)} 分钟）`
      : '定时同步未开启'
  }
  if (!s.enabled) return '定时同步未开启'
  if (!s.running) return '定时同步已配置，但调度线程未运行'

  // ⚠️ **每日模式不能显示成「每 N 分钟」** ——
  // 每日模式按日历触发（每天 8:00），与「间隔」语义完全不同；
  // 显示错会让运维以为触发时刻会随服务重启漂移。
  const parts = [
    s.mode === 'daily'
      ? `每天 ${s.dailyTime || '--:--'}`
      : `每 ${Math.round((s.intervalSeconds || 0) / 60)} 分钟`,
  ]
  if (s.nextRunAt) parts.push(`下次 ${formatTime(s.nextRunAt)}`)
  if (s.consecutiveFailures > 0) parts.push(`连续失败 ${s.consecutiveFailures} 次`)
  return `定时同步运行中（${parts.join(' · ')}）`
})

/**
 * 「配置了但线程没跑」——最容易被漏掉的坑。
 *
 * 典型触发：改了 .env 但没重启进程。
 */
const schedulerMisconfigured = computed(() => {
  const s = scheduler.value
  if (!s) return false
  return s.enabled && !s.running
})

async function loadReconciliation() {
  const result = await getLatestReconciliation()
  reconciliation.value = result?.reconciliation ?? null
}

async function loadBatches() {
  batchLoading.value = true
  try {
    const params: Record<string, unknown> = { page: batchQuery.page, pageSize: batchQuery.pageSize }
    if (batchQuery.status) params.status = batchQuery.status
    if (batchQuery.mode) params.mode = batchQuery.mode
    batchPage.value = await listSyncBatches(params)
  } finally {
    batchLoading.value = false
  }
}

async function loadErrors() {
  errorLoading.value = true
  try {
    const params: Record<string, unknown> = { page: errorQuery.page, pageSize: errorQuery.pageSize }
    if (errorQuery.status) params.status = errorQuery.status
    if (errorQuery.errorCategory) params.errorCategory = errorQuery.errorCategory
    errorPage.value = await listSyncErrors(params)
  } finally {
    errorLoading.value = false
  }
}

/**
 * 加载同步游标（**分页**）。
 *
 * ⚠️ **BUG-R6-46**：游标是「每部门一条」的粒度，条数随部门数增长 ——
 * 一次性全量拉会让响应体与表格渲染都线性膨胀。
 */
async function loadCursors() {
  cursorLoading.value = true
  try {
    cursorPage.value = await listSyncCursors({
      page: cursorQuery.page,
      pageSize: cursorQuery.pageSize,
    })
  } finally {
    cursorLoading.value = false
  }
}

async function refreshAll() {
  refreshing.value = true
  try {
    await Promise.all([
      loadStatus(),
      loadScheduler(),
      loadReconciliation(),
      loadBatches(),
      loadErrors(),
      loadCursors(),
    ])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '同步数据加载失败')
  } finally {
    refreshing.value = false
  }
}

async function triggerSync(mode: 'full' | 'incremental') {
  triggering.value = true
  try {
    const result = await triggerOrganizationSync(mode)
    ElMessage.success(result.created ? '同步已启动，正在后台执行' : '相同操作已在执行中，已复用该批次')
    await loadStatus()
    await loadBatches()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '触发同步失败')
  } finally {
    triggering.value = false
  }
}

async function retryFailed() {
  retrying.value = true
  try {
    const result = await retryFailedSyncItems()
    ElMessage.success(`重试完成：重试 ${result.retried} 条，解决 ${result.resolved} 条，死信 ${result.dead_lettered} 条`)
    await Promise.all([loadErrors(), loadStatus()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '重试失败')
  } finally {
    retrying.value = false
  }
}

async function recoverStale() {
  recovering.value = true
  try {
    const result = await recoverStaleSyncBatches()
    ElMessage.success(`已恢复 ${result.recovered} 个僵死批次`)
    await Promise.all([loadStatus(), loadBatches()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '恢复失败')
  } finally {
    recovering.value = false
  }
}

function openBatchDetail(row: SyncBatch) {
  detailBatch.value = row
  detailVisible.value = true
}

let timer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  loading.value = true
  try {
    await refreshAll()
  } finally {
    loading.value = false
  }
  // 同步在后台执行，定时轮询刷新运行态与批次进度。
  timer = setInterval(() => {
    if (status.value?.running) void refreshAll()
  }, 5000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.feishu-sync-page .mt16 {
  margin-top: 16px;
}

.feishu-sync-page .metric strong {
  display: block;
  margin: 4px 0 2px;
  font-size: 20px;
}

.feishu-sync-page .metric small {
  color: var(--muted);
  font-size: 11px;
}

.cursor-value {
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace;
  font-size: 11px;
}

.phase-cell {
  display: grid;
  gap: 2px;
}

.phase-cell small {
  color: var(--muted);
  font-size: 10px;
}

.ok-text {
  color: var(--green);
  font-weight: 700;
}

.danger-text {
  color: #c44f4f;
  font-weight: 700;
}
</style>
