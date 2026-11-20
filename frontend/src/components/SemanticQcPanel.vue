/**
 * P2-1 评语语义质检 + 风险识别 + 面谈提纲面板。
 *
 * 用途：主管在**评分过程中**就能看到 AI 的质检结果与面谈提纲，
 * 而不是等评语写完、提交后再回来找。
 *
 * 三条硬约束（组件层面保证）：
 * 1. 每条问题/风险都显示 `basis`（判断依据）与 `ruleId` —— 没有依据的建议不展示；
 * 2. 免责提示默认折叠，但**始终在 DOM 中**；
 * 3. 组件**只读** —— 不提供任何「采纳并写回评语」的按钮。
 *    写回由人手工改评语后走既有提交路径，AI 不参与写回。
 */
<template>
  <section class="semantic-qc-panel" :class="`is-${tone}`">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">AI 建议</span>
        <h3 class="panel-title">评语质检与面谈提纲</h3>
        <p class="panel-subtitle">
          {{ summaryText }}
        </p>
      </div>
      <div class="head-actions">
        <el-tag v-if="result" :type="riskTagType" effect="dark" size="small">
          {{ riskLabel }}
        </el-tag>
        <el-button
          link
          type="primary"
          :aria-expanded="String(expanded)"
          @click="expanded = !expanded"
        >
          {{ expanded ? '收起' : '展开' }}
        </el-button>
      </div>
    </header>

    <!-- 免责提示：默认折叠，始终在 DOM -->
    <el-collapse v-model="collapseState" class="disclaimer">
      <el-collapse-item name="disclaimer" title="ⓘ 这只是 AI 建议，不是评价结论">
        <p class="disclaimer-text">
          {{ result?.disclaimer || defaultDisclaimer }}
        </p>
        <p class="disclaimer-text">
          {{ outline?.disclaimer || '' }}
        </p>
      </el-collapse-item>
    </el-collapse>

    <div v-show="expanded" class="panel-body">
      <el-alert
        v-if="error"
        type="error"
        :closable="false"
        show-icon
        :title="error"
      />

      <el-skeleton v-else-if="loading" :rows="5" animated />

      <el-empty
        v-else-if="!result"
        :image-size="80"
        description="暂无质检结果"
      />

      <template v-else>
        <!-- 语义问题 -->
        <div v-if="semanticIssues.length" class="section">
          <h4 class="section-title">
            评语语义问题
            <el-tag size="small" type="warning" effect="plain">{{ semanticIssues.length }} 项</el-tag>
          </h4>
          <article v-for="issue in semanticIssues" :key="issue.code" class="issue">
            <div class="issue-head">
              <el-tag :type="severityType(issue.severity)" size="small" effect="dark">
                {{ severityLabel(issue.severity) }}
              </el-tag>
              <strong>{{ issue.title }}</strong>
            </div>
            <p v-if="issue.quote" class="issue-quote">「{{ issue.quote }}」</p>
            <dl class="issue-meta">
              <div><dt>依据</dt><dd>{{ issue.basis }}</dd></div>
              <div><dt>规则</dt><dd>{{ issue.ruleId }}</dd></div>
              <div v-if="issue.suggestion"><dt>建议</dt><dd>{{ issue.suggestion }}</dd></div>
            </dl>
          </article>
        </div>

        <!-- 风险信号 -->
        <div v-if="risks.length" class="section">
          <h4 class="section-title">
            风险信号
            <el-tag size="small" type="danger" effect="plain">{{ risks.length }} 项</el-tag>
          </h4>
          <article v-for="risk in risks" :key="risk.code" class="issue">
            <div class="issue-head">
              <el-tag :type="severityType(risk.severity)" size="small" effect="dark">
                {{ severityLabel(risk.severity) }}
              </el-tag>
              <strong>{{ risk.title }}</strong>
            </div>
            <dl class="issue-meta">
              <div><dt>依据</dt><dd>{{ risk.basis }}</dd></div>
              <div v-if="risk.suggestion"><dt>建议</dt><dd>{{ risk.suggestion }}</dd></div>
            </dl>
          </article>
        </div>

        <!-- 面谈提纲 -->
        <div v-if="outline?.questions?.length" class="section">
          <h4 class="section-title">
            面谈提纲
            <el-tag size="small" effect="plain">{{ outline.questionCount }} 问</el-tag>
          </h4>
          <p class="section-note">{{ outline.summary }}</p>
          <article
            v-for="question in outline.questions"
            :key="question.id"
            class="question"
          >
            <div class="question-head">
              <el-tag :type="priorityType(question.priority)" size="small" effect="plain">
                {{ priorityLabel(question.priority) }}
              </el-tag>
              <span class="question-dimension">{{ question.dimension }}</span>
            </div>
            <p class="question-text">{{ question.question }}</p>
            <dl class="issue-meta">
              <div><dt>目的</dt><dd>{{ question.purpose }}</dd></div>
              <div v-if="question.followUp"><dt>追问</dt><dd>{{ question.followUp }}</dd></div>
            </dl>
          </article>
        </div>

        <el-empty
          v-if="!semanticIssues.length && !risks.length && !outline?.questions?.length"
          :image-size="80"
          description="未发现语义或风险信号，建议按常规面谈流程进行"
        />
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { semanticQcApi, type SemanticQcResult } from '@/api/p2'

const props = withDefaults(
  defineProps<{
    /** 考核记录 ID */
    recordId: string | null
    /** 是否自动加载 */
    autoLoad?: boolean
  }>(),
  { autoLoad: true },
)

const emit = defineEmits<{ (e: 'loaded', result: SemanticQcResult): void }>()

const loading = ref(false)
const error = ref('')
const expanded = ref(true)
const result = ref<SemanticQcResult | null>(null)
const collapseState = ref<string[]>([])

const defaultDisclaimer =
  'AI 仅提供统计性观察与建议，不构成评价结论；所有条目均需人工复核后方可使用。'

const semanticIssues = computed(() => result.value?.semanticIssues ?? [])
const risks = computed(() => result.value?.risks ?? [])
const outline = computed(() => result.value?.interviewOutline ?? null)

const summaryText = computed(() => {
  if (!result.value) return '加载中…'
  const issues = semanticIssues.value.length
  const risksCount = risks.value.length
  if (!issues && !risksCount) return '未发现语义或风险信号'
  return `发现 ${issues} 项评语语义问题、${risksCount} 项风险信号`
})

/** 整体风险 → 语气。 */
const tone = computed(() => {
  const level = result.value?.overallRisk
  if (level === 'HIGH') return 'danger'
  if (level === 'MEDIUM') return 'warning'
  return 'info'
})

const riskTagType = computed(() => {
  const level = result.value?.overallRisk
  if (level === 'HIGH') return 'danger'
  if (level === 'MEDIUM') return 'warning'
  return 'info'
})

const riskLabel = computed(() => {
  const map: Record<string, string> = { HIGH: '高风险', MEDIUM: '中风险', LOW: '低风险' }
  return map[result.value?.overallRisk ?? 'LOW'] ?? '低风险'
})

function severityType(severity: string): string {
  const map: Record<string, string> = {
    HIGH: 'danger',
    MEDIUM: 'warning',
    LOW: 'info',
    BLOCKING: 'danger',
  }
  return map[severity] ?? 'info'
}

function severityLabel(severity: string): string {
  const map: Record<string, string> = {
    HIGH: '高',
    MEDIUM: '中',
    LOW: '低',
    BLOCKING: '阻断',
  }
  return map[severity] ?? severity
}

function priorityType(priority: string): string {
  const map: Record<string, string> = { HIGH: 'danger', MEDIUM: 'warning', LOW: 'info' }
  return map[priority] ?? 'info'
}

function priorityLabel(priority: string): string {
  const map: Record<string, string> = { HIGH: '优先', MEDIUM: '一般', LOW: '可选' }
  return map[priority] ?? priority
}

async function load() {
  if (!props.recordId) {
    result.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    const data = await semanticQcApi.analyzeRecord(props.recordId)
    result.value = data
    emit('loaded', data)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '质检结果加载失败'
    result.value = null
  } finally {
    loading.value = false
  }
}

watch(() => props.recordId, load, { immediate: props.autoLoad })

defineExpose({ load })
</script>

<style scoped>
.semantic-qc-panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-left: 3px solid #409eff;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
}

.is-warning {
  border-left-color: #e6a23c;
}

.is-danger {
  border-left-color: #f56c6c;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 8px;
}

.head-text {
  flex: 1;
  min-width: 0;
}

.eyebrow {
  display: block;
  font-size: 11px;
  color: #909399;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 4px 0;
  color: #303133;
}

.panel-subtitle {
  font-size: 13px;
  color: #606266;
  margin: 0;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.disclaimer {
  margin-bottom: 12px;
}

.disclaimer :deep(.el-collapse-item__header) {
  font-size: 12px;
  color: #909399;
  height: 28px;
  line-height: 28px;
  background: #fafafa;
  border-radius: 4px;
  padding: 0 8px;
}

.disclaimer-text {
  font-size: 12px;
  color: #606266;
  margin: 0 0 4px 0;
  line-height: 1.7;
}

.section {
  margin-bottom: 16px;
}

.section:last-child {
  margin-bottom: 0;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 10px 0;
  color: #303133;
}

.section-note {
  font-size: 12px;
  color: #909399;
  margin: 0 0 10px 0;
}

.issue,
.question {
  border: 1px solid #ebeef5;
  border-radius: 5px;
  padding: 12px;
  margin-bottom: 10px;
  background: #fafafa;
}

.issue-head,
.question-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.issue-head strong {
  font-size: 13px;
  color: #303133;
}

.question-dimension {
  font-size: 12px;
  color: #909399;
}

.issue-quote {
  font-size: 13px;
  color: #606266;
  margin: 0 0 6px 0;
  padding: 4px 8px;
  background: #fef0f0;
  border-radius: 4px;
  display: inline-block;
}

.question-text {
  font-size: 13px;
  color: #303133;
  margin: 0 0 8px 0;
  line-height: 1.7;
}

.issue-meta {
  margin: 0;
  font-size: 12px;
}

.issue-meta > div {
  display: flex;
  gap: 8px;
  padding: 2px 0;
}

.issue-meta dt {
  color: #909399;
  flex-shrink: 0;
  width: 32px;
}

.issue-meta dd {
  margin: 0;
  color: #606266;
  line-height: 1.6;
}
</style>
