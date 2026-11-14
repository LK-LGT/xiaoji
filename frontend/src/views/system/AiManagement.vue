<template>
  <section class="page-panel ai-admin-page" v-loading="loading && activeTab === 'configs'">
    <div class="page-header">
      <div>
        <span class="eyebrow">AI CONTROL CENTER</span>
        <h1 class="page-title">AI 控制中心</h1>
        <p class="page-subtitle">集中管理模型连接、灰度范围、运行日志和调用指标；日志仅展示已脱敏上下文和运行元数据。</p>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="ai-admin-tabs" @tab-change="handleTabChange">
      <el-tab-pane label="模型配置" name="configs">
        <div class="config-toolbar">
          <el-alert type="warning" :closable="false" show-icon title="任意时刻仅有一条模型配置启用，计划生成和 AI 检查会统一使用它；切换前请先测试连接。" />
          <el-button type="primary" :icon="Plus" @click="openCreate">新增配置</el-button>
        </div>

        <el-table :data="configs" border row-key="id" class="config-table">
          <el-table-column prop="configName" label="配置名称" min-width="170" />
          <el-table-column prop="providerCode" label="供应商" width="170" />
          <el-table-column prop="modelName" label="模型名称" min-width="170" />
          <el-table-column label="AI 总开关" width="110" align="center">
            <template #default="{ row }"><el-tag :type="row.globalEnabled ? 'success' : 'info'">{{ row.globalEnabled ? '开启' : '关闭' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="API Key" width="110" align="center">
            <template #default="{ row }"><el-tag :type="row.apiKeyConfigured ? 'success' : 'danger'">{{ row.apiKeyConfigured ? '已配置' : '未配置' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="启用状态" width="110" align="center">
            <template #default="{ row }"><el-tag :type="row.status === 'ENABLED' ? 'success' : 'info'">{{ row.status === 'ENABLED' ? '当前启用' : '未启用' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="版本" width="80" align="center"><template #default="{ row }">v{{ row.versionNo }}</template></el-table-column>
          <el-table-column label="操作" width="270" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" :icon="Edit" @click="openEdit(row)">编辑</el-button>
              <el-button link type="primary" :icon="Connection" :loading="testingId === row.id" @click="testConnection(row)">测试</el-button>
              <el-button v-if="row.status !== 'ENABLED'" link type="success" :icon="SwitchButton" :loading="enablingId === row.id" @click="enableConfig(row)">启用</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="运行日志" name="logs">
        <div class="ai-log-shortcuts" aria-label="快捷时间范围">
          <span>时间范围</span>
          <el-button-group>
            <el-button v-for="option in quickRanges" :key="option.value" :type="activeQuickRange === option.value ? 'primary' : 'default'" @click="applyQuickRange(option.value)">
              {{ option.label }}
            </el-button>
          </el-button-group>
        </div>

        <div class="filter-bar ai-log-filter">
          <el-date-picker v-model="logFilters.range" type="datetimerange" range-separator="至" start-placeholder="开始时间" end-placeholder="结束时间" value-format="YYYY-MM-DDTHH:mm:ss" @change="activeQuickRange = ''" />
          <el-select v-model="logFilters.sceneCode" clearable filterable placeholder="场景">
            <el-option v-for="option in sceneOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-select v-model="logFilters.success" clearable placeholder="结果">
            <el-option label="成功" value="true" />
            <el-option label="失败" value="false" />
          </el-select>
          <el-input-number v-model="logFilters.userId" :controls="false" :min="1" placeholder="用户 ID" />
          <el-input-number v-model="logFilters.orgId" :controls="false" :min="1" placeholder="组织 ID" />
          <el-input v-model="logFilters.requestId" clearable placeholder="Request ID" />
          <el-input v-model="logFilters.suggestionId" clearable placeholder="Suggestion ID" />
          <el-button type="primary" :icon="Search" @click="searchLogs">查询</el-button>
          <el-tooltip content="重置筛选" placement="top">
            <el-button :icon="RefreshLeft" aria-label="重置筛选" @click="resetLogFilters" />
          </el-tooltip>
        </div>

        <div class="table-card ai-log-table" v-loading="logLoading">
          <el-table :data="logPage.items" empty-text="暂无 AI 调用日志" @row-click="openLogDetail">
            <el-table-column label="时间" min-width="168">
              <template #default="{ row }">{{ formatTime(row.createdAt) }}</template>
            </el-table-column>
            <el-table-column label="场景" min-width="170">
              <template #default="{ row }"><strong>{{ formatScene(row.sceneCode) }}</strong></template>
            </el-table-column>
            <el-table-column label="结果" width="92" align="center">
              <template #default="{ row }"><el-tag :type="row.success ? 'success' : 'danger'">{{ row.success ? '成功' : '失败' }}</el-tag></template>
            </el-table-column>
            <el-table-column label="调用人" width="150">
              <template #default="{ row }"><strong>{{ formatActor(row).primary }}</strong><small v-if="formatActor(row).secondary">{{ formatActor(row).secondary }}</small></template>
            </el-table-column>
            <el-table-column label="业务对象" min-width="150">
              <template #default="{ row }">{{ formatBiz(row) }}</template>
            </el-table-column>
            <el-table-column label="模型" min-width="190">
              <template #default="{ row }"><strong>{{ row.modelName || '未记录' }}</strong><small>{{ formatProvider(row.providerCode) }} · {{ formatPromptVersion(row.promptVersion) }}</small></template>
            </el-table-column>
            <el-table-column label="用量" width="118" align="right">
              <template #default="{ row }"><span class="mono">{{ row.inputTokens + row.outputTokens }}</span><small>输入 {{ row.inputTokens }} / 输出 {{ row.outputTokens }}</small></template>
            </el-table-column>
            <el-table-column label="耗时" width="98" align="right">
              <template #default="{ row }"><span class="mono">{{ row.latencyMs }}ms</span></template>
            </el-table-column>
            <el-table-column label="错误" min-width="180">
              <template #default="{ row }">{{ row.errorCode || row.errorMessage || '无' }}</template>
            </el-table-column>
          </el-table>
        </div>
        <div class="pagination-row">
          <span>共 {{ logPage.total }} 条记录</span>
          <el-pagination v-model:current-page="logFilters.pageNo" :page-size="logFilters.pageSize" layout="prev, pager, next" :total="logPage.total" @current-change="loadLogs" />
        </div>
      </el-tab-pane>

      <el-tab-pane label="指标概览" name="metrics">
        <div class="metrics-toolbar">
          <el-radio-group v-model="metricsDays" @change="loadMetrics">
            <el-radio-button :value="7">近 7 天</el-radio-button>
            <el-radio-button :value="30">近 30 天</el-radio-button>
            <el-radio-button :value="90">近 90 天</el-radio-button>
          </el-radio-group>
          <el-button :icon="RefreshLeft" :loading="metricsLoading" @click="loadMetrics">刷新</el-button>
        </div>

        <div class="metric-grid ai-metric-grid" v-loading="metricsLoading">
          <div class="metric"><span>总调用</span><strong>{{ metricValue('totalCalls') }}</strong><em>成功 {{ metricValue('successCalls') }}</em></div>
          <div class="metric"><span>成功率</span><strong>{{ metricValue('successRate') }}%</strong><em>近 {{ metricsDays }} 天</em></div>
          <div class="metric"><span>Token</span><strong>{{ metricValue('inputTokens') + metricValue('outputTokens') }}</strong><em>入 {{ metricValue('inputTokens') }} / 出 {{ metricValue('outputTokens') }}</em></div>
          <div class="metric"><span>平均耗时</span><strong>{{ metricValue('averageLatencyMs') }}ms</strong><em>模型端响应</em></div>
          <div class="metric"><span>采纳建议</span><strong>{{ metricValue('adoptedSuggestions') }}</strong><em>采纳率 {{ metricValue('adoptionRate') }}%</em></div>
        </div>

        <div class="table-card ai-scene-metrics">
          <el-table :data="metrics?.byScene || []" empty-text="暂无场景调用数据">
            <el-table-column label="场景" min-width="220">
              <template #default="{ row }"><strong>{{ formatScene(String(row.sceneCode || '')) }}</strong><small>{{ row.sceneCode }}</small></template>
            </el-table-column>
            <el-table-column prop="calls" label="调用数" width="110" align="right" />
            <el-table-column prop="successes" label="成功数" width="110" align="right" />
            <el-table-column prop="tokens" label="Token" width="120" align="right" />
            <el-table-column prop="averageLatencyMs" label="平均耗时 ms" width="140" align="right" />
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="configDialog" :title="editingId === null ? '新增模型配置' : '编辑模型配置'" width="min(860px, 94vw)" destroy-on-close>
      <el-form label-position="top" class="ai-config-form">
        <div class="form-grid three-columns">
          <el-form-item label="配置名称" required><el-input v-model="configForm.configName" maxlength="120" /></el-form-item>
          <el-form-item label="供应商" required><el-select v-model="configForm.providerCode" @change="changeProvider"><el-option label="OpenAI 兼容" value="OPENAI_COMPATIBLE" /><el-option label="阿里云兼容" value="ALIYUN" /></el-select></el-form-item>
          <el-form-item label="模型名称" required><el-input v-model="configForm.modelName" maxlength="120" /></el-form-item>
          <el-form-item class="span-two" label="调用地址"><el-input v-model="configForm.baseUrl" placeholder="例如 http://服务器IP:11435/v1 或供应商兼容地址" /></el-form-item>
          <el-form-item label="超时秒数"><el-input-number v-model="configForm.timeoutSeconds" :min="60" :max="300" /></el-form-item>
          <el-form-item class="span-two" :label="apiKeyConfigured ? 'API Key（已配置，留空则保留）' : 'API Key'"><el-input v-model="apiKey" type="password" show-password autocomplete="new-password" /></el-form-item>
          <el-form-item label="AI 总开关"><el-switch v-model="configForm.globalEnabled" /></el-form-item>
        </div>
        <div class="switch-row"><el-checkbox v-model="configForm.draftEnabled">整表生成</el-checkbox><el-checkbox v-model="configForm.optimizeEnabled">单条优化</el-checkbox><el-checkbox v-model="configForm.checkEnabled">提交前检查</el-checkbox></div>
        <div class="form-grid three-columns">
          <el-form-item label="灰度用户 ID"><el-input v-model="configForm.allowedUserIds" placeholder="逗号分隔；留空不限制" /></el-form-item>
          <el-form-item label="灰度组织 ID"><el-input v-model="configForm.allowedOrgIds" placeholder="逗号分隔；留空不限制" /></el-form-item>
          <el-form-item label="整表生成/人/日"><el-input-number v-model="configForm.draftDailyLimit" :min="1" :max="1000" /></el-form-item>
          <el-form-item label="单条优化/人/日"><el-input-number v-model="configForm.optimizeDailyLimit" :min="1" :max="5000" /></el-form-item>
          <el-form-item label="提交检查/人/日"><el-input-number v-model="configForm.checkDailyLimit" :min="1" :max="5000" /></el-form-item>
          <el-form-item label="配置版本"><el-input :model-value="editingId === null ? '新配置' : `v${configForm.versionNo}`" disabled /></el-form-item>
        </div>
      </el-form>
      <template #footer><el-button @click="configDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveConfig">保存</el-button></template>
    </el-dialog>

    <el-drawer v-model="logDetailVisible" class="ai-log-detail-drawer" size="min(760px, 100%)" destroy-on-close>
      <template #header><div><span class="eyebrow">AI CALL DETAIL</span><h2>AI 调用详情</h2></div></template>
      <div v-loading="logDetailLoading">
        <template v-if="activeLog">
          <section class="ai-log-summary">
            <span>{{ formatScene(activeLog.sceneCode) }}</span>
            <h3>{{ activeLog.requestId }}</h3>
            <div>
              <el-tag :type="activeLog.success ? 'success' : 'danger'">{{ activeLog.success ? '成功' : '失败' }}</el-tag>
              <time>{{ formatTime(activeLog.createdAt) }}</time>
            </div>
          </section>

          <div class="ai-detail-section-heading">
            <div><span class="eyebrow">BUSINESS RESULT</span><h3>业务结果摘要</h3></div>
            <span>{{ activeLog.success ? '本次请求已完成' : '本次请求执行失败' }}</span>
          </div>
          <dl class="ai-log-metadata">
            <div><dt>建议 ID</dt><dd>{{ activeLog.suggestionId }}</dd></div>
            <div><dt>调用人</dt><dd>{{ formatActor(activeLog).primary }}<small v-if="formatActor(activeLog).secondary">{{ formatActor(activeLog).secondary }}</small></dd></div>
            <div><dt>业务对象</dt><dd>{{ formatBiz(activeLog) }}</dd></div>
            <div><dt>模型</dt><dd>{{ activeLog.modelName || '未记录' }}<small>{{ formatProvider(activeLog.providerCode) }} · {{ formatPromptVersion(activeLog.promptVersion) }}</small></dd></div>
            <div><dt>耗时与用量</dt><dd>{{ activeLog.latencyMs }}ms<small>输入 {{ activeLog.inputTokens }} / 输出 {{ activeLog.outputTokens }}</small></dd></div>
            <div><dt>执行阶段</dt><dd>{{ semanticTraceEvents.length }} 个业务节点<small>{{ repairAttemptCount ? `包含 ${repairAttemptCount} 次自动修复` : '未触发自动修复' }}</small></dd></div>
            <div><dt>用户反馈</dt><dd>{{ suggestionActionLabel(activeLog.suggestionAction) }}</dd></div>
          </dl>

          <section class="ai-trace-section">
            <div class="ai-trace-heading">
              <div><span class="eyebrow">CURRENT CALL</span><h3>当前调用链路</h3></div>
              <span v-if="semanticTraceEvents.length">{{ semanticTraceEvents.length }} 个业务节点</span>
            </div>
            <el-timeline v-if="semanticTraceEvents.length" class="ai-trace-timeline">
              <el-timeline-item
                v-for="event in semanticTraceEvents"
                :key="event.id"
                :type="formatTraceEvent(event).type"
                :timestamp="formatTraceTimestamp(event)"
              >
                <div class="ai-trace-title">
                  <strong>{{ event.label }}</strong>
                  <span v-if="event.attempt" class="ai-trace-attempt">第 {{ event.attempt }} 次尝试</span>
                  <el-tag size="small" effect="plain" :type="formatTraceEvent(event).type">{{ formatTraceEvent(event).statusLabel }}</el-tag>
                  <span>{{ formatTraceDuration(event) }}</span>
                </div>
                <p v-if="event.metadata.action || event.summary" class="ai-trace-summary">{{ event.metadata.action || event.summary }}</p>
                <div v-if="event.metadata.evidence?.length" class="ai-trace-facts">
                  <span>依据</span>
                  <ul><li v-for="(evidence, index) in event.metadata.evidence" :key="index">{{ semanticEvidenceText(evidence) || formatJson(evidence) }}</li></ul>
                </div>
                <div v-if="hasTraceMetadata(event.metadata.output)" class="ai-trace-facts"><span>输出</span><pre>{{ formatJson(event.metadata.output) }}</pre></div>
                <div v-if="hasTraceMetadata(event.metadata.impact)" class="ai-trace-facts"><span>影响</span><pre>{{ formatJson(event.metadata.impact) }}</pre></div>
                <p v-if="event.errorCode || event.errorMessage" class="ai-trace-error">
                  {{ [event.errorCode, event.errorMessage].filter(Boolean).join(' · ') }}
                </p>
              </el-timeline-item>
            </el-timeline>
            <el-empty v-else description="暂无执行追溯记录" :image-size="64" />
          </section>

          <section v-if="!activeLog.success" class="ai-log-block">
            <h3>错误信息</h3>
            <pre>{{ activeLog.errorCode || '未记录' }}{{ activeLog.errorMessage ? `\n${activeLog.errorMessage}` : '' }}</pre>
          </section>

          <el-collapse class="ai-technical-collapse">
            <el-collapse-item name="technical">
              <template #title><div class="ai-technical-title"><strong>技术详情</strong><span>Request ID、Token、原始事件与 JSON</span></div></template>
              <dl class="ai-log-metadata">
                <div><dt>Request ID</dt><dd>{{ activeLog.requestId }}</dd></div>
                <div><dt>输入哈希</dt><dd>{{ activeLog.inputHash }}</dd></div>
                <div><dt>场景编码</dt><dd>{{ activeLog.sceneCode }}</dd></div>
                <div><dt>业务编码</dt><dd>{{ activeLog.bizType || '未记录' }}<small>{{ activeLog.bizId ? `ID ${activeLog.bizId}` : '' }}</small></dd></div>
                <div><dt>缓存 Token</dt><dd>{{ cachedTokenLabel }}</dd></div>
                <div><dt>Prompt 版本</dt><dd>{{ formatPromptVersion(activeLog.promptVersion) }}</dd></div>
              </dl>
              <section v-if="technicalTraceEvents.length" class="ai-log-block">
                <h3>原始追踪事件</h3>
                <pre>{{ formatJson(technicalTraceEvents) }}</pre>
              </section>
              <AiInputSummary :value="activeLog.inputSummary" />
              <section class="ai-log-block"><h3>输出 JSON</h3><pre>{{ formatJson(activeLog.outputJson) }}</pre></section>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Edit, Plus, RefreshLeft, Search, SwitchButton } from '@element-plus/icons-vue'
import AiInputSummary from '@/components/AiInputSummary.vue'
import { isTechnicalEvent, normalizeTraceEvent, semanticEvidenceText } from '@/utils/aiTracePresentation'
import {
  createAiConfigApi,
  enableAiConfigApi,
  getAiCallLogDetailApi,
  getAiCallLogsApi,
  getAiMetricsApi,
  listAiConfigsApi,
  testAiConnectionApi,
  updateAiConfigApi,
  type AiCallLogDetail,
  type AiCallLogFilters,
  type AiCallLogPage,
  type AiCallLogRow,
  type AiTraceEvent,
  type AiMetrics,
  type AiModelConfig,
  type AiModelConfigPayload,
} from '@/api/aiManagement'

const defaultConfig = (): AiModelConfigPayload => ({ configName: '', providerCode: 'ALIYUN', baseUrl: '', modelName: '', timeoutSeconds: 120, globalEnabled: true, draftEnabled: true, optimizeEnabled: true, checkEnabled: true, allowedUserIds: '', allowedOrgIds: '', draftDailyLimit: 10, optimizeDailyLimit: 30, checkDailyLimit: 20, versionNo: 1 })
const loading = ref(false), saving = ref(false)
const testingId = ref<number | null>(null), enablingId = ref<number | null>(null), editingId = ref<number | null>(null)
const configDialog = ref(false), apiKeyConfigured = ref(false), apiKey = ref('')
const configs = ref<AiModelConfig[]>([])
const configForm = reactive<AiModelConfigPayload>(defaultConfig())
const activeTab = ref('configs')

const logLoading = ref(false), logDetailLoading = ref(false), logDetailVisible = ref(false)
const activeLog = ref<AiCallLogDetail>()
const normalizedTraceEvents = computed(() => (activeLog.value?.traceEvents || []).map(normalizeTraceEvent))
const semanticTraceEvents = computed(() => normalizedTraceEvents.value.filter((event) => !isTechnicalEvent(event.eventCode)))
const technicalTraceEvents = computed(() => normalizedTraceEvents.value.filter((event) => isTechnicalEvent(event.eventCode) || event.metadata.legacy))
const repairAttemptCount = computed(() => normalizedTraceEvents.value.filter((event) => event.eventCode === 'REPAIR_ATTEMPT').length)
const cachedTokenLabel = computed(() => {
  const attempts = normalizedTraceEvents.value.filter((event) => event.eventCode === 'MODEL_ATTEMPT')
  const values = attempts.map((event) => event.metadata.technical?.cachedInputTokens).filter((value) => typeof value === 'number') as number[]
  return values.length ? String(values.reduce((total, value) => total + value, 0)) : '供应商未提供'
})
const activeQuickRange = ref('7d')
const logPage = reactive<AiCallLogPage>({ items: [], total: 0, pageNo: 1, pageSize: 20 })
const logFilters = reactive<{ range: string[]; sceneCode: string; success: string; userId?: number; orgId?: number; requestId: string; suggestionId: string; pageNo: number; pageSize: number }>({
  range: rangeFor('7d'), sceneCode: '', success: '', userId: undefined, orgId: undefined, requestId: '', suggestionId: '', pageNo: 1, pageSize: 20,
})
const logsLoaded = ref(false)
const metricsLoading = ref(false)
const metricsDays = ref(30)
const metrics = ref<AiMetrics>()
const metricsLoaded = ref(false)
const quickRanges = [
  { label: '今天', value: 'today' },
  { label: '近 7 天', value: '7d' },
  { label: '近 30 天', value: '30d' },
  { label: '全部', value: 'all' },
]
const sceneOptions = [
  { label: '月计划整表生成', value: 'MONTH_PLAN_DRAFT' },
  { label: '月计划单条优化', value: 'MONTH_PLAN_ITEM_OPTIMIZE' },
  { label: '月计划提交检查', value: 'MONTH_PLAN_CHECK' },
  { label: '周计划生成', value: 'WEEK_PLAN_DRAFT' },
  { label: '周计划调整', value: 'WEEK_PLAN_ADJUST' },
  { label: '日计划生成', value: 'DAY_PLAN_DRAFT' },
  { label: '日计划调整', value: 'DAY_PLAN_ADJUST' },
  { label: 'AI 审核', value: 'AI_REVIEW' },
]
const traceLabels: Record<string, string> = {
  REQUEST_RECEIVED: '请求接收',
  MODE_DECISION: '模式决策',
  IDEMPOTENCY_HIT: '复用已有结果',
  CONTEXT_BUILD: '上下文组装',
  KNOWLEDGE_RETRIEVAL: '企业知识召回',
  PROMPT_PREPARE: 'Prompt 准备',
  MODEL_ATTEMPT: '模型调用',
  OUTPUT_VALIDATE: '输出校验',
  REPAIR_ATTEMPT: '自动修复',
  CALL_LOG_PERSIST: '调用日志保存',
  PERSIST_RESULT: '草稿版本保存',
  CONVERSATION_UPDATE: '会话结果保存',
  COMPLETED: '执行完成',
  FAILED: '执行失败',
  CANCELLED: '执行取消',
}
const traceStatuses = {
  RUNNING: { statusLabel: '进行中', type: 'primary' },
  SUCCESS: { statusLabel: '成功', type: 'success' },
  FAILED: { statusLabel: '失败', type: 'danger' },
  SKIPPED: { statusLabel: '已跳过', type: 'info' },
  CANCELLED: { statusLabel: '已取消', type: 'warning' },
} as const

async function load() {
  loading.value = true
  try {
    configs.value = await listAiConfigsApi()
  } catch (error) { showError(error, 'AI 配置加载失败') }
  finally { loading.value = false }
}
function openCreate() { editingId.value = null; apiKeyConfigured.value = false; apiKey.value = ''; Object.assign(configForm, defaultConfig()); configDialog.value = true }
function openEdit(row: AiModelConfig) {
  editingId.value = row.id; apiKeyConfigured.value = row.apiKeyConfigured; apiKey.value = ''
  Object.assign(configForm, { configName: row.configName, providerCode: row.providerCode, baseUrl: row.baseUrl || '', modelName: row.modelName, timeoutSeconds: row.timeoutSeconds, globalEnabled: row.globalEnabled, draftEnabled: row.draftEnabled, optimizeEnabled: row.optimizeEnabled, checkEnabled: row.checkEnabled, allowedUserIds: row.allowedUserIds || '', allowedOrgIds: row.allowedOrgIds || '', draftDailyLimit: row.draftDailyLimit, optimizeDailyLimit: row.optimizeDailyLimit, checkDailyLimit: row.checkDailyLimit, versionNo: row.versionNo })
  configDialog.value = true
}
function changeProvider() {
  apiKey.value = ''
  apiKeyConfigured.value = false
}
async function saveConfig() {
  if (!configForm.configName.trim() || !configForm.modelName.trim()) return ElMessage.warning('请填写配置名称和模型名称')
  if (!configForm.baseUrl.trim()) return ElMessage.warning('请填写调用地址')
  if (!apiKeyConfigured.value && !apiKey.value.trim()) return ElMessage.warning('请填写 API Key')
  saving.value = true
  try {
    const payload = { ...configForm, apiKey: apiKey.value || undefined }
    if (editingId.value === null) await createAiConfigApi(payload); else await updateAiConfigApi(editingId.value, payload)
    ElMessage.success(editingId.value === null ? '配置已新增' : '配置已保存'); configDialog.value = false; configs.value = await listAiConfigsApi()
  } catch (error) { showError(error, '保存失败') }
  finally { saving.value = false }
}
async function testConnection(row: AiModelConfig) {
  testingId.value = row.id
  try { const result = await testAiConnectionApi(row.id); ElMessage.success(`${result.message}，耗时 ${result.latencyMs}ms`) }
  catch (error) { showError(error, '连接失败') }
  finally { testingId.value = null }
}
async function enableConfig(row: AiModelConfig) {
  try { await ElMessageBox.confirm(`启用“${row.configName}”后，当前模型配置将自动停用。`, '确认切换模型', { type: 'warning', confirmButtonText: '确认启用' }) }
  catch { return }
  enablingId.value = row.id
  try { await enableAiConfigApi(row.id); ElMessage.success('当前启用配置已切换'); configs.value = await listAiConfigsApi() }
  catch (error) { showError(error, '启用失败') }
  finally { enablingId.value = null }
}

function handleTabChange(name: string | number) {
  if (name === 'logs' && !logsLoaded.value) void loadLogs()
  if (name === 'metrics' && !metricsLoaded.value) void loadMetrics()
}
function pad(value: number) { return String(value).padStart(2, '0') }
function localDateTime(value: Date) { return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}` }
function rangeFor(value: string) {
  if (value === 'all') return []
  const end = new Date()
  const start = new Date(end)
  start.setHours(0, 0, 0, 0)
  if (value !== 'today') start.setDate(start.getDate() - (value === '30d' ? 29 : 6))
  return [localDateTime(start), localDateTime(end)]
}
function applyQuickRange(value: string) {
  activeQuickRange.value = value
  logFilters.range = rangeFor(value)
  void searchLogs()
}
function logParams(): AiCallLogFilters {
  return {
    start: logFilters.range?.[0],
    end: logFilters.range?.[1],
    sceneCode: logFilters.sceneCode || undefined,
    success: logFilters.success === '' ? undefined : logFilters.success === 'true',
    userId: logFilters.userId,
    orgId: logFilters.orgId,
    requestId: logFilters.requestId || undefined,
    suggestionId: logFilters.suggestionId || undefined,
    pageNo: logFilters.pageNo,
    pageSize: logFilters.pageSize,
  }
}
async function loadLogs() {
  logLoading.value = true
  try {
    Object.assign(logPage, await getAiCallLogsApi(logParams()))
    logsLoaded.value = true
  } catch (error) { showError(error, 'AI 运行日志加载失败') }
  finally { logLoading.value = false }
}
function searchLogs() {
  logFilters.pageNo = 1
  void loadLogs()
}
function resetLogFilters() {
  activeQuickRange.value = '7d'
  logFilters.range = rangeFor('7d')
  logFilters.sceneCode = ''
  logFilters.success = ''
  logFilters.userId = undefined
  logFilters.orgId = undefined
  logFilters.requestId = ''
  logFilters.suggestionId = ''
  logFilters.pageNo = 1
  void loadLogs()
}
async function openLogDetail(row: AiCallLogRow) {
  logDetailVisible.value = true
  logDetailLoading.value = true
  activeLog.value = undefined
  try {
    activeLog.value = await getAiCallLogDetailApi(row.id)
  }
  catch (error) { showError(error, 'AI 调用详情加载失败') }
  finally { logDetailLoading.value = false }
}
async function loadMetrics() {
  metricsLoading.value = true
  try {
    metrics.value = await getAiMetricsApi(metricsDays.value)
    metricsLoaded.value = true
  } catch (error) { showError(error, 'AI 指标加载失败') }
  finally { metricsLoading.value = false }
}
function formatScene(sceneCode: string) {
  if (sceneCode === 'AI_REVIEW_WEEK_PLAN') return 'AI 审核周计划'
  if (sceneCode === 'AI_REVIEW_MONTH_PLAN') return 'AI 审核月计划'
  if (sceneCode === 'AI_REVIEW_DAY_PLAN') return 'AI 审核日计划'
  if (sceneCode.startsWith('AI_REVIEW_')) return `AI 审核${formatBusinessType(sceneCode.replace('AI_REVIEW_', ''))}`
  return sceneOptions.find((item) => item.value === sceneCode)?.label || sceneCode || '未记录'
}
function formatBusinessType(value?: string) {
  const labels: Record<string, string> = {
    MONTH_PLAN: '月计划',
    WEEK_PLAN: '周计划',
    DAY_PLAN: '日计划',
    RESULT: '成果',
    WEEK_RESULT: '周成果',
    MONTH_RESULT: '月成果',
  }
  return value ? labels[value] || value : '业务对象'
}
function formatBiz(row: Pick<AiCallLogRow, 'bizType' | 'bizId'>) {
  if (!row.bizType && !row.bizId) return '未关联业务'
  return `${formatBusinessType(row.bizType)}${row.bizId ? ` #${row.bizId}` : ''}`
}
function formatActor(row: Pick<AiCallLogRow, 'userId' | 'orgId'>) {
  return {
    primary: row.userId ? `用户 #${row.userId}` : '未记录用户',
    secondary: row.orgId ? `组织 #${row.orgId}` : '',
  }
}
function formatProvider(providerCode?: string) {
  if (providerCode === 'OPENAI_COMPATIBLE') return 'OpenAI 兼容'
  if (providerCode === 'ALIYUN') return '阿里云兼容'
  return providerCode || '未记录供应商'
}
function formatPromptVersion(version?: string) {
  if (!version) return '无 Prompt 版本'
  const match = version.match(/^v\d+/i)
  return match ? `Prompt ${match[0]}` : `Prompt ${version}`
}
function suggestionActionLabel(action?: string) {
  if (['APPLY_ALL', 'APPLY_ITEM', 'APPLY_FIELDS'].includes(action || '')) return '已采纳'
  if (action === 'ADOPT_WITH_EDIT') return '修改后采纳'
  if (action === 'IGNORE') return '已忽略'
  return '尚未反馈'
}
function formatTime(value?: string) { return value ? value.replace('T', ' ').slice(0, 19) : '未记录' }
function formatTraceEvent(event: { eventCode: string; status: AiTraceEvent['status'] }) {
  return {
    label: traceLabels[event.eventCode] || event.eventCode,
    ...(traceStatuses[event.status] || traceStatuses.RUNNING),
  }
}
function formatTraceTimestamp(event: { startedAt?: string; finishedAt?: string }) {
  const startedAt = formatTime(event.startedAt)
  return event.finishedAt ? `${startedAt} 至 ${formatTime(event.finishedAt)}` : startedAt
}
function formatTraceDuration(event: { status: AiTraceEvent['status']; finishedAt?: string; durationMs?: number }) {
  if (event.status === 'RUNNING' && !event.finishedAt) return '进行中'
  return `${Math.max(0, event.durationMs || 0)}ms`
}
function hasTraceMetadata(value: unknown) {
  if (value === undefined || value === null || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'object') return Object.keys(value).length > 0
  return true
}
function formatJson(value: unknown) {
  if (value === undefined || value === null || value === '') return '未记录'
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2) } catch { return value }
  }
  return JSON.stringify(value, null, 2)
}
function metricValue(key: keyof AiMetrics) {
  const value = metrics.value?.[key]
  return typeof value === 'number' ? value : 0
}
function showError(error: unknown, fallback: string) { ElMessage.error(error instanceof Error ? error.message : fallback) }
onMounted(load)
</script>

<style scoped>
.ai-admin-tabs :deep(.el-tabs__header) { margin-bottom: 18px; }
.config-toolbar { display:flex; align-items:center; gap:16px; margin:8px 0 18px; }
.config-toolbar .el-alert { flex:1; }
.config-table { width:100%; }
.ai-config-form { margin-top:4px; }
.switch-row { display:flex; gap:24px; padding:12px 0 22px; }
.ai-log-shortcuts, .metrics-toolbar { display:flex; align-items:center; justify-content:space-between; gap:16px; margin:4px 0 16px; }
.ai-log-shortcuts span { color:var(--muted); font-size:12px; font-weight:700; }
.ai-log-filter .el-input, .ai-log-filter .el-select, .ai-log-filter .el-input-number, .ai-log-filter .el-date-editor { width:190px; }
.ai-log-table strong, .ai-scene-metrics strong { display:block; color:var(--ink); font-size:13px; }
.ai-log-table small, .ai-scene-metrics small, .ai-log-metadata small { display:block; margin-top:4px; color:var(--muted); font-size:11px; }
.mono { font-family:"IBM Plex Mono", "Cascadia Mono", monospace; font-variant-numeric:tabular-nums; }
.ai-metric-grid { grid-template-columns:repeat(5, minmax(0, 1fr)); margin-bottom:16px; }
.ai-scene-metrics { margin-top:16px; }
.ai-log-summary { display:grid; gap:8px; padding-bottom:18px; border-bottom:1px solid var(--line); }
.ai-log-summary span { color:var(--blue); font-size:11px; font-weight:800; }
.ai-log-summary h3 { margin:0; overflow-wrap:anywhere; color:var(--ink); font-size:18px; letter-spacing:0; }
.ai-log-summary div { display:flex; flex-wrap:wrap; align-items:center; gap:10px; color:var(--muted); font-size:12px; }
.ai-detail-section-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; padding-top:18px; }
.ai-detail-section-heading h3, .ai-trace-heading h3 { margin:3px 0 0; color:var(--ink); font-size:14px; letter-spacing:0; }
.ai-detail-section-heading > span { color:var(--muted); font-size:12px; }
.ai-log-metadata { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:0 22px; margin:0; padding:16px 0; }
.ai-log-metadata div { min-width:0; padding:13px 0; border-bottom:1px solid #e7ebe5; }
.ai-log-metadata dt { color:var(--muted); font-size:12px; }
.ai-log-metadata dd { margin:6px 0 0; overflow-wrap:anywhere; color:var(--ink); font-size:13px; font-weight:650; }
.ai-trace-section { padding:18px 0 4px; border-top:1px solid var(--line); }
.ai-trace-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:18px; }
.ai-trace-heading span { color:var(--muted); font-size:12px; }
.ai-trace-timeline { margin:0; padding-left:4px; }
.ai-trace-timeline :deep(.el-timeline-item) { padding-bottom:20px; }
.ai-trace-timeline :deep(.el-timeline-item__timestamp) { color:var(--muted); font-size:11px; }
.ai-trace-title { display:flex; flex-wrap:wrap; align-items:center; gap:8px; min-height:24px; }
.ai-trace-title strong { color:var(--ink); font-size:13px; }
.ai-trace-title > span { margin-left:auto; color:var(--muted); font:11px/1.4 "IBM Plex Mono", "Cascadia Mono", monospace; }
.ai-trace-title .ai-trace-attempt { margin-left:0; padding:2px 6px; border:1px solid #cbd6d0; border-radius:4px; color:#4d655d; background:#f6f8f5; }
.ai-trace-summary { margin:6px 0 0; color:#41554e; font-size:12px; line-height:1.6; }
.ai-trace-facts { display:grid; grid-template-columns:44px minmax(0, 1fr); gap:8px; margin-top:8px; color:#40534c; font-size:11px; line-height:1.55; }
.ai-trace-facts > span { color:var(--muted); font-weight:700; }
.ai-trace-facts ul { margin:0; padding-left:17px; }
.ai-trace-facts li + li { margin-top:3px; }
.ai-trace-facts pre { max-height:150px; margin:0; overflow:auto; padding:8px 10px; border-left:2px solid #cbd6d0; background:#f6f7f1; font:11px/1.55 "IBM Plex Mono", "Cascadia Mono", monospace; white-space:pre-wrap; word-break:break-word; }
.ai-trace-metadata { max-height:180px; margin:8px 0 0; overflow:auto; padding:9px 11px; border-left:2px solid #cbd6d0; background:#f6f7f1; color:#40534c; font:11px/1.55 "IBM Plex Mono", "Cascadia Mono", monospace; white-space:pre-wrap; word-break:break-word; }
.ai-trace-error { margin:7px 0 0; color:#c2413b; font-size:12px; line-height:1.55; overflow-wrap:anywhere; }
.ai-trace-section :deep(.el-empty) { padding:14px 0 22px; }
.ai-trace-section :deep(.el-empty__description) { margin-top:4px; }
.ai-log-block { margin-top:16px; }
.ai-log-block h3 { margin:0 0 8px; color:var(--ink); font-size:14px; }
.ai-log-block pre { max-height:360px; margin:0; overflow:auto; padding:13px; border:1px solid var(--line); border-radius:8px; background:#f6f7f1; color:#273a34; font:12px/1.6 "IBM Plex Mono", "Cascadia Mono", monospace; white-space:pre-wrap; word-break:break-word; }
.ai-technical-collapse { margin-top:18px; border-top:1px solid var(--line); }
.ai-technical-collapse :deep(.el-collapse-item__header) { min-height:58px; height:auto; line-height:1.4; }
.ai-technical-title { display:flex; flex-direction:column; align-items:flex-start; gap:3px; }
.ai-technical-title strong { color:var(--ink); font-size:13px; }
.ai-technical-title span { color:var(--muted); font-size:11px; }
@media (max-width: 1180px) { .ai-metric-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) {
  .config-toolbar, .ai-log-shortcuts, .metrics-toolbar { align-items:stretch; flex-direction:column; }
  .ai-log-filter .el-input, .ai-log-filter .el-select, .ai-log-filter .el-input-number, .ai-log-filter .el-date-editor { width:100%; }
  .ai-log-metadata { grid-template-columns:1fr; }
  .ai-detail-section-heading { align-items:flex-start; flex-direction:column; }
}
</style>
