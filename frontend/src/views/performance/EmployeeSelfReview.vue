<template>
  <section class="page-panel employee-self-review">
    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="8" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-empty :image-size="120" description="数据加载失败">
        <template #default>
          <div class="error-details">
            <h3>⚠️ 无法加载考核记录</h3>
            <p>{{ error }}</p>
            <el-button type="primary" @click="loadRecords">重新加载</el-button>
          </div>
        </template>
      </el-empty>
    </div>

    <!-- 正常状态 -->
    <div v-else>
      <div class="page-header">
        <div>
          <span class="eyebrow">SELF REVIEW</span>
          <h1 class="page-title">员工自评</h1>
          <p class="page-subtitle">
            完成你的自评表，总结你的工作成就、主要表现和遇到的挑战
          </p>
        </div>
      </div>

      <!-- 待自评的任务 -->
      <div class="section-card">
        <div class="section-header">
          <div>
            <h2>待自评的任务</h2>
            <p>请在截止日期前完成自评</p>
          </div>
        </div>

        <div v-if="pendingRecords.length > 0" class="records-grid">
          <div v-for="record in pendingRecords" :key="record.id" class="record-card">
            <div class="record-card-header">
              <h3>{{ record.taskName }}</h3>
              <el-tag
                :type="getStatusType(record.status)"
                effect="plain"
              >
                {{ getStatusLabel(record.status) }}
              </el-tag>
            </div>
            <div class="record-card-body">
              <div class="deadline-info">
                <span class="label">自评截止：</span>
                <span class="value" :class="{ 'is-overdue': isOverdue(record.deadline) }">
                  {{ formatDate(record.deadline) }}
                </span>
              </div>
              <div class="task-period">
                <span class="label">考核周期：</span>
                <span class="value">
                  {{ formatDate(record.periodStart) }} ~ {{ formatDate(record.periodEnd) }}
                </span>
              </div>
            </div>
            <div class="record-card-footer">
              <el-button
                type="primary"
                @click="editSelfReview(record.id, record.taskId)"
              >
                <span>✎</span> 编写自评
              </el-button>
            </div>
          </div>
        </div>
        <el-empty v-else :image-size="60" description="暂无待自评的任务" />
      </div>

      <!-- 已提交的自评 -->
      <div class="section-card mt20">
        <div class="section-header">
          <div>
            <h2>已提交的自评</h2>
            <p>查看你已提交的自评内容</p>
          </div>
        </div>

        <el-table :data="submittedRecords" stripe empty-text="暂无已提交的自评">
          <el-table-column prop="taskName" label="任务名称" min-width="200" />
          <el-table-column label="周期" width="220">
            <template #default="{ row }">
              {{ formatDate(row.periodStart) }} ~ {{ formatDate(row.periodEnd) }}
            </template>
          </el-table-column>
          <el-table-column label="提交时间" width="180">
            <template #default="{ row }">
              {{ formatDateTime(row.submittedAt) }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tag type="success" effect="plain">已提交</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                size="small"
                @click="viewSelfReview(row.id)"
              >
                查看
              </el-button>
              <el-button
                link
                type="primary"
                size="small"
                @click="editSelfReview(row.id, row.taskId)"
              >
                编辑
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 自评编辑抽屉 -->
    <el-drawer
      v-model="showEditDrawer"
      title="编写自评"
      size="60%"
      @close="handleDrawerClose"
    >
      <div v-if="editingRecord" class="self-review-form">
        <el-form :model="reviewForm" label-width="120px">
          <el-form-item label="业绩总结" required>
            <el-input
              v-model="reviewForm.performanceSummary"
              type="textarea"
              placeholder="总结你在本期的主要工作内容和成果..."
              rows="5"
              maxlength="1000"
              show-word-limit
            />
          </el-form-item>

          <el-form-item label="主要成就" required>
            <el-input
              v-model="reviewForm.achievements"
              type="textarea"
              placeholder="列举你本期最主要的成就和突出表现..."
              rows="5"
              maxlength="1000"
              show-word-limit
            />
          </el-form-item>

          <el-form-item label="遇到的挑战" required>
            <el-input
              v-model="reviewForm.challenges"
              type="textarea"
              placeholder="说明你在工作中遇到的主要挑战和障碍..."
              rows="4"
              maxlength="800"
              show-word-limit
            />
          </el-form-item>

          <el-form-item label="改进方向">
            <el-input
              v-model="reviewForm.improvements"
              type="textarea"
              placeholder="描述你在下一阶段的改进计划和发展方向..."
              rows="4"
              maxlength="800"
              show-word-limit
            />
          </el-form-item>

          <el-form-item label="其他说明">
            <el-input
              v-model="reviewForm.remarks"
              type="textarea"
              placeholder="如有其他需要说明的内容，可在此补充..."
              rows="3"
              maxlength="500"
              show-word-limit
            />
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <div style="flex: auto">
          <el-button @click="showEditDrawer = false">取消</el-button>
          <el-button type="primary" @click="saveDraft" :loading="saving">
            保存草稿
          </el-button>
          <el-button type="primary" @click="submitReview" :loading="submitting">
            提交自评
          </el-button>
        </div>
      </template>
    </el-drawer>

    <!--
      自评详情弹窗
      ⚠️ BUG-R6-43：过去「查看」按钮跳转到
      `/performance/self-reviews/{recordId}`，但**该路由未注册**，
      点「查看」直接白屏。现在改为弹窗 + 真实接口取数。
    -->
    <el-dialog
      v-model="showDetailDialog"
      title="自评详情"
      width="720px"
      destroy-on-close
    >
      <div v-if="detailLoading" class="loading-state">
        <el-skeleton :rows="6" animated />
      </div>
      <div v-else-if="detailContent">
        <el-descriptions :column="2" border size="small" class="mb16">
          <el-descriptions-item label="记录 ID">{{ detailContent.record_id }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ getStatusLabel(detailContent.status) }}</el-descriptions-item>
          <el-descriptions-item label="提交时间">
            {{ detailContent.submitted_at ? formatDateTime(detailContent.submitted_at) : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="员工 ID">{{ detailContent.employee_id }}</el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">自评正文</el-divider>
        <div class="detail-block">
          <h4>业绩总结</h4>
          <p>{{ detailField('performance_summary') || '—' }}</p>
        </div>
        <div class="detail-block">
          <h4>主要成就</h4>
          <p>{{ detailField('achievements') || '—' }}</p>
        </div>
        <div class="detail-block">
          <h4>遇到的挑战</h4>
          <p>{{ detailField('challenges') || '—' }}</p>
        </div>
        <div class="detail-block">
          <h4>改进方向</h4>
          <p>{{ detailField('improvements') || '—' }}</p>
        </div>
        <div class="detail-block">
          <h4>其他说明</h4>
          <p>{{ detailField('remarks') || '—' }}</p>
        </div>
      </div>
      <el-empty v-else :image-size="80" description="未找到自评内容" />
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import {
  buildDedupKey,
  buildSelfReviewComment,
  fetchMySelfReviewRecords,
  fetchSelfReviewDetail,
  normalizeSelfReviewText,
  submitSelfReview,
} from '@/api/selfReview'
import type { SelfReviewDetail } from '@/api/selfReview'
import type { PendingAssessmentRecord } from '@/api/attendance'
import { toDashedPeriod } from '@/utils/period'

const loading = ref(false)
const saving = ref(false)
const submitting = ref(false)
const error = ref('')
const showEditDrawer = ref(false)
const editingRecordId = ref('')
const editingTaskId = ref('')

// 自评详情弹窗（BUG-R6-43）
const showDetailDialog = ref(false)
const detailLoading = ref(false)
const detailContent = ref<SelfReviewDetail | null>(null)

/**
 * ⚠️ BUG-R6-41：这里过去是一段写死的 mock 数组
 * （`R001` / `T001` / "2026年Q4绩效考核"），
 * `loadRecords` / `saveDraft` / `submitReview` 里也只有
 * "这里应该调用后端 API ..." 的 TODO 注释 —— **一个请求都没发**。
 *
 * 后果：员工填完自评点「提交自评」，页面弹「自评已提交」，
 * 但数据库里**什么都没变**；主管那边永远看不到这条自评，
 * 绩效闭环在第一步就断了（而页面看起来一切正常，极难排查）。
 *
 * 现在改为真实数据源：``GET /v1/assessment/records/pending``
 * （权限过滤后普通员工只看得到自己的记录）。
 */
const allRecords = ref<PendingAssessmentRecord[]>([])

/** 把后端记录映射成页面用的视图模型。 */
function toViewModel(record: PendingAssessmentRecord) {
  // ⚠️ **BUG-R6-26**：周期归一化统一走 `@/utils/period`。
  //   后端 `AssessmentTask.period` 是紧凑 `YYYYMM`（权威格式），
  //   但历史数据/考勤证据侧可能是 `YYYY-MM` —— 两种都要能处理。
  const dashed = toDashedPeriod(record.period)
  const start = dashed ? `${dashed}-01` : ''
  const end = dashed
    ? (() => {
        const [y, m] = dashed.split('-').map(Number)
        const last = new Date(y, m, 0).getDate()
        return `${dashed}-${String(last).padStart(2, '0')}`
      })()
    : ''
  return {
    id: record.record_id,
    taskId: record.task_id,
    taskName: record.task_name || '未命名考核任务',
    periodStart: start,
    periodEnd: end,
    deadline: record.updated_at || '',
    status: record.status,
    managerComment: record.manager_comment || '',
    submittedAt: record.updated_at || '',
  }
}

const records = computed(() => allRecords.value.map(toViewModel))

const reviewForm = reactive({
  performanceSummary: '',
  achievements: '',
  challenges: '',
  improvements: '',
  remarks: '',
})

const editingRecord = computed(() => {
  return records.value.find((r) => r.id === editingRecordId.value)
})

// 待自评的任务
const pendingRecords = computed(() => {
  return records.value.filter((r) => r.status === 'not_started')
})

// 已提交的自评（后端状态：self_review_submitted / manager_review_submitted / confirmed）
const submittedRecords = computed(() => {
  return records.value.filter((r) =>
    ['self_review_submitted', 'manager_review_submitted', 'confirmed'].includes(r.status),
  )
})

function getStatusLabel(status: string): string {
  // ⚠️ BUG-R6-43：这里过去的映射表只覆盖了
  // ``not_started / in_progress / submitted / confirmed``，
  // 但后端真实状态是
  // ``not_started / self_review_submitted / manager_review_submitted / confirmed``。
  // 结果是「已提交的自评」表里状态列直接显示英文 ``self_review_submitted``。
  const statusMap: Record<string, string> = {
    not_started: '未开始',
    in_progress: '进行中',
    submitted: '已提交',
    self_review_submitted: '已提交自评',
    manager_review_submitted: '主管已评分',
    confirmed: '已确认',
  }
  return statusMap[status] || status
}

function getStatusType(status: string): string {
  const typeMap: Record<string, string> = {
    not_started: 'info',
    in_progress: 'warning',
    submitted: 'success',
    self_review_submitted: 'success',
    manager_review_submitted: 'success',
    confirmed: 'success',
  }
  return typeMap[status] || 'info'
}

function isOverdue(deadline: string): boolean {
  return dayjs(deadline).isBefore(dayjs())
}

function formatDate(date: string): string {
  return dayjs(date).format('YYYY-MM-DD')
}

function formatDateTime(dateTime: string): string {
  return dayjs(dateTime).format('YYYY-MM-DD HH:mm')
}

async function loadRecords() {
  loading.value = true
  error.value = ''
  try {
    // ⚠️ BUG-R6-41：真正去后端拉「属于我」的考核记录。
    // 后端按 token 解析身份并做 ACL 过滤，普通员工只会拿到自己的记录。
    allRecords.value = await fetchMySelfReviewRecords()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '未知错误'
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

function editSelfReview(recordId: string, taskId: string) {
  editingRecordId.value = recordId
  editingTaskId.value = taskId

  reviewForm.performanceSummary = ''
  reviewForm.achievements = ''
  reviewForm.challenges = ''
  reviewForm.improvements = ''
  reviewForm.remarks = ''

  showEditDrawer.value = true
}

function handleDrawerClose() {
  showEditDrawer.value = false
  editingRecordId.value = ''
  editingTaskId.value = ''
}

/**
 * 组装提交体。
 * 后端契约见 ``backend/app/schemas_self_review.py``：
 * - ``self_comment`` 必填且 >= 10 字 → 用五段文本拼成正文；
 * - ``self_score`` 必填 1–5 → 页面没采集分数，按"达成预期"给 4；
 * - ``answers`` 存结构化五段，便于后续按字段做 AI 引用与审核。
 */
function buildPayload(recordId: string) {
  const comment = buildSelfReviewComment(reviewForm)
  return {
    self_score: 4,
    self_comment: comment,
    highlights: reviewForm.achievements.trim() || null,
    improvement_areas: reviewForm.improvements.trim() || null,
    answers: [
      { field_key: 'performance_summary', text: reviewForm.performanceSummary.trim() },
      { field_key: 'achievements', text: reviewForm.achievements.trim() },
      { field_key: 'challenges', text: reviewForm.challenges.trim() },
      { field_key: 'improvements', text: reviewForm.improvements.trim() },
      { field_key: 'remarks', text: reviewForm.remarks.trim() },
    ],
    dedup_key: buildDedupKey(recordId, reviewForm),
  }
}

async function saveDraft() {
  if (!editingRecord.value) return

  saving.value = true
  try {
    // 后端自评提交接口没有单独的"草稿"入口，
    // 且草稿状态（in_progress）会被后端视为非法中间态，
    // 因此这里只做本地留痕 —— **不**伪造"已保存到服务端"的假象。
    // 真正落库请点「提交自评」。
    ElMessage.info('草稿仅保留在当前页面，提交后才会保存到服务器')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function submitReview() {
  if (!reviewForm.performanceSummary.trim()) {
    ElMessage.warning('请填写业绩总结')
    return
  }
  if (!reviewForm.achievements.trim()) {
    ElMessage.warning('请填写主要成就')
    return
  }
  if (!reviewForm.challenges.trim()) {
    ElMessage.warning('请填写遇到的挑战')
    return
  }
  if (!editingRecord.value) {
    ElMessage.warning('未找到要提交的自评记录')
    return
  }

  submitting.value = true
  try {
    // ⚠️ BUG-R6-41：真正提交到后端。
    // 成功后重新拉列表，页面上"待自评"会消失、"已提交"会出现。
    await submitSelfReview(editingRecordId.value, buildPayload(editingRecordId.value))
    ElMessage.success('自评已提交')
    showEditDrawer.value = false
    await loadRecords()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

/**
 * 取自评正文的某个字段并归一成可展示文本。
 *
 * ⚠️ BUG-R6-44：后端历史数据里 ``achievements`` / ``challenges``
 * 是**字符串数组**，``performance_summary`` 是**字符串**；
 * 直接插值数组会渲染成 ``[object Object]``。
 */
function detailField(key: string): string {
  const content = detailContent.value?.self_review
  if (!content) return ''
  return normalizeSelfReviewText(content[key])
}

/**
 * 查看已提交的自评。
 *
 * ⚠️ BUG-R6-43：过去这里是 ``router.push(`/performance/self-reviews/${recordId}`)``，
 * 但**该路由在 router/index.ts 里没有注册** —— Vue Router 匹配不到，
 * 整页渲染成空白，用户点「查看」只看到白屏，没有任何提示。
 *
 * 现在改为：当前页弹窗 + 真实接口 ``GET /performance/records/{id}/self-review``。
 */
async function viewSelfReview(recordId: string) {
  showDetailDialog.value = true
  detailLoading.value = true
  detailContent.value = null
  try {
    detailContent.value = await fetchSelfReviewDetail(recordId)
  } catch (err) {
    const message = err instanceof Error ? err.message : '加载自评详情失败'
    ElMessage.error(message)
  } finally {
    detailLoading.value = false
  }
}

onMounted(loadRecords)
</script>

<style scoped>
.employee-self-review {
  padding: 20px;
}

.page-header {
  margin-bottom: 30px;
}

.eyebrow {
  display: block;
  font-size: 12px;
  color: #909399;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.page-title {
  font-size: 28px;
  font-weight: 600;
  margin: 0 0 8px 0;
  color: #303133;
}

.page-subtitle {
  font-size: 14px;
  color: #606266;
  margin: 0;
}

.section-card {
  background: #fff;
  border-radius: 6px;
  border: 1px solid #ebeef5;
  padding: 20px;
  margin-bottom: 20px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
}

.section-header > div {
  flex: 1;
}

.section-header h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px 0;
  color: #303133;
}

.section-header p {
  font-size: 13px;
  color: #909399;
  margin: 0;
}

.records-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.record-card {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px;
  transition: all 0.3s;
}

.record-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
  border-color: #409eff;
}

.record-card-header {
  display: flex;
  justify-content: space-between;
  align-items: start;
  margin-bottom: 12px;
  gap: 8px;
}

.record-card-header h3 {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  color: #303133;
  flex: 1;
}

.record-card-body {
  margin-bottom: 12px;
  font-size: 13px;
}

.deadline-info,
.task-period {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.deadline-info .label,
.task-period .label {
  color: #909399;
}

.deadline-info .value,
.task-period .value {
  color: #303133;
  font-weight: 500;
}

.deadline-info .value.is-overdue {
  color: #f56c6c;
  font-weight: 600;
}

.record-card-footer {
  display: flex;
  gap: 8px;
}

.record-card-footer .el-button {
  flex: 1;
}

.self-review-form {
  padding: 20px 0;
}

.loading-state,
.error-state {
  padding: 40px;
}

.error-details {
  text-align: center;
}

.error-details h3 {
  margin-bottom: 10px;
  color: #f56c6c;
  font-size: 18px;
}

.error-details p {
  margin: 10px 0;
  color: #606266;
  font-size: 14px;
}

.mt20 {
  margin-top: 20px;
}

/* 自评详情弹窗（BUG-R6-43） */
.mb16 {
  margin-bottom: 16px;
}

.detail-block {
  margin-bottom: 18px;
}

.detail-block h4 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: #606266;
}

.detail-block p {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
