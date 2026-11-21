<template>
  <div class="pan-workload-panel">
    <!-- 头部：口径说明 + 操作 -->
    <div class="panel-header">
      <div class="panel-title-row">
        <h3 class="panel-title">剪辑工作量</h3>
        <el-tag v-if="period" size="small" type="info" effect="plain">{{ period }}</el-tag>
      </div>
      <div class="panel-actions">
        <el-button size="small" @click="loadAll" :loading="loading">刷新</el-button>
        <slot name="actions" />
      </div>
    </div>

    <!-- 口径说明（★ 关键：不说明就说不清「系统判的是多少、谁改的」）-->
    <el-alert type="info" :closable="false" class="caliber-note">
      <template #title>
        <span class="caliber-title">口径说明</span>
      </template>
      <ul class="caliber-list">
        <li>「系统判定」= 机器按规则数出来的条数，<b>永不被覆盖</b>。</li>
        <li>「裁定后」= 主管复核后确认的条数；为空表示没有人工调整。</li>
        <li>条数与完播率/留存率<b>量纲不同</b>，分别进算分集，不合成综合分。</li>
      </ul>
    </el-alert>

    <!-- 加载 / 错误 -->
    <div v-if="loading" class="panel-state">
      <el-skeleton :rows="4" animated />
    </div>
    <div v-else-if="error" class="panel-state">
      <el-alert :title="error" type="warning" :closable="false" show-icon />
      <el-button link type="primary" size="small" @click="loadAll">重新加载</el-button>
    </div>

    <template v-else>
      <!-- ① 按人汇总 -->
      <section class="section">
        <h4 class="section-title">
          按人汇总
          <span class="section-hint">{{ summary?.employees.length ?? 0 }} 人</span>
        </h4>
        <el-table :data="summary?.employees ?? []" size="small" border stripe>
          <el-table-column prop="employeeName" label="员工" min-width="120" />
          <el-table-column label="合计条数" width="100" align="right">
            <template #default="{ row }">
              <b>{{ row.totalCount }}</b>
            </template>
          </el-table-column>
          <el-table-column prop="days" label="统计天数" width="90" align="right" />
          <el-table-column label="人工裁定" width="90" align="right">
            <template #default="{ row }">
              <el-tag v-if="row.adjustedDays > 0" size="small" type="warning" effect="plain">
                {{ row.adjustedDays }} 天
              </el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="有异议" width="90" align="right">
            <template #default="{ row }">
              <el-tag v-if="row.disputedDays > 0" size="small" type="danger" effect="plain">
                {{ row.disputedDays }} 天
              </el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="异常" width="80" align="right">
            <template #default="{ row }">
              <span :class="{ danger: row.anomalyCount > 0 }">{{ row.anomalyCount }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="viewFiles(row.employeeId)">
                文件
              </el-button>
            </template>
          </el-table-column>
          <template #empty>
            <span class="muted">暂无数据</span>
          </template>
        </el-table>
      </section>

      <!-- ② 待裁定工单 -->
      <section class="section">
        <h4 class="section-title">
          待裁定工单
          <span class="section-hint">{{ summary?.pendingReviews.length ?? 0 }} 条</span>
        </h4>
        <el-table :data="summary?.pendingReviews ?? []" size="small" border stripe>
          <el-table-column prop="businessDate" label="业务日期" width="120" />
          <el-table-column prop="employeeId" label="员工" width="140" show-overflow-tooltip />
          <el-table-column label="粒度" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">
                {{ row.scope === 'file' ? '逐文件' : '按天' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="系统判定" width="110" align="right">
            <template #default="{ row }">
              <span
                v-if="row.dailySnapshot?.has_daily === false"
                class="muted"
                title="该日尚未采集，系统没有可比对的判定值"
              >未采集</span>
              <template v-else>{{ row.systemCount }}</template>
            </template>
          </el-table-column>
          <el-table-column label="员工主张" width="100" align="right">
            <template #default="{ row }">{{ row.claimedCount }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="申诉理由" min-width="160" show-overflow-tooltip />
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openReview(row.id)">去裁定</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <span class="muted">没有待裁定的工单</span>
          </template>
        </el-table>
      </section>

      <!-- ③ 人工指标 -->
      <section class="section">
        <h4 class="section-title">
          人工填报指标
          <span class="section-hint">{{ summary?.manualMetrics.length ?? 0 }} 条</span>
        </h4>
        <el-table :data="summary?.manualMetrics ?? []" size="small" border stripe>
          <el-table-column prop="metricDate" label="日期" width="120" />
          <el-table-column prop="employeeId" label="员工" width="140" show-overflow-tooltip />
          <el-table-column label="指标" width="110">
            <template #default="{ row }">
              {{ MANUAL_METRIC_LABELS[row.metricCode] ?? row.metricCode }}
            </template>
          </el-table-column>
          <el-table-column label="数值" width="90" align="right">
            <template #default="{ row }">{{ formatRate(row.metricValue) }}</template>
          </el-table-column>
          <el-table-column label="来源" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.source === 'feishu' ? 'success' : 'info'">
                {{ SOURCE_LABELS[row.source] ?? row.source }}
              </el-tag>
            </template>
          </el-table-column>
          <template #empty>
            <span class="muted">暂无人工填报</span>
          </template>
        </el-table>
      </section>
    </template>

    <!-- 文件明细抽屉（审计根）-->
    <el-drawer v-model="filesDrawer" :title="filesTitle" size="70%">
      <el-table :data="files" size="small" border stripe v-loading="filesLoading">
        <el-table-column prop="fileName" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column prop="panPath" label="网盘路径" min-width="240" show-overflow-tooltip />
        <el-table-column label="计入" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.counted ? 'success' : 'info'" effect="plain">
              {{ row.counted ? `计 ${row.countWeight}` : '排除' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标记" width="140">
          <template #default="{ row }">
            <el-tag v-for="m in row.markers" :key="m" size="small" effect="plain" class="marker">
              {{ m }}
            </el-tag>
            <span v-if="!row.markers?.length" class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="excludeReason" label="排除原因" width="150" show-overflow-tooltip />
        <el-table-column label="重复" width="80">
          <template #default="{ row }">
            <span v-if="row.isDuplicate" class="danger">{{ row.duplicateKind }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <!-- 工单详情抽屉 -->
    <el-drawer v-model="reviewDrawer" :title="reviewTitle" size="70%">
      <div v-loading="reviewLoading">
        <div v-if="reviewDetail" class="review-detail">
          <!-- 摘要 -->
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="业务日期">{{ reviewDetail.review.businessDate }}</el-descriptions-item>
            <el-descriptions-item label="粒度">
              {{ reviewDetail.review.scope === 'file' ? '逐文件裁定' : '按天裁定' }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag size="small" :type="statusType(reviewDetail.review.status)">
                {{ STATUS_LABELS[reviewDetail.review.status] ?? reviewDetail.review.status }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="系统判定">{{ reviewDetail.review.systemCount }}</el-descriptions-item>
            <el-descriptions-item label="员工主张">{{ reviewDetail.review.claimedCount }}</el-descriptions-item>
            <el-descriptions-item label="裁定后">
              {{ reviewDetail.review.finalCount ?? '—' }}
            </el-descriptions-item>
          </el-descriptions>

          <!-- 链校验 -->
          <el-alert
            :type="reviewDetail.chain.ok ? 'success' : 'error'"
            :closable="false"
            class="chain-alert"
          >
            <template #title>
              事件链{{ reviewDetail.chain.ok ? '完整' : '异常' }}（{{ reviewDetail.chain.eventCount }} 条事件）
            </template>
            <ul v-if="!reviewDetail.chain.ok" class="chain-issues">
              <li v-for="(issue, i) in reviewDetail.chain.issues" :key="i">{{ issue }}</li>
            </ul>
            <div class="chain-guarantee">{{ reviewDetail.chain.guarantee }}</div>
          </el-alert>

          <!-- 争议项（逐项裁定）-->
          <h4 class="section-title">争议项</h4>
          <el-table :data="reviewDetail.items" size="small" border>
            <el-table-column label="争议对象" min-width="180">
              <template #default="{ row }">
                <div v-if="row.panWorkloadFileId" class="target">
                  <el-icon class="linked"><Link /></el-icon> 系统文件
                </div>
                <div v-else class="target">
                  <el-icon class="manual"><EditPen /></el-icon> {{ row.manualName || '手填' }}
                </div>
                <div v-if="row.manualDescription" class="muted desc">{{ row.manualDescription }}</div>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ DISPUTE_LABELS[row.disputeType] ?? row.disputeType }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="主张" width="70" align="right">
              <template #default="{ row }">{{ row.claimedWeight }}</template>
            </el-table-column>
            <el-table-column label="裁定" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="itemResolutionType(row.resolution)">
                  {{ ITEM_RESOLUTION_LABELS[row.resolution] ?? row.resolution }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最终" width="70" align="right">
              <template #default="{ row }">{{ row.finalWeight ?? '—' }}</template>
            </el-table-column>
            <el-table-column v-if="canResolve" label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button link type="success" size="small" @click="resolveItem(row, 'upheld')">成立</el-button>
                <el-button link type="info" size="small" @click="resolveItem(row, 'rejected')">不成立</el-button>
                <el-button link type="warning" size="small" @click="resolveItem(row, 'need_more')">补证</el-button>
              </template>
            </el-table-column>
          </el-table>

          <!-- ★ 整体裁定（主管）—— 写回日汇总 -->
          <div v-if="canResolve && reviewDetail.review.status === 'pending'" class="resolve-bar">
            <el-divider content-position="left">
              <span class="divider-text">整体裁定</span>
            </el-divider>
            <el-alert type="warning" :closable="false" class="resolve-note">
              裁定后结果会<b>写回该日汇总</b>，并生成不可篡改的事件记录。
              「不成立」则保留系统原判定值。
            </el-alert>
            <div class="resolve-form">
              <el-form-item label="最终条数">
                <el-input-number v-model="resolveForm.final_count" :min="0" :max="10000" style="width: 140px" />
              </el-form-item>
              <el-form-item label="裁定说明">
                <el-input
                  v-model="resolveForm.resolution_note"
                  placeholder="说明裁定依据"
                  style="width: 320px"
                  maxlength="200"
                  show-word-limit
                />
              </el-form-item>
              <el-form-item>
                <el-button type="success" @click="resolveReview('approved')" :loading="submitting">裁定成立</el-button>
                <el-button type="info" @click="resolveReview('rejected')" :loading="submitting">裁定不成立</el-button>
              </el-form-item>
            </div>
          </div>

          <!-- 证据 -->
          <h4 class="section-title">证据附件（{{ reviewDetail.artifacts.length }}）</h4>
          <div v-if="reviewDetail.artifacts.length" class="artifact-list">
            <div v-for="a in reviewDetail.artifacts" :key="a.id" class="artifact-item">
              <el-icon><Picture /></el-icon>
              <span class="artifact-name">{{ a.name }}</span>
              <span class="muted">{{ formatSize(a.sizeBytes) }}</span>
              <el-tag size="small" effect="plain" class="digest" :title="a.digest">
                {{ a.digest.slice(0, 8) }}
              </el-tag>
              <el-button link type="primary" size="small" @click="downloadArtifact(a)">下载</el-button>
            </div>
          </div>
          <div v-else class="muted">没有证据附件</div>

          <!-- 事件流 -->
          <h4 class="section-title">事件流（hash 链）</h4>
          <el-timeline class="event-timeline">
            <el-timeline-item
              v-for="e in reviewDetail.events"
              :key="e.seq"
              :timestamp="e.createdAt"
              placement="top"
              size="small"
            >
              <div class="event-line">
                <b>#{{ e.seq }} {{ EVENT_LABELS[e.eventType] ?? e.eventType }}</b>
                <span class="muted"> by {{ e.actorId }}</span>
              </div>
              <div class="event-hash" :title="e.eventHash">
                ← {{ e.prevHash.slice(0, 8) || '（链首）' }}
                <span class="muted"> hash {{ e.eventHash.slice(0, 8) }}</span>
              </div>
            </el-timeline-item>
          </el-timeline>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { EditPen, Link, Picture } from '@element-plus/icons-vue'
import {
  MANUAL_METRIC_LABELS,
  panWorkloadActions,
  panWorkloadApi,
  type ItemResolution,
  type PanWorkloadArtifact,
  type PanWorkloadFile,
  type PanWorkloadReviewDetail,
  type PanWorkloadSummary,
  type ReviewStatus,
} from '@/api/panWorkload'

/** 工单状态中文名。 */
const STATUS_LABELS: Record<string, string> = {
  pending: '待裁定',
  approved: '已裁定（成立）',
  rejected: '已裁定（不成立）',
  withdrawn: '已撤回',
}

/** 争议类型中文名。 */
const DISPUTE_LABELS: Record<string, string> = {
  missing: '系统漏了',
  overcounted: '系统多算了',
  weight: '权重不对',
  other: '其他',
}

/** 争议项裁定结果中文名。 */
const ITEM_RESOLUTION_LABELS: Record<string, string> = {
  pending: '待裁定',
  upheld: '成立',
  rejected: '不成立',
  need_more: '需补证',
}

/** 事件类型中文名。 */
const EVENT_LABELS: Record<string, string> = {
  created: '发起工单',
  item_added: '添加争议项',
  artifact_added: '上传证据',
  item_resolved: '裁定争议项',
  approved: '裁定成立',
  rejected: '裁定不成立',
  withdrawn: '撤回工单',
}

/** 来源中文名。 */
const SOURCE_LABELS: Record<string, string> = {
  frontend: '网页端',
  feishu: '飞书端',
  import: '导入',
}

const props = withDefaults(
  defineProps<{
    /** 周期（YYYY-MM）。 */
    period?: string
    /**
     * 是否能裁定 —— 由父级按权限传。
     *
     * ⚠️ 前端按钮隐藏**不是安全措施** —— 后端也会校验（`_require_leader`）。
     * 这里只是**减少误操作**。
     */
    canResolve?: boolean
  }>(),
  { period: '', canResolve: false },
)

const loading = ref(false)
const error = ref('')
const summary = ref<PanWorkloadSummary | null>(null)

const filesDrawer = ref(false)
const filesLoading = ref(false)
const files = ref<PanWorkloadFile[]>([])
const filesTitle = ref('文件明细')

const reviewDrawer = ref(false)
const reviewLoading = ref(false)
const reviewDetail = ref<PanWorkloadReviewDetail | null>(null)
const reviewTitle = ref('复核工单')
const submitting = ref(false)
const resolveForm = reactive({ final_count: 0, resolution_note: '' })

/** 加载总览。 */
async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    summary.value = await panWorkloadApi.getSummary()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/** 打开某人的文件明细。 */
async function viewFiles(employeeId: string) {
  filesTitle.value = '文件明细'
  filesDrawer.value = true
  filesLoading.value = true
  try {
    // 默认展示**最近一天** —— 明细可能很大
    const daily = await panWorkloadApi.getDaily({ employee_id: employeeId })
    const latest = daily.items[0]
    if (!latest) {
      files.value = []
      return
    }
    // ⚠️ **标题要带上日期** —— 否则用户不知道看的是哪一天，
    //    看到「暂无数据」会以为是系统出问题。
    filesTitle.value = `文件明细 — ${employeeId} — ${latest.businessDate}`
    const res = await panWorkloadApi.getFiles({
      employee_id: employeeId,
      business_date: latest.businessDate,
    })
    files.value = res.items
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '加载文件明细失败')
  } finally {
    filesLoading.value = false
  }
}

/** 打开工单详情。 */
async function openReview(reviewId: string) {
  reviewTitle.value = `复核工单 ${reviewId.slice(0, 8)}`
  reviewDrawer.value = true
  reviewLoading.value = true
  try {
    reviewDetail.value = await panWorkloadApi.getReview(reviewId)
    // 预填最终条数 —— 默认取员工主张（最常见的是「系统少算了」）
    resolveForm.final_count = reviewDetail.value.review.claimedCount
    resolveForm.resolution_note = ''
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '加载工单失败')
  } finally {
    reviewLoading.value = false
  }
}

/** 裁定单个争议项。 */
async function resolveItem(row: { id: string }, resolution: ItemResolution) {
  const reviewId = reviewDetail.value?.review.id
  if (!reviewId) return
  try {
    await panWorkloadActions.resolveItem(reviewId, row.id, { resolution })
    ElMessage.success('已裁定')
    await openReview(reviewId)
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '裁定失败')
  }
}

/**
 * 整体裁定工单 —— **写回日汇总**。
 *
 * ⚠️ 「不成立」时后端**保留系统原判定值**（adjusted_count 置空），
 * 不把 adjusted_count 写成 system_count ——
 * 否则「人工裁定过」和「没裁定过」看起来一样，审计就失去意义。
 */
async function resolveReview(resolution: 'approved' | 'rejected') {
  const reviewId = reviewDetail.value?.review.id
  if (!reviewId) return
  submitting.value = true
  try {
    await panWorkloadActions.resolveReview(reviewId, {
      resolution,
      final_count: resolution === 'approved' ? resolveForm.final_count : null,
      resolution_note: resolveForm.resolution_note,
    })
    ElMessage.success(resolution === 'approved' ? '已裁定成立，结果已写回日汇总' : '已裁定不成立')
    await openReview(reviewId)
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '裁定失败')
  } finally {
    submitting.value = false
  }
}

/** 下载证据附件。 */
async function downloadArtifact(a: PanWorkloadArtifact) {
  const reviewId = reviewDetail.value?.review.id
  if (!reviewId) return
  try {
    await panWorkloadApi.downloadArtifact(reviewId, a.id, a.name)
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '下载失败')
  }
}

/** 比率格式化 —— 0.35 ⇒ 35.0%。 */
function formatRate(v: number) {
  return `${(v * 100).toFixed(1)}%`
}

/** 文件大小格式化。 */
function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

/** 状态标签色。 */
function statusType(s: ReviewStatus) {
  if (s === 'approved') return 'success'
  if (s === 'rejected') return 'info'
  if (s === 'withdrawn') return 'info'
  return 'warning'
}

/** 争议项裁定结果标签色。 */
function itemResolutionType(r: ItemResolution) {
  if (r === 'upheld') return 'success'
  if (r === 'rejected') return 'info'
  if (r === 'need_more') return 'warning'
  return 'primary'
}

onMounted(loadAll)

defineExpose({ reload: loadAll })
</script>

<style scoped>
.pan-workload-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-title {
  margin: 0;
  font-size: 16px;
}

.panel-actions {
  display: flex;
  gap: 8px;
}

/* ⚠️ 口径说明 —— 不说明就说不清「系统判的是多少、谁改的」 */
.caliber-note {
  :deep(.el-alert__content) {
    width: 100%;
  }
}

.caliber-title {
  font-weight: 600;
}

.caliber-list {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-title {
  margin: 0;
  font-size: 14px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.section-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.panel-state {
  padding: 12px 0;
}

.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.danger {
  color: var(--el-color-danger);
}

.marker {
  margin-right: 4px;
}

.target {
  display: flex;
  align-items: center;
  gap: 4px;
}

.linked {
  color: var(--el-color-success);
}

.manual {
  color: var(--el-color-warning);
}

.desc {
  margin-top: 2px;
}

.chain-alert {
  margin: 12px 0;
}

.chain-issues {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
}

.chain-guarantee {
  margin-top: 6px;
  font-size: 11px;
  opacity: 0.75;
  line-height: 1.6;
}

.resolve-bar {
  margin-top: 16px;
}

.resolve-note {
  margin-bottom: 12px;
}

.resolve-form {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
}

.resolve-form :deep(.el-form-item) {
  margin-bottom: 8px;
}

.divider-text {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.artifact-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.artifact-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  font-size: 13px;
}

.artifact-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.digest {
  font-family: ui-monospace, monospace;
}

.event-timeline {
  margin-top: 8px;
  padding-left: 4px;
}

.event-line {
  font-size: 13px;
}

.event-hash {
  margin-top: 2px;
  font-size: 11px;
  font-family: ui-monospace, monospace;
  opacity: 0.7;
}
</style>
