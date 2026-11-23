<template>
  <section class="page-panel pan-page" v-loading="loading">
    <div class="page-header">
      <div>
        <span class="eyebrow">BAIDU PAN VERIFICATION</span>
        <h1 class="page-title">百度网盘产出校验</h1>
        <p class="page-subtitle">
          从百度网盘拉取成片文件数，核对员工申报的产出数量。
          <strong>核对结果仅供主管人工评定，不产生任何分数影响。</strong>
        </p>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :loading="loading" @click="refreshAll">刷新</el-button>
        <el-button
          :icon="RefreshRight"
          :loading="syncing"
          :disabled="!status?.authorized"
          @click="triggerSync"
        >
          立即取数
        </el-button>
        <el-button
          v-if="!status?.authorized"
          type="primary"
          :icon="Link"
          @click="authorizeDialog = true"
        >
          授权百度网盘
        </el-button>
      </div>
    </div>

    <!-- 状态告警 -->
    <el-alert
      v-if="status && !status.configured"
      type="warning"
      show-icon
      :closable="false"
      title="百度网盘凭证未配置"
      description="缺少 BAIDU_PAN_APP_KEY / BAIDU_PAN_APP_SECRET，请联系管理员在服务端环境变量中补齐后重启服务。"
      class="mt16"
    />
    <el-alert
      v-else-if="status && !status.authorized"
      type="warning"
      show-icon
      :closable="false"
      title="尚未授权百度网盘"
      description="点击「授权百度网盘」，在百度页面登录并勾选要校验的网盘目录。授权后取数将全自动运行。"
      class="mt16"
    />
    <el-alert
      v-else-if="status?.probe && !status.probe.ok"
      type="error"
      show-icon
      :closable="false"
      title="授权有效，但目录读不到"
      :description="`目录 ${status.probe.path} 读取失败：${status.probe.error || ''}。通常是因为授权时没有勾选该目录，请重新授权并勾选正确目录。`"
      class="mt16"
    />
    <el-alert
      v-else-if="status?.probe?.ok"
      type="success"
      show-icon
      :closable="false"
      title="目录可读"
      :description="`扫描路径 ${status.probe.path}，当前可见 ${status.probe.scanned} 个文件。`"
      class="mt16"
    />

    <div class="metric-grid mt16">
      <div class="metric" :class="status?.authorized ? 'success' : 'warning'">
        <span>授权状态</span>
        <strong>{{ status?.authorized ? '已授权' : '未授权' }}</strong>
        <small>{{ status?.authorized ? 'token 已自动续期' : '需要管理员完成一次授权' }}</small>
      </div>
      <div class="metric">
        <span>校验目录</span>
        <strong class="path">{{ status?.watchPath || '—' }}</strong>
        <small>按上传目录名解析业务日期</small>
      </div>
      <div class="metric">
        <span>上次窗口成片数</span>
        <strong>{{ latestCount?.total ?? '—' }}</strong>
        <small>
          {{ latestCount ? `${latestCount.windowStart} ~ ${latestCount.windowEnd}` : '暂无快照' }}
        </small>
      </div>
      <div class="metric">
        <span>定时取数</span>
        <strong>{{ scheduler?.running ? '运行中' : scheduler?.enabled ? '已启用' : '未启用' }}</strong>
        <small>{{ scheduler?.lastResult || '尚未执行' }}</small>
      </div>
    </div>

    <!-- 授权对话框 -->
    <el-dialog v-model="authorizeDialog" title="授权百度网盘" width="620px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="授权只需做一次"
        description="打开下方链接，用百度账号登录，并在授权页面**勾选要校验的网盘目录**。授权后系统自动取数、自动续期 token。"
      />
      <div v-if="authorizeUrl" class="authorize-box">
        <el-input :model-value="authorizeUrl" readonly>
          <template #append>
            <el-button @click="copyLink">复制</el-button>
          </template>
        </el-input>
        <p class="hint">勾选的目录会挂到应用目录下；系统按目录名（如 11月 / 11.19）解析业务日期。</p>
      </div>
      <div v-else class="authorize-box">
        <el-skeleton :rows="2" animated />
      </div>
      <el-form label-position="top" class="code-form">
        <el-form-item label="授权码（百度授权成功后页面显示的那串）">
          <el-input v-model="authCode" placeholder="粘贴授权码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="authorizeDialog = false">取消</el-button>
        <el-button type="primary" :loading="authorizing" :disabled="!authCode.trim()" @click="submitCode">
          提交授权码
        </el-button>
      </template>
    </el-dialog>

    <!-- 计数快照 -->
    <div class="section-card mt16">
      <div class="section-header">
        <div>
          <h2>计数快照</h2>
          <p class="section-desc">每次取数按统计窗口落一条快照；同一窗口重复取数会覆盖而非追加。</p>
        </div>
      </div>
      <el-table :data="counts" size="small" v-loading="loading">
        <el-table-column prop="windowStart" label="统计窗口" width="200" />
        <el-table-column label="成片总数" width="100" align="right">
          <template #default="{ row }"><strong>{{ row.total }}</strong></template>
        </el-table-column>
        <el-table-column label="扫到文件" width="100" align="right">
          <template #default="{ row }">{{ row.scannedFiles }}</template>
        </el-table-column>
        <el-table-column label="规则排除" width="100" align="right">
          <template #default="{ row }">{{ row.excludedFiles }}</template>
        </el-table-column>
        <el-table-column prop="scannedPath" label="扫描路径" min-width="160" show-overflow-tooltip />
        <el-table-column label="数据截至" width="150">
          <template #default="{ row }">{{ row.asOf ? formatTime(row.asOf) : '—' }}</template>
        </el-table-column>
        <el-table-column label="逐日" width="90">
          <template #default="{ row }">
            <el-popover trigger="click" placement="left" width="320">
              <template #reference>
                <el-button link type="primary">查看</el-button>
              </template>
              <div class="daily-grid">
                <div v-for="[day, count] in dailyOf(row)" :key="day" class="daily-cell">
                  <span class="day">{{ day.slice(5) }}</span>
                  <span class="count">{{ count }}</span>
                </div>
              </div>
            </el-popover>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !counts.length" description="暂无计数快照" :image-size="70" />
    </div>

    <!-- 同步批次 -->
    <div class="section-card mt16">
      <div class="section-header">
        <div>
          <h2>取数批次</h2>
          <p class="section-desc">每次取数的执行记录，用于排查「为什么没数到」。</p>
        </div>
      </div>
      <el-table :data="batches" size="small" v-loading="loading">
        <el-table-column label="触发" width="100">
          <template #default="{ row }">{{ row.triggerType === 'manual' ? '手动' : '定时' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="batchType(row.status)" size="small">{{ batchLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="扫到" width="90" align="right">
          <template #default="{ row }">{{ row.scannedFiles }}</template>
        </el-table-column>
        <el-table-column label="计入" width="90" align="right">
          <template #default="{ row }">{{ row.countedFiles }}</template>
        </el-table-column>
        <el-table-column prop="errorMessage" label="错误" min-width="200" show-overflow-tooltip />
        <el-table-column label="开始时间" width="150">
          <template #default="{ row }">{{ row.startedAt ? formatTime(row.startedAt) : '—' }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !batches.length" description="暂无批次记录" :image-size="70" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Link, Refresh, RefreshRight } from '@element-plus/icons-vue'
import {
  panAuthorize,
  panAuthorizeUrl,
  panBatches,
  panCounts,
  panStatus,
  panSync,
  panSyncStatus,
  type PanBatch,
  type PanCount,
  type PanStatus,
} from '@/api/baiduPan'

const loading = ref(false)
const syncing = ref(false)
const authorizing = ref(false)
const error = ref('')

const status = ref<PanStatus | null>(null)
const counts = ref<PanCount[]>([])
const batches = ref<PanBatch[]>([])
const scheduler = ref<{
  enabled: boolean
  running: boolean
  lastResult: string | null
  lastError: string
} | null>(null)

const authorizeDialog = ref(false)
const authorizeUrl = ref('')
const authCode = ref('')

const latestCount = computed(() => counts.value[0] || null)

function formatTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 16)
}

function batchType(status: string): 'success' | 'warning' | 'info' {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'warning'
  return 'info'
}

function batchLabel(status: string): string {
  return { succeeded: '成功', failed: '失败', running: '进行中' }[status] || status
}

function dailyOf(row: PanCount): [string, number][] {
  return Object.entries(row.dailyCounts || {}).sort((a, b) => a[0].localeCompare(b[0]))
}

async function refreshAll() {
  loading.value = true
  error.value = ''
  try {
    const [s, c, b, sc] = await Promise.all([
      panStatus(),
      panCounts(),
      panBatches(),
      panSyncStatus(),
    ])
    status.value = s
    counts.value = c || []
    batches.value = b || []
    scheduler.value = sc
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function loadAuthorizeUrl() {
  try {
    const res = await panAuthorizeUrl()
    authorizeUrl.value = res.authorizeUrl
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  }
}

async function openAuthorize() {
  authorizeDialog.value = true
  authCode.value = ''
  await loadAuthorizeUrl()
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText(authorizeUrl.value)
    ElMessage.success('授权链接已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选中复制')
  }
}

async function submitCode() {
  authorizing.value = true
  try {
    const res = await panAuthorize(authCode.value.trim())
    ElMessage.success('授权成功')
    authorizeDialog.value = false
    await refreshAll()
    if (res.probe && !res.probe.ok) {
      ElMessage.warning(`授权成功，但目录读不到：${res.probe.error || ''}`)
    } else if (res.probe?.ok) {
      ElMessage.success(`目录可读，当前可见 ${res.probe.scanned} 个文件`)
    }
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  } finally {
    authorizing.value = false
  }
}

async function triggerSync() {
  syncing.value = true
  try {
    await ElMessageBox.confirm(
      '将按默认窗口（上个自然月）取数。确定立即执行吗？',
      '确认取数',
      { type: 'warning' }
    )
    const batch = await panSync({})
    if (batch.status === 'succeeded') {
      ElMessage.success(`取数完成：扫到 ${batch.scannedFiles} 个文件，计入 ${batch.countedFiles} 条`)
    } else {
      ElMessage.error(`取数失败：${batch.errorMessage || '未知错误'}`)
    }
    await refreshAll()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error(e instanceof Error ? e.message : String(e))
    }
  } finally {
    syncing.value = false
  }
}

onMounted(() => {
  refreshAll()
  void openAuthorize()
})
</script>

<style scoped>
.pan-page {
  min-height: 100%;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}
.eyebrow {
  font-size: 11px;
  letter-spacing: 0.12em;
  color: #8a9a92;
}
.page-title {
  margin: 6px 0 8px;
  font-size: 22px;
}
.page-subtitle {
  margin: 0;
  max-width: 640px;
  color: #61736b;
  font-size: 13px;
  line-height: 1.7;
}
.toolbar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.mt16 {
  margin-top: 16px;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}
.metric {
  padding: 16px;
  border: 1px solid #dce4df;
  border-radius: 10px;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.metric span {
  font-size: 12px;
  color: #8a9a92;
}
.metric strong {
  font-size: 20px;
}
.metric small {
  font-size: 12px;
  color: #61736b;
}
.metric.success {
  border-color: #b7dcc6;
  background: #f4fbf6;
}
.metric.warning {
  border-color: #f0d9a8;
  background: #fdfaf3;
}
.metric .path {
  font-family: ui-monospace, monospace;
  font-size: 13px;
  word-break: break-all;
}
.section-card {
  padding: 18px;
  border: 1px solid #dce4df;
  border-radius: 10px;
  background: #fff;
}
.section-header {
  margin-bottom: 12px;
}
.section-header h2 {
  margin: 0 0 4px;
  font-size: 16px;
}
.section-desc {
  margin: 0;
  font-size: 12px;
  color: #8a9a92;
}
.authorize-box {
  margin: 16px 0;
}
.authorize-box .hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: #8a9a92;
}
.code-form {
  margin-top: 8px;
}
.daily-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}
.daily-cell {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid #e3eae6;
  border-radius: 6px;
  background: #fff;
}
.daily-cell .day {
  font-size: 12px;
  color: #8a9a92;
}
.daily-cell .count {
  font-weight: 600;
}
</style>
