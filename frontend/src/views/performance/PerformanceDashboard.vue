<template>
  <section class="page-panel performance-dashboard">
    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="8" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-empty :image-size="120" description="数据加载失败">
        <template #default>
          <div class="error-details">
            <h3>⚠️ 无法连接到服务</h3>
            <p>{{ error }}</p>
            <el-button type="primary" @click="loadDashboard">重新加载</el-button>
          </div>
        </template>
      </el-empty>
    </div>

    <!-- 正常状态 -->
    <div v-else>
      <div class="page-header">
        <div>
          <span class="eyebrow">PERFORMANCE MANAGEMENT</span>
          <h1 class="page-title">绩效管理系统</h1>
          <p class="page-subtitle">完整的考核流程：任务发起 → 自评 → 主管评分 → 最终确认</p>
        </div>
        <div class="header-actions">
          <el-button type="primary" @click="showCreateTaskDialog = true">
            <span>✚</span> 发起考核任务
          </el-button>
        </div>
      </div>

      <!-- 统计卡片 -->
      <div class="metric-grid">
        <article class="metric primary">
          <span>进行中的任务</span>
          <strong>{{ stats.activeTasks }}</strong>
          <em>正在进行考核</em>
        </article>
        <article class="metric success">
          <span>已完成的考核</span>
          <strong>{{ stats.completedRecords }}</strong>
          <em>已确认成绩</em>
        </article>
        <article class="metric warning">
          <span>待自评</span>
          <strong>{{ stats.pendingSelfReview }}</strong>
          <em>需要员工填写</em>
        </article>
        <article class="metric info">
          <span>待主管评分</span>
          <strong>{{ stats.pendingManagerReview }}</strong>
          <em>需要主管评分</em>
        </article>
      </div>

      <!--
        P2-2 趋势复盘报表。
        放在统计卡片之后、任务列表之前 —— 领导进来第一屏就能看到
        「谁在上升、谁在下降、谁有风险」，而不是先翻列表。
      -->
      <div class="section-card mt16">
        <el-collapse v-model="trendExpanded" class="trend-collapse">
          <el-collapse-item name="trend">
            <template #title>
              <span class="collapse-title">📈 趋势复盘报表</span>
              <span class="collapse-hint">跨周期趋势 / 岗位横向 / 风险清单</span>
            </template>
            <TrendReportPanel />
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- 任务列表 -->
      <div class="section-card">
        <div class="section-header">
          <div>
            <h2>考核任务管理</h2>
            <p>查看、创建和管理所有的绩效考核任务</p>
          </div>
          <el-input
            v-model="searchText"
            placeholder="搜索任务..."
            style="width: 250px"
            clearable
            @keyup.enter="searchTasks"
          />
        </div>

        <el-table :data="filteredTasks" stripe style="width: 100%">
          <el-table-column prop="id" label="任务ID" width="120" />
          <el-table-column prop="name" label="任务名称" min-width="200" />
          <el-table-column prop="startDate" label="开始日期" width="120" />
          <el-table-column prop="endDate" label="结束日期" width="120" />
          <el-table-column prop="selfReviewDeadline" label="自评截止" width="120" />
          <el-table-column prop="managerReviewDeadline" label="评分截止" width="120" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="getTaskStatusType(row.status)">
                {{ getTaskStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="240" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                size="small"
                @click="viewTaskDetails(row.id)"
              >
                查看详情
              </el-button>
              <el-button
                v-if="row.status === 'created'"
                link
                type="success"
                size="small"
                @click="confirmTask(row.id)"
              >
                确认成绩
              </el-button>
              <!--
                P1-3 执行类指令：催办 / 导出都要走**两阶段确认**，
                所以这里只打开确认对话框，真正执行由对话框里的
                「确认执行」按钮触发。
              -->
              <el-button
                link
                type="warning"
                size="small"
                @click="openExecTool('exec_remind', row.id)"
              >
                催办
              </el-button>
              <el-button
                link
                type="warning"
                size="small"
                @click="openExecTool('exec_export', row.id)"
              >
                导出
              </el-button>
              <el-button
                link
                type="danger"
                size="small"
                @click="deleteTask(row.id)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 最近的考核记录 -->
      <div class="section-card mt16">
        <div class="section-header">
          <div>
            <h2>最近的考核记录</h2>
            <p>查看员工的最新考核结果和评分</p>
          </div>
          <el-button text type="primary" @click="router.push('/performance/records')">
            查看全部
          </el-button>
        </div>

        <el-table :data="recentRecords" stripe empty-text="暂无考核记录">
          <el-table-column prop="employeeName" label="员工" width="150" />
          <el-table-column prop="departmentName" label="部门" width="150" />
          <el-table-column prop="taskName" label="考核任务" min-width="200" />
          <el-table-column label="自评状态" width="120">
            <template #default="{ row }">
              <el-tag :type="getRecordStatusType(row.selfReviewStatus)">
                {{ getRecordStatusLabel(row.selfReviewStatus) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="评分状态" width="120">
            <template #default="{ row }">
              <el-tag :type="getRecordStatusType(row.managerReviewStatus)">
                {{ getRecordStatusLabel(row.managerReviewStatus) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="最终等级" width="100">
            <template #default="{ row }">
              <span v-if="row.finalGrade" class="grade-badge" :class="`grade-${row.finalGrade}`">
                {{ row.finalGrade }}
              </span>
              <span v-else class="grade-badge grade-pending">待评</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                size="small"
                @click="viewRecordDetails(row.id)"
              >
                查看记录
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 发起任务对话框 -->
    <el-dialog v-model="showCreateTaskDialog" title="发起考核任务" width="600px">
      <el-form :model="createTaskForm" label-width="120px">
        <el-form-item label="任务名称" required>
          <el-input
            v-model="createTaskForm.name"
            placeholder="例如：2026年Q4绩效考核"
          />
        </el-form-item>
        <el-form-item label="考核周期" required>
          <el-date-picker
            v-model="createTaskForm.dateRange"
            type="daterange"
            range-separator="到"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="自评截止" required>
          <el-date-picker
            v-model="createTaskForm.selfReviewDeadline"
            type="date"
            placeholder="选择日期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="评分截止" required>
          <el-date-picker
            v-model="createTaskForm.managerReviewDeadline"
            type="date"
            placeholder="选择日期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="createTaskForm.description"
            type="textarea"
            placeholder="任务说明..."
            rows="3"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateTaskDialog = false">取消</el-button>
        <el-button type="primary" @click="createTask" :loading="creating">
          发起任务
        </el-button>
      </template>
    </el-dialog>

    <!-- P1-3 执行类指令两阶段确认对话框 -->
    <ExecToolConfirmDialog
      ref="execToolDialog"
      :tool="execTool"
      :task-id="execToolTaskId"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import TrendReportPanel from '@/components/TrendReportPanel.vue'
import ExecToolConfirmDialog from '@/components/ExecToolConfirmDialog.vue'
import type { ExecToolName } from '@/api/p0p1'

const router = useRouter()
const loading = ref(false)
/** 趋势复盘默认折叠 —— 它是分析工具，不是主流程。 */
const trendExpanded = ref<string[]>([])
const creating = ref(false)
const error = ref('')
const searchText = ref('')
const showCreateTaskDialog = ref(false)

/** P1-3 执行类指令：当前打开的工具与目标任务。 */
const execToolDialog = ref<InstanceType<typeof ExecToolConfirmDialog> | null>(null)
const execTool = ref<ExecToolName>('exec_remind')
const execToolTaskId = ref('')

function openExecTool(tool: ExecToolName, taskId: string) {
  execTool.value = tool
  execToolTaskId.value = taskId
  execToolDialog.value?.open()
}

// 统计数据
const stats = reactive({
  activeTasks: 0,
  completedRecords: 0,
  pendingSelfReview: 0,
  pendingManagerReview: 0,
})

// 模拟数据
const tasks = ref<any[]>([
  {
    id: 'T001',
    name: '2026年Q4绩效考核',
    startDate: '2026-10-01',
    endDate: '2026-12-31',
    selfReviewDeadline: '2027-01-10',
    managerReviewDeadline: '2027-01-15',
    status: 'created',
  },
])

const recentRecords = ref<any[]>([
  {
    id: 'R001',
    employeeName: '张三',
    departmentName: '运营中心',
    taskName: '2026年Q4绩效考核',
    selfReviewStatus: 'submitted',
    managerReviewStatus: 'submitted',
    finalGrade: 'A',
  },
  {
    id: 'R002',
    employeeName: '李四',
    departmentName: '仓储部',
    taskName: '2026年Q4绩效考核',
    selfReviewStatus: 'submitted',
    managerReviewStatus: 'not_started',
    finalGrade: null,
  },
])

const createTaskForm = reactive({
  name: '',
  dateRange: [],
  selfReviewDeadline: null,
  managerReviewDeadline: null,
  description: '',
})

// 过滤任务
const filteredTasks = computed(() => {
  if (!searchText.value) return tasks.value
  return tasks.value.filter(
    (task) =>
      task.name.includes(searchText.value) ||
      task.id.includes(searchText.value)
  )
})

// 获取任务状态标签
function getTaskStatusLabel(status: string): string {
  const statusMap: Record<string, string> = {
    created: '创建中',
    in_progress: '进行中',
    confirmed: '已确认',
    completed: '已完成',
  }
  return statusMap[status] || status
}

// 获取任务状态类型
function getTaskStatusType(status: string): string {
  const typeMap: Record<string, string> = {
    created: 'info',
    in_progress: 'warning',
    confirmed: 'success',
    completed: 'success',
  }
  return typeMap[status] || 'info'
}

// 获取记录状态标签
function getRecordStatusLabel(status: string): string {
  const statusMap: Record<string, string> = {
    not_started: '未开始',
    in_progress: '进行中',
    submitted: '已提交',
    confirmed: '已确认',
  }
  return statusMap[status] || status
}

// 获取记录状态类型
function getRecordStatusType(status: string): string {
  const typeMap: Record<string, string> = {
    not_started: 'info',
    in_progress: 'warning',
    submitted: 'success',
    confirmed: 'success',
  }
  return typeMap[status] || 'info'
}

// 搜索任务
function searchTasks() {
  // 触发计算属性重新计算
}

// 加载仪表盘
async function loadDashboard() {
  loading.value = true
  error.value = ''
  try {
    // 这里应该调用后端 API 获取数据
    stats.activeTasks = tasks.value.filter((t) => t.status !== 'completed').length
    stats.completedRecords = recentRecords.value.filter(
      (r) => r.finalGrade
    ).length
    stats.pendingSelfReview = recentRecords.value.filter(
      (r) => r.selfReviewStatus === 'not_started'
    ).length
    stats.pendingManagerReview = recentRecords.value.filter(
      (r) =>
        r.managerReviewStatus === 'not_started' &&
        r.selfReviewStatus === 'submitted'
    ).length
  } catch (err) {
    error.value = err instanceof Error ? err.message : '未知错误'
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

// 发起任务
async function createTask() {
  if (!createTaskForm.name) {
    ElMessage.warning('请输入任务名称')
    return
  }
  if (!createTaskForm.dateRange || createTaskForm.dateRange.length !== 2) {
    ElMessage.warning('请选择考核周期')
    return
  }
  if (!createTaskForm.selfReviewDeadline) {
    ElMessage.warning('请选择自评截止日期')
    return
  }
  if (!createTaskForm.managerReviewDeadline) {
    ElMessage.warning('请选择评分截止日期')
    return
  }

  creating.value = true
  try {
    // 这里应该调用后端 API 创建任务
    const newTask = {
      id: `T${Math.random().toString().slice(2, 6)}`,
      name: createTaskForm.name,
      startDate: createTaskForm.dateRange[0],
      endDate: createTaskForm.dateRange[1],
      selfReviewDeadline: createTaskForm.selfReviewDeadline,
      managerReviewDeadline: createTaskForm.managerReviewDeadline,
      status: 'created',
    }
    tasks.value.push(newTask)
    ElMessage.success('考核任务已发起')
    showCreateTaskDialog.value = false

    // 重置表单
    createTaskForm.name = ''
    createTaskForm.dateRange = []
    createTaskForm.selfReviewDeadline = null
    createTaskForm.managerReviewDeadline = null
    createTaskForm.description = ''
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '发起任务失败')
  } finally {
    creating.value = false
  }
}

// 查看任务详情
function viewTaskDetails(taskId: string) {
  router.push(`/performance/tasks/${taskId}`)
}

// 查看记录详情
function viewRecordDetails(recordId: string) {
  router.push(`/performance/records/${recordId}`)
}

// 确认成绩
async function confirmTask(taskId: string) {
  try {
    await ElMessageBox.confirm(
      '确认后任务状态将变为已确认，员工无法再修改评分。是否继续？',
      '确认成绩',
      {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        type: 'warning',
      } as any
    )
    // 这里应该调用后端 API 确认成绩
    const task = tasks.value.find((t) => t.id === taskId)
    if (task) {
      task.status = 'confirmed'
    }
    ElMessage.success('成绩已确认')
  } catch {
    // 用户取消
  }
}

// 删除任务
async function deleteTask(taskId: string) {
  try {
    await ElMessageBox.confirm(
      '删除任务将同时删除所有相关的考核记录。是否继续？',
      '删除任务',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'danger',
      } as any
    )
    // 这里应该调用后端 API 删除任务
    tasks.value = tasks.value.filter((t) => t.id !== taskId)
    ElMessage.success('任务已删除')
  } catch {
    // 用户取消
  }
}

onMounted(loadDashboard)
</script>

<style scoped>
.performance-dashboard {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 30px;
}

.page-header > div:first-child {
  flex: 1;
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

.header-actions {
  margin-left: 20px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin-bottom: 30px;
}

.metric {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 20px;
  border-radius: 6px;
  background: #fff;
  border: 1px solid #ebeef5;
  transition: all 0.3s;
}

.metric:hover {
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

.metric > span {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
}

.metric > strong {
  font-size: 32px;
  font-weight: 600;
  color: #303133;
  line-height: 1;
  margin-bottom: 8px;
}

.metric > em {
  font-size: 12px;
  color: #b0b9c6;
  font-style: normal;
}

.metric.primary {
  border-left: 4px solid #409eff;
}

.metric.success {
  border-left: 4px solid #67c23a;
}

.metric.warning {
  border-left: 4px solid #e6a23c;
}

.metric.info {
  border-left: 4px solid #909399;
}

.section-card {
  background: #fff;
  border-radius: 6px;
  border: 1px solid #ebeef5;
  padding: 20px;
  margin-bottom: 20px;
}

.trend-collapse :deep(.el-collapse-item__header) {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  height: 32px;
  line-height: 32px;
  border-bottom: none;
}

.collapse-title {
  font-weight: 600;
}

.collapse-hint {
  font-size: 12px;
  font-weight: 400;
  color: #909399;
  margin-left: 10px;
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

.grade-badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 3px;
  font-size: 12px;
  font-weight: 600;
  text-align: center;
  min-width: 40px;
}

.grade-A {
  background-color: #c6e2ff;
  color: #0052cc;
}

.grade-B {
  background-color: #f0f9ff;
  color: #0066cc;
}

.grade-C {
  background-color: #fef3c7;
  color: #b45309;
}

.grade-D {
  background-color: #fee2e2;
  color: #dc2626;
}

.grade-pending {
  background-color: #f3f4f6;
  color: #6b7280;
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

.mt16 {
  margin-top: 16px;
}
</style>
