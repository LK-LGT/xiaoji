<template>
  <section class="page-panel manager-review">
    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="8" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-empty :image-size="120" description="数据加载失败">
        <template #default>
          <div class="error-details">
            <h3>⚠️ 无法加载待评员工</h3>
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
          <span class="eyebrow">MANAGER REVIEW</span>
          <h1 class="page-title">主管评分</h1>
          <p class="page-subtitle">
            对你的下属进行绩效评分，包括等级评定和评价意见
          </p>
        </div>
        <!-- 周期筛选：只有可见周期才出现在下拉里 -->
        <el-select
          v-model="selectedPeriod"
          placeholder="全部周期"
          clearable
          style="width: 180px"
          @change="handlePeriodChange"
        >
          <el-option
            v-for="p in visiblePeriods"
            :key="p.period"
            :label="`${p.period_display}（待评 ${p.pending_count}）`"
            :value="p.period"
          />
        </el-select>
      </div>

      <!-- 待评下属 -->
      <div class="section-card">
        <div class="section-header">
          <div>
            <h2>待评的下属</h2>
            <p>
              已自评完毕，请完成你的评分
              <span class="count">({{ pendingRecords.length }})</span>
            </p>
          </div>
          <el-input
            v-model="searchText"
            placeholder="搜索员工..."
            style="width: 250px"
            clearable
            @keyup.enter="searchRecords"
          />
        </div>

        <el-table
          :data="filteredPendingRecords"
          stripe
          style="width: 100%"
          empty-text="暂无待评员工"
          v-loading="recordsLoading"
        >
          <el-table-column label="员工姓名" width="150">
            <template #default="{ row }">
              {{ row.employee?.name || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="考核任务" min-width="180">
            <template #default="{ row }">
              {{ row.task_name || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="周期" width="110">
            <template #default="{ row }">
              <el-tag size="small" type="info" effect="plain">
                {{ row.period_display || '—' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="自评提交" width="130">
            <template #default="{ row }">
              <span style="color: #67c23a; font-weight: 500">
                ✓ {{ formatDate(row.updated_at) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                size="small"
                @click="viewSelfReview(row)"
              >
                查看自评
              </el-button>
              <el-button
                link
                type="primary"
                size="small"
                @click="submitReview(row)"
              >
                进行评分
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 已评下属 -->
      <div class="section-card mt20">
        <div class="section-header">
          <div>
            <h2>已评的下属</h2>
            <p>你已完成的评分记录</p>
          </div>
        </div>

        <el-table
          :data="completedRecords"
          stripe
          style="width: 100%"
          empty-text="暂无已评记录"
          v-loading="recordsLoading"
        >
          <el-table-column label="员工姓名" width="150">
            <template #default="{ row }">
              {{ row.employee?.name || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="考核任务" min-width="180">
            <template #default="{ row }">
              {{ row.task_name || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="周期" width="110">
            <template #default="{ row }">
              <el-tag size="small" type="info" effect="plain">
                {{ row.period_display || '—' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="评分等级" width="120">
            <template #default="{ row }">
              <el-tag :type="getGradeType(row.confirmed_rating)">
                {{ row.confirmed_rating || '—' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="提交时间" width="180">
            <template #default="{ row }">
              {{ formatDateTime(row.updated_at) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                size="small"
                @click="viewReview(row)"
              >
                查看评分
              </el-button>
              <el-button
                link
                type="primary"
                size="small"
                @click="editReview(row)"
              >
                修改
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 自评预览对话框 -->
    <el-dialog
      v-model="showSelfReviewDialog"
      title="员工自评"
      class="manager-review-dialog"
      width="min(760px, calc(100vw - 32px))"
      append-to-body
      align-center
    >
      <div v-if="selectedRecord" class="review-preview">
        <div class="review-section">
          <h3>员工信息</h3>
          <div class="info-row">
            <span class="label">姓名：</span>
            <span class="value">{{ selectedRecord.employee?.name || '—' }}</span>
          </div>
          <div class="info-row">
            <span class="label">岗位：</span>
            <span class="value">{{ selectedRecord.employee?.position || '—' }}</span>
          </div>
          <div class="info-row">
            <span class="label">任务：</span>
            <span class="value">{{ selectedRecord.task_name || '—' }}</span>
          </div>
          <div class="info-row">
            <span class="label">周期：</span>
            <span class="value">{{ selectedRecord.period_display || '—' }}</span>
          </div>
        </div>

        <!-- 考勤参考面板 -->
        <div class="review-section">
          <h3>考勤参考</h3>
          <AttendancePanel
            :period="detail?.period || selectedRecord.period"
            :summary="attendanceSummary"
            :reference-evidence="detail?.evidence?.reference_only || []"
            :loading="attendanceLoading"
            :error="attendanceError"
            @retry="loadAttendance"
          />
        </div>

        <div class="review-section">
          <h3>自评内容</h3>
          <div class="content-block">
            <div class="content-title">业绩总结</div>
            <div class="content-text">
              {{ detail?.self_review?.performance_summary || '未填写' }}
            </div>
          </div>
          <div class="content-block">
            <div class="content-title">主要成就</div>
            <div class="content-text">
              {{ formatList(detail?.self_review?.achievements) }}
            </div>
          </div>
          <div class="content-block">
            <div class="content-title">遇到的挑战</div>
            <div class="content-text">
              {{ formatList(detail?.self_review?.challenges) }}
            </div>
          </div>
          <div class="content-block">
            <div class="content-title">改进方向</div>
            <div class="content-text">
              {{ detail?.self_review?.improvements || '未填写' }}
            </div>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- 评分表单对话框 -->
    <el-dialog
      v-model="showReviewFormDialog"
      title="进行评分"
      class="manager-review-dialog"
      width="min(760px, calc(100vw - 32px))"
      append-to-body
      align-center
      @close="handleFormDialogClose"
    >
      <div v-if="selectedRecord" class="review-form">
        <div class="employee-info">
          <strong>{{ selectedRecord.employee?.name || '—' }}</strong>
          <span class="task-name">
            {{ selectedRecord.task_name || '—' }} · {{ selectedRecord.period_display || '—' }}
          </span>
        </div>

        <!-- 考勤参考面板 -->
        <div class="attendance-section">
          <AttendancePanel
            :period="detail?.period || selectedRecord.period"
            :summary="attendanceSummary"
            :reference-evidence="detail?.evidence?.reference_only || []"
            :loading="attendanceLoading"
            :error="attendanceError"
            @retry="loadAttendance"
          />
        </div>

        <!--
          P2-1 评语质检 + 面谈提纲。
          放在评价意见**上方**是有意的 —— 主管写评语时就能看到
          「空洞措辞」「无据推断」这类问题，而不是写完提交后再回来找。
        -->
        <SemanticQcPanel :record-id="selectedRecord.record_id" class="semantic-qc-section" />

        <!--
          P0-1 互评：主管在评分前先看互评回收进度，
          互评意见是评分参考材料（不会自动改分）。
        -->
        <PeerReviewPanel
          :record-id="selectedRecord.record_id"
          :task-id="selectedRecord.task_id"
          :can-assign="canManagePeers"
          class="peer-review-section"
        />

        <!--
          P0-2 分布质检：只提供统计观察与核查线索，**不改分**。
          放在评分表单上方，主管能带着分布背景做判断。
        -->
        <DistributionQcPanel
          :period="selectedRecord.period"
          :department-ids="selectedRecord.employee?.department_id || ''"
          class="distribution-qc-section"
        />

        <el-form :model="reviewFormData" label-width="100px">
          <el-form-item label="评分等级" required>
            <el-radio-group v-model="reviewFormData.grade">
              <el-radio-button label="A">
                <span>A - 优秀</span>
              </el-radio-button>
              <el-radio-button label="B">
                <span>B - 良好</span>
              </el-radio-button>
              <el-radio-button label="C">
                <span>C - 中等</span>
              </el-radio-button>
              <el-radio-button label="D">
                <span>D - 需改进</span>
              </el-radio-button>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="评价意见" required>
            <el-input
              v-model="reviewFormData.comment"
              type="textarea"
              placeholder="给出你对该员工的评价..."
              rows="6"
              maxlength="2000"
              show-word-limit
            />
          </el-form-item>

          <el-form-item label="强项">
            <el-input
              v-model="reviewFormData.strengths"
              type="textarea"
              placeholder="列出该员工的主要优势和强项..."
              rows="3"
              maxlength="500"
              show-word-limit
            />
          </el-form-item>

          <el-form-item label="改进建议">
            <el-input
              v-model="reviewFormData.improvements"
              type="textarea"
              placeholder="提出改进建议..."
              rows="3"
              maxlength="500"
              show-word-limit
            />
          </el-form-item>
        </el-form>

        <!-- 自评内容参考 -->
        <div class="self-review-reference">
          <div class="reference-title">📋 该员工的自评内容（参考）</div>
          <div class="reference-content">
            <div class="ref-item">
              <strong>业绩总结：</strong>
              {{ detail?.self_review?.performance_summary || '未填写' }}
            </div>
            <div class="ref-item">
              <strong>主要成就：</strong>
              {{ formatList(detail?.self_review?.achievements) }}
            </div>
          </div>
        </div>

        <!--
          P2-3 反馈闭环。
          评分提交后，主管顺手记录「AI 草稿好不好用」——
          这是 Prompt 版本取舍的唯一可信依据（模型自评不算数）。
          ⚠️ 反馈**不改分数**，只落 ai_feedback 表。
        -->
        <div class="feedback-section">
          <div class="reference-title">💬 AI 草稿反馈（可选）</div>
          <div class="feedback-body">
            <el-radio-group v-model="feedbackAction" size="small">
              <el-radio-button label="ACCEPT">采纳</el-radio-button>
              <el-radio-button label="MODIFY">修改后采纳</el-radio-button>
              <el-radio-button label="REJECT">拒绝</el-radio-button>
            </el-radio-group>
            <el-input
              v-model="feedbackReason"
              size="small"
              placeholder="简单说明原因（可选）"
              class="feedback-reason"
            />
            <el-button
              size="small"
              :loading="feedbackSubmitting"
              :disabled="!feedbackAction"
              @click="submitFeedback"
            >
              提交反馈
            </el-button>
          </div>
          <p class="feedback-note">反馈只记录你对 AI 产出的评价，不会修改分数或已确认等级。</p>
        </div>
      </div>

      <template #footer>
        <el-button @click="showReviewFormDialog = false">取消</el-button>
        <el-button type="primary" @click="saveReview" :loading="submitting">
          提交评分
        </el-button>
      </template>    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import AttendancePanel from './AttendancePanel.vue'
import SemanticQcPanel from '@/components/SemanticQcPanel.vue'
import PeerReviewPanel from '@/components/PeerReviewPanel.vue'
import DistributionQcPanel from '@/components/DistributionQcPanel.vue'
import { feedbackApi } from '@/api/p2'
import { http } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import {
  fetchAssessmentRecord,
  fetchAttendanceSummary,
  fetchPendingRecords,
  fetchVisiblePeriods,
  periodDateRange,
  type AssessmentRecordDetail,
  type AttendanceSummary,
  type PendingAssessmentRecord,
  type VisiblePeriod,
} from '@/api/attendance'

const loading = ref(false)
const recordsLoading = ref(false)
const submitting = ref(false)
const error = ref('')
const searchText = ref('')
const showSelfReviewDialog = ref(false)
const showReviewFormDialog = ref(false)
const selectedRecord = ref<PendingAssessmentRecord | null>(null)
const authStore = useAuthStore()
/** P0-1：主管 / 部门负责人 / HR 可分派互评人。 */
const canManagePeers = computed(() =>
  (authStore.user?.roles || []).some((r) =>
    ['manager', 'department_leader', 'hr'].includes(r),
  ),
)

/** P2-3 AI 草稿反馈。 */
const feedbackAction = ref<'ACCEPT' | 'MODIFY' | 'REJECT' | ''>('')
const feedbackReason = ref('')
const feedbackSubmitting = ref(false)

/** 全部可见记录（含待评与已评）。 */
const allRecords = ref<PendingAssessmentRecord[]>([])
/** 可见周期列表。 */
const visiblePeriods = ref<VisiblePeriod[]>([])
/** 当前周期筛选（null = 全部）。 */
const selectedPeriod = ref<string | null>(null)

/** 当前选中记录的详情（含自评 + 按周期过滤后的证据）。 */
const detail = ref<AssessmentRecordDetail | null>(null)
/** 考勤统计汇总。 */
const attendanceSummary = ref<AttendanceSummary | null>(null)
const attendanceLoading = ref(false)
const attendanceError = ref('')

const reviewFormData = reactive({
  grade: 'B',
  comment: '',
  strengths: '',
  improvements: '',
})

/** 待评状态：员工已交自评，等待主管评分。 */
const PENDING_STATUS = 'self_review_submitted'

const pendingRecords = computed(() =>
  allRecords.value.filter((r) => r.status === PENDING_STATUS),
)

const filteredPendingRecords = computed(() => {
  const keyword = searchText.value.trim()
  if (!keyword) return pendingRecords.value
  return pendingRecords.value.filter((r) => {
    const name = r.employee?.name || ''
    const task = r.task_name || ''
    return name.includes(keyword) || task.includes(keyword)
  })
})

const completedRecords = computed(() =>
  allRecords.value.filter((r) => r.status !== PENDING_STATUS),
)

function getGradeType(grade?: string | null): string {
  const typeMap: Record<string, string> = {
    A: 'success',
    B: 'primary',
    C: 'warning',
    D: 'danger',
  }
  return typeMap[grade || ''] || 'info'
}

function formatDate(date?: string | null): string {
  if (!date) return '—'
  const parsed = dayjs(date)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD') : '—'
}

function formatDateTime(dateTime?: string | null): string {
  if (!dateTime) return '—'
  const parsed = dayjs(dateTime)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : '—'
}

/** 自评里的数组或字符串统一渲染。 */
function formatList(value?: string[] | string | null): string {
  if (!value) return '未填写'
  if (Array.isArray(value)) {
    return value.length ? value.join('；') : '未填写'
  }
  return value
}

function searchRecords() {
  // 触发计算属性重新计算（保持与既有交互一致）
}

async function loadRecords() {
  loading.value = true
  error.value = ''
  recordsLoading.value = true
  try {
    // 并行拉取记录列表与可见周期
    const [records, periods] = await Promise.all([
      fetchPendingRecords({ includeAllStatuses: true }),
      fetchVisiblePeriods(),
    ])
    allRecords.value = records.items || []
    visiblePeriods.value = periods.items || []
  } catch (err) {
    error.value = err instanceof Error ? err.message : '未知错误'
    ElMessage.error(error.value)
  } finally {
    loading.value = false
    recordsLoading.value = false
  }
}

async function handlePeriodChange() {
  await loadRecords()
}

/** 载入选中记录的详情 + 考勤统计。 */
async function loadDetail(record: PendingAssessmentRecord) {
  detail.value = null
  attendanceSummary.value = null
  attendanceError.value = ''
  try {
    detail.value = await fetchAssessmentRecord(record.record_id)
  } catch (err) {
    attendanceError.value = err instanceof Error ? err.message : '记录详情加载失败'
  }
  await loadAttendance()
}

/** 载入考勤统计（按考核周期计算起止日期）。 */
async function loadAttendance() {
  const record = selectedRecord.value
  const period = detail.value?.period || record?.period
  const employeeId = detail.value?.employee?.id || record?.employee?.id
  if (!record || !period || !employeeId) {
    attendanceSummary.value = null
    return
  }

  const range = periodDateRange(period)
  if (!range) {
    attendanceError.value = `考核周期格式非法：${period}`
    attendanceSummary.value = null
    return
  }

  attendanceLoading.value = true
  attendanceError.value = ''
  try {
    attendanceSummary.value = await fetchAttendanceSummary({
      employeeId,
      startDate: range.startDate,
      endDate: range.endDate,
    })
  } catch (err) {
    attendanceSummary.value = null
    attendanceError.value = err instanceof Error ? err.message : '考勤统计加载失败'
  } finally {
    attendanceLoading.value = false
  }
}

async function viewSelfReview(record: PendingAssessmentRecord) {
  selectedRecord.value = record
  showSelfReviewDialog.value = true
  await loadDetail(record)
}

async function submitReview(record: PendingAssessmentRecord) {
  selectedRecord.value = record
  reviewFormData.grade = record.confirmed_rating || 'B'
  reviewFormData.comment = record.manager_comment || ''
  reviewFormData.strengths = ''
  reviewFormData.improvements = ''
  feedbackAction.value = ''
  feedbackReason.value = ''
  showReviewFormDialog.value = true
  await loadDetail(record)
}

function handleFormDialogClose() {
  showReviewFormDialog.value = false
  selectedRecord.value = null
  detail.value = null
  attendanceSummary.value = null
}

async function saveReview() {
  if (!reviewFormData.grade) {
    ElMessage.warning('请选择评分等级')
    return
  }
  if (!reviewFormData.comment.trim()) {
    ElMessage.warning('请填写评价意见')
    return
  }
  if (!selectedRecord.value) return

  submitting.value = true
  try {
    // 后端契约：confirmed_rating + manager_comment
    // （后端 AssessmentRecord 暂未提供 strengths / improvements 落库字段，
    //   这两项目前仅作草稿展示，不随请求提交。）
    await http.post(
      `/assessment/records/${selectedRecord.value.record_id}/manager-review`,
      {
        confirmed_rating: reviewFormData.grade,
        manager_comment: reviewFormData.comment,
      },
    )
    ElMessage.success('评分已提交')
    showReviewFormDialog.value = false
    await loadRecords()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

/**
 * P2-3 提交 AI 草稿反馈。
 *
 * ⚠️ 反馈**只写 ai_feedback**，不碰评分、不碰等级。
 * 失败只提示，不阻断评分流程 —— 反馈是可选的，不能因为反馈接口挂了
 * 就让主管提交不了评分。
 */
async function submitFeedback() {
  if (!selectedRecord.value || !feedbackAction.value) return
  feedbackSubmitting.value = true
  try {
    await feedbackApi.submit({
      record_id: selectedRecord.value.record_id,
      action: feedbackAction.value,
      agent: 'comment_draft',
      reason: feedbackReason.value || null,
    })
    ElMessage.success('反馈已记录')
    feedbackAction.value = ''
    feedbackReason.value = ''
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '反馈提交失败')
  } finally {
    feedbackSubmitting.value = false
  }
}

async function viewReview(record: PendingAssessmentRecord) {
  selectedRecord.value = record
  reviewFormData.grade = record.confirmed_rating || 'B'
  reviewFormData.comment = record.manager_comment || ''
  reviewFormData.strengths = ''
  reviewFormData.improvements = ''
  feedbackAction.value = ''
  feedbackReason.value = ''
  showReviewFormDialog.value = true
  await loadDetail(record)
}

function editReview(record: PendingAssessmentRecord) {
  submitReview(record)
}

onMounted(loadRecords)
</script>

<style scoped>
.manager-review {
  padding: 20px;
}

.page-header {
  margin-bottom: 30px;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
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

.count {
  color: #409eff;
  font-weight: 600;
}

.review-preview {
  line-height: 1.8;
}

.review-section {
  margin-bottom: 20px;
}

.review-section h3 {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 12px 0;
  padding-bottom: 8px;
  border-bottom: 1px solid #ebeef5;
  color: #303133;
}

.info-row {
  display: flex;
  margin-bottom: 8px;
  font-size: 13px;
}

.info-row .label {
  color: #909399;
  width: 80px;
  flex-shrink: 0;
}

.info-row .value {
  color: #303133;
  font-weight: 500;
  flex: 1;
}

.content-block {
  margin-bottom: 12px;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
}

.content-title {
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  margin-bottom: 6px;
}

.content-text {
  font-size: 13px;
  color: #303133;
  line-height: 1.6;
  white-space: pre-wrap;
}

.review-form {
  padding: 20px 0;
}

.employee-info {
  display: flex;
  align-items: center;
  padding: 12px;
  margin-bottom: 16px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 13px;
  color: #303133;
}

.employee-info strong {
  font-weight: 600;
  margin-right: 8px;
}

.task-name {
  margin-left: auto;
  color: #909399;
}

.attendance-section {
  margin-bottom: 18px;
}

.semantic-qc-section {
  margin-bottom: 18px;
}

.feedback-section {
  margin-top: 16px;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  border-left: 3px solid #67c23a;
}

.feedback-body {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.feedback-reason {
  flex: 1;
  min-width: 220px;
}

.feedback-note {
  font-size: 11px;
  color: #909399;
  margin: 8px 0 0 0;
  line-height: 1.6;
}

.self-review-reference {
  margin-top: 16px;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  border-left: 3px solid #409eff;
}

.reference-title {
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  margin-bottom: 8px;
}

.reference-content {
  font-size: 12px;
  color: #606266;
}

.ref-item {
  margin-bottom: 6px;
  line-height: 1.5;
}

.ref-item strong {
  color: #303133;
  font-weight: 600;
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
</style>

<style>
/* 主管评分弹窗：挂到 body 后需自行保证层级、居中与可滚动。 */
.manager-review-dialog.el-dialog {
  display: flex;
  flex-direction: column;
  max-height: calc(100dvh - 48px);
  margin: 0;
  overflow: hidden;
}
.manager-review-dialog .el-dialog__header,
.manager-review-dialog .el-dialog__footer {
  flex-shrink: 0;
}
.manager-review-dialog .el-dialog__body {
  min-height: 0;
  overflow: auto;
}
.manager-review-dialog .el-dialog__header {
  margin-right: 0;
}
.manager-review-dialog .el-overlay-dialog {
  padding: 24px 16px;
  box-sizing: border-box;
}
@media (max-width: 600px) {
  .manager-review-dialog.el-dialog {
    width: calc(100vw - 24px) !important;
    max-height: calc(100dvh - 24px);
  }
  .manager-review-dialog .el-overlay-dialog {
    padding: 12px;
  }
  .manager-review-dialog .el-form-item {
    display: block;
  }
  .manager-review-dialog .el-form-item__label {
    width: auto !important;
    padding-bottom: 4px;
    text-align: left;
  }
  .manager-review-dialog .el-form-item__content {
    margin-left: 0 !important;
  }
}
</style>
