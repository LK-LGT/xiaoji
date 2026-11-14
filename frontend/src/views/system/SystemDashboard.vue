<template>
  <section class="page-panel system-dashboard">
    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="8" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-empty :image-size="120" description="数据加载失败">
        <template #default>
          <div class="error-details">
            <h3>⚠️ 服务连接异常</h3>
            <p>{{ error }}</p>
            <el-button type="primary" @click="load">重新加载</el-button>
            <el-button @click="showDebugInfo">查看调试信息</el-button>
          </div>
        </template>
      </el-empty>
    </div>

    <!-- 正常状态 -->
    <div v-else>
      <div class="page-header">
        <div>
          <span class="eyebrow">SYSTEM ADMINISTRATION</span>
          <h1 class="page-title">系统管理工作台</h1>
          <p class="page-subtitle">检查组织、员工、角色和权限配置是否完整，并快速进入对应维护页面。</p>
        </div>
        <el-button @click="load" :loading="loading">刷新数据</el-button>
      </div>

      <div class="metric-grid system-metric-grid">
        <article class="metric primary">
          <span>组织节点</span>
          <strong>{{ data.departmentCount }}</strong>
          <em>部门、小组与项目组</em>
        </article>
        <article class="metric success">
          <span>启用员工</span>
          <strong>{{ data.employeeCount }}</strong>
          <em>可登录账号</em>
        </article>
        <article class="metric">
          <span>启用角色</span>
          <strong>{{ data.roleCount }}</strong>
          <em>当前权限集合</em>
        </article>
        <article class="metric warning">
          <span>近90天审计</span>
          <strong>{{ data.auditCount }}</strong>
          <em>关键操作记录</em>
        </article>
      </div>

      <div class="system-dashboard-grid">
        <section class="section-card">
          <div class="section-header">
            <div><h2>配置风险</h2><p>风险项可直接进入对应管理页面处理。</p></div>
          </div>
          <div v-if="data.risks.length" class="risk-list">
            <button v-for="risk in data.risks" :key="risk.code" type="button" class="risk-row" @click="router.push(risk.route)">
              <span :class="['risk-indicator', `is-${risk.level}`]"></span>
              <span><strong>{{ risk.title }}</strong><small>{{ risk.count ? `存在 ${risk.count} 项待处理` : '当前配置正常' }}</small></span>
              <b>{{ risk.count }}</b><em>查看 ›</em>
            </button>
          </div>
          <el-empty v-else :image-size="60" description="暂无风险提示" />
        </section>

        <section class="section-card">
          <div class="section-header">
            <div><h2>常用管理</h2><p>保持原型的直接入口，不在工作台执行批量变更。</p></div>
          </div>
          <div class="admin-entry-list">
            <button v-for="item in entries" :key="item.path" type="button" @click="router.push(item.path)">
              <span>{{ item.label }}</span><small>{{ item.desc }}</small><b>›</b>
            </button>
          </div>
        </section>
      </div>

      <section class="section-card mt16">
        <div class="section-header">
          <div><h2>最近审计记录</h2><p>系统管理、计划、成果和审批动作统一留痕。</p></div>
          <el-button text type="primary" @click="router.push('/system/audits')">查看全部</el-button>
        </div>
        <el-table :data="data.recentAudits" empty-text="暂无审计记录">
          <el-table-column prop="createdAt" label="时间" width="180" />
          <el-table-column label="发生的事情" min-width="300">
            <template #default="{ row }">
              <div class="audit-event-cell">
                <strong>{{ row.actionLabel || row.action }}</strong>
                <p>{{ row.targetSummary || row.targetType || '未指定对象' }}</p>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作人" width="160">
            <template #default="{ row }">
              <div class="audit-operator-cell">
                <strong>{{ row.operatorName || row.username || '系统' }}</strong>
                <small v-if="row.operatorName && row.username && row.operatorName !== row.username">{{ row.username }}</small>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="结果" width="100">
            <template #default="{ row }">
              <el-tag :type="row.result === 'SUCCESS' ? 'success' : 'danger'">
                {{ row.result === 'SUCCESS' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>

    <!-- 调试信息对话框 -->
    <el-dialog v-model="debugVisible" title="调试信息" width="600px">
      <div class="debug-info">
        <p><strong>后端地址:</strong> {{ backendUrl }}</p>
        <p><strong>API 端点:</strong> GET /system/dashboard</p>
        <p><strong>最后错误:</strong> {{ lastError || '无' }}</p>
        <p><strong>浏览器:</strong> {{ getBrowserUserAgent() }}</p>
        <pre>{{ debugJson }}</pre>
      </div>
      <template #footer>
        <el-button @click="debugVisible = false">关闭</el-button>
        <el-button @click="copyDebugInfo" type="primary">复制</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { getSystemDashboard, type DashboardData } from '@/api/system'

const router = useRouter()
const loading = ref(false)
const error = ref('')
const lastError = ref('')
const debugVisible = ref(false)
const backendUrl = ref((import.meta as any).env.VITE_API_BASE_URL || 'http://localhost:8000')

const data = reactive<DashboardData>({
  departmentCount: 0,
  employeeCount: 0,
  roleCount: 0,
  auditCount: 0,
  risks: [],
  recentAudits: [],
})

const entries = [
  { label: '员工注册', desc: '直接创建员工账号', path: '/system/employee-register' },
  { label: '员工管理', desc: '维护组织、负责人和状态', path: '/system/employees' },
  { label: '部门/项目组', desc: '维护组织树和负责人', path: '/system/orgs' },
  { label: '角色与权限', desc: '配置角色动作边界', path: '/system/roles' },
  { label: '工作日规则', desc: '维护日期填报口径', path: '/system/workday-rules' },
  { label: '审计日志', desc: '查询关键操作留痕', path: '/system/audits' },
  { label: '企业知识', desc: '查看发布依据与同步状态', path: '/system/knowledge' },
  { label: 'AI 配置', desc: '维护模型、提示词与调用指标', path: '/system/ai' },
]

const debugJson = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    console.log('📥 正在加载系统仪表盘数据...')
    const result = await getSystemDashboard()
    console.log('✅ 原始响应数据:', result)
    
    // 正确处理响应数据，保持响应式
    data.departmentCount = result.departmentCount || 0
    data.employeeCount = result.employeeCount || 0
    data.roleCount = result.roleCount || 0
    data.auditCount = result.auditCount || 0
    data.risks = result.risks || []
    data.recentAudits = result.recentAudits || []
    
    console.log('✅ 数据加载成功，已更新到响应式对象', data)
    ElMessage.success('数据已刷新')
  } catch (err) {
    lastError.value = err instanceof Error ? err.message : '未知错误'
    error.value = buildErrorMessage(err)
    console.error('❌ 加载失败:', err)
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

function buildErrorMessage(err: any): string {
  if (err instanceof Error) {
    if (err.message.includes('404')) {
      return '后端接口不存在 (404) - 请确保后端服务已启动并已部署此接口'
    }
    if (err.message.includes('401') || err.message.includes('403')) {
      return '认证失败 (401/403) - 请重新登录'
    }
    if (err.message.includes('ERR_NETWORK')) {
      return '网络连接失败 - 请检查后端服务是否已启动'
    }
    if (err.message.includes('CORS')) {
      return '跨域请求被阻止 - 请检查后端 CORS 配置'
    }
    return `加载失败: ${err.message}`
  }
  return '未知错误,请稍候重试'
}

function showDebugInfo() {
  debugJson.value = JSON.stringify(
    {
      apiBase: backendUrl.value,
      endpoint: '/system/dashboard',
      timestamp: new Date().toISOString(),
      userAgent: navigator.userAgent,
      lastError: lastError.value,
    },
    null,
    2
  )
  debugVisible.value = true
}

function copyDebugInfo() {
  navigator.clipboard.writeText(debugJson.value).then(() => {
    ElMessage.success('已复制到剪贴板')
  })
}

function getBrowserUserAgent() {
  return typeof navigator !== 'undefined' ? navigator.userAgent : 'Unknown'
}

onMounted(load)
</script>

<style scoped>
.loading-state {
  padding: 20px;
}

.error-state {
  padding: 60px 20px;
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

.error-details .el-button {
  margin-top: 15px;
  margin-right: 10px;
}

.debug-info {
  background: #f5f7fa;
  padding: 15px;
  border-radius: 4px;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  max-height: 400px;
  overflow: auto;
}

.debug-info p {
  margin: 8px 0;
  word-break: break-all;
}

.debug-info pre {
  background: #fff;
  padding: 10px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: auto;
}
</style>