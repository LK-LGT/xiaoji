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
          <el-button type="primary" @click="openCreateTask">
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

        <el-table :data="filteredTasks" stripe style="width: 100%" empty-text="暂无考核任务">
          <el-table-column prop="task_id" label="任务ID" width="140" show-overflow-tooltip />
          <el-table-column prop="name" label="任务名称" min-width="200" />
          <el-table-column prop="period" label="周期" width="100" />
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
                @click="viewTaskDetails(row.task_id)"
              >
                查看详情
              </el-button>
              <el-button
                v-if="row.status === 'created'"
                link
                type="success"
                size="small"
                @click="confirmTask(row.task_id)"
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
                @click="openExecTool('exec_remind', row.task_id)"
              >
                催办
              </el-button>
              <el-button
                link
                type="warning"
                size="small"
                @click="openExecTool('exec_export', row.task_id)"
              >
                导出
              </el-button>
              <el-button
                link
                type="danger"
                size="small"
                @click="deleteTask(row.task_id)"
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
          <el-button text type="primary" @click="router.push('/performance/scheme-filling')">
            查看全部
          </el-button>
        </div>

        <el-table :data="recentRecords" stripe empty-text="暂无考核记录">
          <el-table-column prop="employee_name" label="员工" width="150" />
          <el-table-column prop="name" label="考核任务" min-width="200" />
          <el-table-column prop="period" label="周期" width="100" />
          <el-table-column label="状态" width="140">
            <template #default="{ row }">
              <el-tag :type="getRecordStatusType(row.status)">
                {{ getRecordStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="归属" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.is_own" type="info" effect="plain">本人</el-tag>
              <span v-else class="muted">下属</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
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
      <el-alert
        title="考核任务需绑定员工，并按方案冻结配置快照，否则主管提交评分时会失败。"
        type="info"
        :closable="false"
        class="mb12"
      />
      <el-form :model="createTaskForm" label-width="120px">
        <el-form-item label="任务名称" required>
          <el-input
            v-model="createTaskForm.name"
            placeholder="例如：2026年Q4绩效考核"
          />
        </el-form-item>
        <el-form-item label="考核周期" required>
          <el-input
            v-model="createTaskForm.period"
            placeholder="202611"
          />
          <div class="hint">
            ⚠️ 本入口**仅支持月度周期**（格式 <b>YYYYMM</b>，如 202611）。
            季度 / 年度考核请到
            <el-button link type="primary" @click="goSchemeManagement">
              方案管理 → 配置覆盖与发起考核
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="被评员工" required>
          <el-select
            v-model="createTaskForm.targetEmployeeIds"
            multiple
            filterable
            placeholder="选择被考核的员工"
            style="width: 100%"
          >
            <el-option
              v-for="emp in candidateEmployees"
              :key="emp.id"
              :label="`${emp.name}（${emp.departmentName || '未分部门'}）`"
              :value="emp.id"
            />
          </el-select>
          <div class="hint">
            ⚠️ 需要**方案覆盖**的员工请到
            <el-button link type="primary" @click="goSchemeManagement">
              方案管理 → 配置覆盖与发起考核
            </el-button>
            （那里会按方案版本自动分组冻结快照）。
          </div>
        </el-form-item>
        <el-form-item label="考核周期起止" required>
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
import { http } from '@/api/http'
import {
  computeStats,
  confirmTask as confirmTaskApi,
  createPerformanceTask as createPerformanceTaskApi,
  fetchDashboard,
  type PerformanceRecordRow,
  type PerformanceTaskRow,
} from '@/api/performanceDashboard'

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

/**
 * ⚠️⚠️ **2026-11-24：这个页面原来是纯假数据页**
 *
 * 之前这里是写死的数组：
 * ```ts
 * const tasks = ref<any[]>([{ id: 'T001', name: '2026年Q4绩效考核', ... }])
 * const recentRecords = ref<any[]>([{ id: 'R001', employeeName: '张三', ... }])
 * ```
 * 以及三处 `// 这里应该调用后端 API ...` 的 TODO
 * ⇒ **一个请求都没发**。
 *
 * ⚠️ **为什么这是最坏的一类缺陷**
 * 页面**看起来完全正常**：有数据、有按钮、有成功提示。
 * ⇒ 验收的人会以为「系统跑通了」，
 *    而实际数据库里什么都没变。
 * （同类问题在 `EmployeeSelfReview.vue` 出现过 —— BUG-R6-41，已修）
 *
 * ⇒ 现在真实调后端，且由 `frontend/tests/performance-dashboard-no-mock.test.ts`
 *   锁住「不许退回假数据」。
 */
const tasks = ref<PerformanceTaskRow[]>([])
const recentRecords = ref<PerformanceRecordRow[]>([])

const createTaskForm = reactive({
  name: '',
  /** ⚠️ **周期是紧凑格式** —— 后端权威格式是 `202611` / `2026Q4` / `2026` */
  period: '',
  /** ⚠️ **必须有被评员工** —— 后端 400「被评员工列表不能为空」 */
  targetEmployeeIds: [] as string[],
  dateRange: [] as string[],
  selfReviewDeadline: null as string | null,
  managerReviewDeadline: null as string | null,
})

/**
 * 可选员工列表。
 *
 * ⚠️ **数据来源** `/system/employees`（需管理员权限）
 * ⇒ 非 HR/管理员打开这个对话框时列表是空的，
 *    此时应引导到「方案管理」页（那里 HR 有 `/schemes/scheme-coverage`）。
 */
const candidateEmployees = ref<{
  id: string
  name: string
  departmentName?: string
}[]>([])

/** 跳到方案管理页（方案考核的正确入口）。 */
function goSchemeManagement() {
  showCreateTaskDialog.value = false
  router.push('/system/performance-schemes')
}

/**
 * 打开「发起考核任务」对话框，并加载可选员工。
 *
 * ⚠️ **员工列表来自 `/system/employees`** —— 需要管理员/HR 权限。
 * 非 HR 打开时列表会为空 ⇒ 引导到方案管理页。
 */
async function openCreateTask() {
  showCreateTaskDialog.value = true
  if (candidateEmployees.value.length) return
  try {
    // ⚠️ **响应形状**：`{records, total, pageNo, pageSize}`，
    //   员工名字段是 `realName`（不是 `name`），部门是 `departmentName`。
    const res = await http.get<unknown, {
      records?: { id: string; realName: string; departmentName?: string }[]
    }>('/system/employees?pageSize=200')
    candidateEmployees.value = (res?.records ?? []).map((e) => ({
      id: e.id,
      name: e.realName,
      departmentName: e.departmentName,
    }))
  } catch {
    // ⚠️ **静默降级** —— 没权限拿不到列表不该弹错误框打断流程，
    //    用户仍可去方案管理页发起。
    candidateEmployees.value = []
  }
}

// 过滤任务
const filteredTasks = computed(() => {
  if (!searchText.value) return tasks.value
  return tasks.value.filter(
    (task) =>
      task.name.includes(searchText.value) ||
      task.task_id.includes(searchText.value) ||
      task.period.includes(searchText.value)
  )
})

// 获取任务状态标签
/**
 * ⚠️ **状态值以 `AssessmentTask.status` 为准**
 * （`created` / `confirmed` / `archived` / `draft`）
 */
function getTaskStatusLabel(status: string): string {
  const statusMap: Record<string, string> = {
    draft: '草稿',
    created: '已创建',
    confirmed: '已定稿',
    archived: '已归档',
  }
  return statusMap[status] || status
}

// 获取任务状态类型
function getTaskStatusType(status: string): string {
  const typeMap: Record<string, string> = {
    draft: 'info',
    created: 'warning',
    confirmed: 'success',
    archived: 'success',
  }
  return typeMap[status] || 'info'
}

/**
 * 获取记录状态标签
 *
 * ⚠️ **口径与 `scheme_workflow.py` 的状态机完全一致**
 * —— 这套值同时用于列表和看板统计，不能各写一套。
 */
function getRecordStatusLabel(status: string): string {
  const statusMap: Record<string, string> = {
    not_started: '未开始',
    self_review_open: '填报中',
    self_review_submitted: '待主管评分',
    manager_review_submitted: '待主管面谈',
    confirmed: '已定稿',
    archived: '已归档',
  }
  return statusMap[status] || status
}

// 获取记录状态类型
function getRecordStatusType(status: string): string {
  const typeMap: Record<string, string> = {
    not_started: 'info',
    self_review_open: 'info',
    self_review_submitted: 'warning',
    manager_review_submitted: 'warning',
    confirmed: 'success',
    archived: 'success',
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
    // ⚠️ **真实后端数据**（2026-11-24 修）—— 之前这里是假数据。
    const data = await fetchDashboard()
    tasks.value = data.tasks
    recentRecords.value = data.records
    Object.assign(stats, computeStats(data.tasks, data.records))
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
  if (!createTaskForm.period) {
    ElMessage.warning('请输入考核周期（如 202611）')
    return
  }
  // ⚠️ **只支持月度周期** —— 后端 `scheme_service.resolve` 默认
  //   `period_type="monthly"`，且方案绑定是按 period_type 匹配的
  //   ⇒ 传 "2026Q4" 匹配不到任何已发布方案 ⇒ 409。
  //   ⇒ 前端先拦一道，给出可操作的提示而不是让后端报错。
  if (!/^\d{6}$/.test(createTaskForm.period)) {
    ElMessage.warning('考核周期格式应为 YYYYMM（如 202611）；季度/年度请到方案管理发起')
    return
  }
  if (!createTaskForm.targetEmployeeIds.length) {
    ElMessage.warning('请选择被评员工')
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
  // ⚠️ **必须自评早于主管评分** —— 后端会拒（400）
  if (createTaskForm.selfReviewDeadline >= createTaskForm.managerReviewDeadline) {
    ElMessage.warning('自评截止日期必须早于主管评分截止日期')
    return
  }

  creating.value = true
  try {
    // ⚠️ **真实后端调用**（2026-11-24 修）——
    //   之前这里只往本地数组 push 一条假数据，然后弹「考核任务已发起」，
    //   **数据库里什么都没变**（比不接还糟：它骗人）。
    const result = await createPerformanceTaskApi({
      title: createTaskForm.name,
      // ⚠️ **已经是 YYYYMM**（上面正则校验过），不要再清洗
      period: createTaskForm.period,
      target_employee_ids: createTaskForm.targetEmployeeIds,
      start_date: createTaskForm.dateRange[0],
      end_date: createTaskForm.dateRange[1],
      self_review_deadline: createTaskForm.selfReviewDeadline,
      manager_review_deadline: createTaskForm.managerReviewDeadline,
    })
    ElMessage.success(result?.message || `已为 ${result?.target_count ?? 0} 人发起考核`)
    showCreateTaskDialog.value = false

    // 重置表单
    createTaskForm.name = ''
    createTaskForm.period = ''
    createTaskForm.targetEmployeeIds = []
    createTaskForm.dateRange = []
    createTaskForm.selfReviewDeadline = null
    createTaskForm.managerReviewDeadline = null

    await loadDashboard()
  } catch (err) {
    // ⚠️ **失败必须让用户看到** —— 静默失败 = 「看起来成功了」
    ElMessage.error(err instanceof Error ? err.message : '发起任务失败')
  } finally {
    creating.value = false
  }
}

// 查看任务详情
function viewTaskDetails(taskId: string) {
  // ⚠️ **2026-11-24 修**：之前跳 `/performance/tasks/{id}`，
  //   而**这个路由根本不存在** ⇒ 点「查看详情」进空白页。
  //   任务详情在「按方案填报与评价」页里（列表可点进记录抽屉）。
  router.push('/performance/scheme-filling')
}

// 查看记录详情
function viewRecordDetails(recordId: string) {
  // ⚠️ **2026-11-24 修**：之前跳 `/performance/records/{id}`，该路由也不存在。
  //   记录详情统一在「按方案填报与评价」页的抽屉里。
  router.push('/performance/scheme-filling')
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
    // ⚠️ **真实后端调用**（2026-11-24 修）——
    //   之前只改本地 `task.status`，弹「成绩已确认」，数据库没变。
    const result = await confirmTaskApi(taskId)
    ElMessage.success(result?.message || '成绩已确认')
    await loadDashboard()
  } catch (err) {
    // ⚠️ **区分「用户取消」与「真的失败」** ——
    //   取消是 'cancel'，失败是 Error ⇒ 只后者要提示。
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '确认成绩失败')
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
    // ⚠️ **后端没有删除任务的端点**（2026-11-24 核对 `performance.py` 确认），
    //   过去这里只从前端数组里删掉一行，弹「任务已删除」——
    //   **数据库里任务还在** ⇒ 刷新页面又回来了。
    // ⇒ 现在**明确告知**，而不是假装删成功。
    ElMessage.warning('后端暂未开放删除考核任务的接口，任务未被删除。如需清理请在方案管理页停用对应方案。')
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '删除任务失败')
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
