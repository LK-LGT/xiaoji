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
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'

const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const submitting = ref(false)
const error = ref('')
const showEditDrawer = ref(false)
const editingRecordId = ref('')
const editingTaskId = ref('')

// 模拟数据
const allRecords = ref<any[]>([
  {
    id: 'R001',
    taskId: 'T001',
    taskName: '2026年Q4绩效考核',
    periodStart: '2026-10-01',
    periodEnd: '2026-12-31',
    deadline: '2027-01-10',
    status: 'not_started',
    selfReview: null,
    submittedAt: null,
  },
  {
    id: 'R002',
    taskId: 'T001',
    taskName: '2026年Q3绩效考核',
    periodStart: '2026-07-01',
    periodEnd: '2026-09-30',
    deadline: '2026-10-10',
    status: 'submitted',
    selfReview: {
      performanceSummary: '本期主要完成了...',
      achievements: '完成了X项工作...',
      challenges: '遇到的主要困难是...',
      improvements: '下一步计划...',
      remarks: '',
    },
    submittedAt: '2026-10-08 14:30:00',
  },
])

const reviewForm = reactive({
  performanceSummary: '',
  achievements: '',
  challenges: '',
  improvements: '',
  remarks: '',
})

const editingRecord = computed(() => {
  return allRecords.value.find((r) => r.id === editingRecordId.value)
})

// 待自评的任务
const pendingRecords = computed(() => {
  return allRecords.value.filter((r) => r.status === 'not_started')
})

// 已提交的自评
const submittedRecords = computed(() => {
  return allRecords.value.filter((r) => r.status === 'submitted')
})

function getStatusLabel(status: string): string {
  const statusMap: Record<string, string> = {
    not_started: '未开始',
    in_progress: '进行中',
    submitted: '已提交',
    confirmed: '已确认',
  }
  return statusMap[status] || status
}

function getStatusType(status: string): string {
  const typeMap: Record<string, string> = {
    not_started: 'info',
    in_progress: 'warning',
    submitted: 'success',
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

function loadRecords() {
  loading.value = true
  error.value = ''
  try {
    // 这里应该调用后端 API 加载记录
    loading.value = false
  } catch (err) {
    error.value = err instanceof Error ? err.message : '未知错误'
    ElMessage.error(error.value)
    loading.value = false
  }
}

function editSelfReview(recordId: string, taskId: string) {
  editingRecordId.value = recordId
  editingTaskId.value = taskId

  const record = allRecords.value.find((r) => r.id === recordId)
  if (record?.selfReview) {
    Object.assign(reviewForm, record.selfReview)
  } else {
    reviewForm.performanceSummary = ''
    reviewForm.achievements = ''
    reviewForm.challenges = ''
    reviewForm.improvements = ''
    reviewForm.remarks = ''
  }

  showEditDrawer.value = true
}

function handleDrawerClose() {
  showEditDrawer.value = false
  editingRecordId.value = ''
  editingTaskId.value = ''
}

async function saveDraft() {
  if (!editingRecord.value) return

  saving.value = true
  try {
    // 这里应该调用后端 API 保存草稿
    const record = allRecords.value.find((r) => r.id === editingRecordId.value)
    if (record) {
      record.selfReview = { ...reviewForm }
      record.status = 'in_progress'
    }
    ElMessage.success('草稿已保存')
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

  submitting.value = true
  try {
    // 这里应该调用后端 API 提交自评
    const record = allRecords.value.find((r) => r.id === editingRecordId.value)
    if (record) {
      record.selfReview = { ...reviewForm }
      record.status = 'submitted'
      record.submittedAt = dayjs().format('YYYY-MM-DD HH:mm:ss')
    }
    ElMessage.success('自评已提交')
    showEditDrawer.value = false
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

function viewSelfReview(recordId: string) {
  router.push(`/performance/self-reviews/${recordId}`)
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
</style>
