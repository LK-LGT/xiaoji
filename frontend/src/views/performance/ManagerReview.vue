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
        >
          <el-table-column prop="employeeName" label="员工姓名" width="150" />
          <el-table-column prop="departmentName" label="部门" width="150" />
          <el-table-column prop="taskName" label="考核任务" min-width="150" />
          <el-table-column label="自评提交" width="130">
            <template #default="{ row }">
              <span style="color: #67c23a; font-weight: 500">
                ✓ {{ formatDate(row.selfReviewSubmittedAt) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
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
        >
          <el-table-column prop="employeeName" label="员工姓名" width="150" />
          <el-table-column prop="departmentName" label="部门" width="150" />
          <el-table-column prop="taskName" label="考核任务" min-width="150" />
          <el-table-column label="评分等级" width="120">
            <template #default="{ row }">
              <el-tag :type="getGradeType(row.grade)">
                {{ row.grade }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="提交时间" width="180">
            <template #default="{ row }">
              {{ formatDateTime(row.submittedAt) }}
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
      width="700px"
    >
      <div v-if="selectedRecord" class="review-preview">
        <div class="review-section">
          <h3>员工信息</h3>
          <div class="info-row">
            <span class="label">姓名：</span>
            <span class="value">{{ selectedRecord.employeeName }}</span>
          </div>
          <div class="info-row">
            <span class="label">部门：</span>
            <span class="value">{{ selectedRecord.departmentName }}</span>
          </div>
          <div class="info-row">
            <span class="label">任务：</span>
            <span class="value">{{ selectedRecord.taskName }}</span>
          </div>
        </div>

        <div class="review-section">
          <h3>自评内容</h3>
          <div class="content-block">
            <div class="content-title">业绩总结</div>
            <div class="content-text">
              {{ selectedRecord.selfReview?.performanceSummary }}
            </div>
          </div>
          <div class="content-block">
            <div class="content-title">主要成就</div>
            <div class="content-text">
              {{ selectedRecord.selfReview?.achievements }}
            </div>
          </div>
          <div class="content-block">
            <div class="content-title">遇到的挑战</div>
            <div class="content-text">
              {{ selectedRecord.selfReview?.challenges }}
            </div>
          </div>
          <div class="content-block">
            <div class="content-title">改进方向</div>
            <div class="content-text">
              {{ selectedRecord.selfReview?.improvements || '未填写' }}
            </div>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- 评分表单对话框 -->
    <el-dialog
      v-model="showReviewFormDialog"
      title="进行评分"
      width="700px"
      @close="handleFormDialogClose"
    >
      <div v-if="selectedRecord" class="review-form">
        <div class="employee-info">
          <strong>{{ selectedRecord.employeeName }}</strong> -
          {{ selectedRecord.departmentName }}
          <span class="task-name">{{ selectedRecord.taskName }}</span>
        </div>

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
              maxlength="1000"
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
              {{ selectedRecord.selfReview?.performanceSummary }}
            </div>
            <div class="ref-item">
              <strong>主要成就：</strong>
              {{ selectedRecord.selfReview?.achievements }}
            </div>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="showReviewFormDialog = false">取消</el-button>
        <el-button type="primary" @click="saveReview" :loading="submitting">
          提交评分
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'

const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const searchText = ref('')
const showSelfReviewDialog = ref(false)
const showReviewFormDialog = ref(false)
const selectedRecord = ref<any>(null)

// 模拟数据
const allRecords = ref<any[]>([
  {
    id: 'R001',
    taskId: 'T001',
    employeeName: '张三',
    departmentName: '运营中心',
    taskName: '2026年Q4绩效考核',
    status: 'pending_manager_review',
    selfReviewSubmittedAt: '2026-12-20 10:30:00',
    selfReview: {
      performanceSummary: '本期主要负责直播部的日常运营管理，成功组织了10场大型直播活动...',
      achievements: '完成目标销售额120%，获得公司优秀员工表彰...',
      challenges: '面对市场竞争压力和团队人手不足的问题...',
      improvements: '下一步计划加强团队建设和技能培训...',
    },
    grade: null,
    comment: null,
    strengths: null,
    improvements: null,
    submittedAt: null,
  },
  {
    id: 'R002',
    taskId: 'T001',
    employeeName: '李四',
    departmentName: '仓储部',
    taskName: '2026年Q4绩效考核',
    status: 'pending_manager_review',
    selfReviewSubmittedAt: '2026-12-21 14:15:00',
    selfReview: {
      performanceSummary: '负责广州仓库的日常管理和物流协调工作...',
      achievements: '库存准确率达到99.5%，处理了50起客户投诉且满意度达95%...',
      challenges: '新系统部署过程中遇到了一些兼容性问题...',
      improvements: '继续优化仓库流程，提升工作效率...',
    },
    grade: 'B',
    comment: '工作认真负责，完成良好，有待进一步提升',
    strengths: '执行力强，细心谨慎',
    improvements: '可以多主动学习新知识',
    submittedAt: '2026-12-25 16:00:00',
  },
])

const reviewFormData = reactive({
  grade: 'B',
  comment: '',
  strengths: '',
  improvements: '',
})

// 待评的记录
const pendingRecords = computed(() => {
  return allRecords.value.filter((r) => r.status === 'pending_manager_review')
})

// 过滤待评的记录
const filteredPendingRecords = computed(() => {
  if (!searchText.value) return pendingRecords.value
  return pendingRecords.value.filter(
    (r) =>
      r.employeeName.includes(searchText.value) ||
      r.departmentName.includes(searchText.value)
  )
})

// 已评的记录
const completedRecords = computed(() => {
  return allRecords.value.filter((r) => r.status !== 'pending_manager_review')
})

function getGradeType(grade: string): string {
  const typeMap: Record<string, string> = {
    A: 'success',
    B: 'primary',
    C: 'warning',
    D: 'danger',
  }
  return typeMap[grade] || 'info'
}

function formatDate(date: string): string {
  return dayjs(date).format('YYYY-MM-DD')
}

function formatDateTime(dateTime: string): string {
  return dayjs(dateTime).format('YYYY-MM-DD HH:mm')
}

function searchRecords() {
  // 触发计算属性重新计算
}

function loadRecords() {
  loading.value = true
  error.value = ''
  try {
    // 这里应该调用后端 API 加载待评记录
    loading.value = false
  } catch (err) {
    error.value = err instanceof Error ? err.message : '未知错误'
    ElMessage.error(error.value)
    loading.value = false
  }
}

function viewSelfReview(record: any) {
  selectedRecord.value = record
  showSelfReviewDialog.value = true
}

function submitReview(record: any) {
  selectedRecord.value = record
  reviewFormData.grade = record.grade || 'B'
  reviewFormData.comment = record.comment || ''
  reviewFormData.strengths = record.strengths || ''
  reviewFormData.improvements = record.improvements || ''
  showReviewFormDialog.value = true
}

function handleFormDialogClose() {
  showReviewFormDialog.value = false
  selectedRecord.value = null
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

  submitting.value = true
  try {
    // 这里应该调用后端 API 保存评分
    if (selectedRecord.value) {
      selectedRecord.value.grade = reviewFormData.grade
      selectedRecord.value.comment = reviewFormData.comment
      selectedRecord.value.strengths = reviewFormData.strengths
      selectedRecord.value.improvements = reviewFormData.improvements
      selectedRecord.value.status = 'manager_review_submitted'
      selectedRecord.value.submittedAt = dayjs().format('YYYY-MM-DD HH:mm:ss')
    }
    ElMessage.success('评分已提交')
    showReviewFormDialog.value = false
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

function viewReview(record: any) {
  selectedRecord.value = record
  reviewFormData.grade = record.grade || 'B'
  reviewFormData.comment = record.comment || ''
  reviewFormData.strengths = record.strengths || ''
  reviewFormData.improvements = record.improvements || ''
  showReviewFormDialog.value = true
}

function editReview(record: any) {
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
