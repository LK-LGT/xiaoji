/**
 * P2-3 反馈闭环 + Prompt 灰度面板。
 *
 * 两块内容：
 * 1. **人工反馈**：对 AI 草稿的采纳 / 修改 / 拒绝 —— 这是灰度对比的
 *    **唯一可信依据**（模型自评不算数）。
 * 2. **Prompt 灰度管理**（仅 HR）：版本配置、灰度比例、确定性分桶查询、
 *    以及基于人工反馈的版本对比。
 *
 * 两条硬约束：
 * 1. 反馈只落 `ai_feedback`，**不改 Score / confirmed_rating** ——
 *    组件文案里必须让人知道这一点，否则会以为「拒绝」等于「改分」。
 * 2. 灰度对比**样本不足时不下结论** —— 后端会返回 `sufficient: false`，
 *    前端必须如实显示「样本不足」，不能自己下判断。
 */
<template>
  <section class="feedback-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">反馈闭环</span>
        <h3 class="panel-title">{{ title }}</h3>
        <p class="panel-subtitle">你的采纳与修改决定后续 Prompt 版本的取舍</p>
      </div>
      <div class="head-actions">
        <el-button link type="primary" @click="loadStats">刷新</el-button>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="tabs">
      <!-- 提交反馈 -->
      <el-tab-pane label="提交反馈" name="submit">
        <div class="toolbar">
          <el-input
            v-model="recordId"
            placeholder="考核记录 ID"
            style="width: 220px"
            clearable
          />
          <el-select v-model="action" style="width: 160px">
            <el-option label="采纳" value="ACCEPT" />
            <el-option label="修改后采纳" value="MODIFY" />
            <el-option label="拒绝" value="REJECT" />
          </el-select>
          <el-input
            v-model="promptVersion"
            placeholder="Prompt 版本（可选）"
            style="width: 180px"
            clearable
          />
        </div>

        <el-form label-width="90px" class="feedback-form">
          <el-form-item label="修改摘要">
            <el-input
              v-model="diffSummary"
              type="textarea"
              :rows="2"
              placeholder="改了什么？（不写正文，只写差异摘要）"
            />
          </el-form-item>
          <el-form-item label="反馈理由">
            <el-input
              v-model="reason"
              type="textarea"
              :rows="2"
              placeholder="为什么采纳 / 修改 / 拒绝？"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submit">
              提交反馈
            </el-button>
          </el-form-item>
        </el-form>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="反馈不影响绩效结论"
          description="反馈只记录你对 AI 产出的评价，不会修改分数或已确认等级。要改结论请走调整单流程。"
        />
      </el-tab-pane>

      <!-- 反馈统计 -->
      <el-tab-pane label="反馈统计" name="stats">
        <el-skeleton v-if="statsLoading" :rows="3" animated />

        <template v-else-if="stats">
          <div class="stat-row">
            <span class="stat"><em>总反馈</em><strong>{{ stats.total }}</strong></span>
            <span class="stat"><em>采纳</em><strong>{{ stats.accept }}</strong></span>
            <span class="stat"><em>修改后采纳</em><strong>{{ stats.modify }}</strong></span>
            <span class="stat"><em>拒绝</em><strong>{{ stats.reject }}</strong></span>
            <span class="stat">
              <em>加权采纳率</em>
              <strong>{{ stats.acceptRate === null ? '—' : `${(stats.acceptRate * 100).toFixed(1)}%` }}</strong>
            </span>
          </div>
          <p class="note">加权采纳率 = (采纳 + 修改后采纳 × 0.5) / 总反馈</p>
        </template>

        <el-empty v-else :image-size="80" description="暂无反馈数据" />
      </el-tab-pane>

      <!-- Prompt 灰度（仅 HR） -->
      <el-tab-pane v-if="canManage" label="Prompt 灰度" name="prompt">
        <div class="prompt-layout">
          <!-- 版本列表 -->
          <div class="prompt-list">
            <h4 class="section-title">Prompt 版本</h4>
            <el-table :data="prompts" stripe size="small" style="width: 100%">
              <el-table-column prop="version" label="版本" width="100" />
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="rolloutType(row.rollout)" size="small" effect="dark">
                    {{ rolloutLabel(row.rollout) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="灰度比例" width="90">
                <template #default="{ row }">
                  {{ (row.canaryRatio * 100).toFixed(0) }}%
                </template>
              </el-table-column>
              <el-table-column prop="baselineVersion" label="基线版本" width="100" />
            </el-table>
          </div>

          <!-- 新增/编辑 -->
          <div class="prompt-form">
            <h4 class="section-title">新增 / 更新版本</h4>
            <el-form label-width="90px" size="small">
              <el-form-item label="Agent">
                <el-input v-model="form.agent" placeholder="comment_draft" />
              </el-form-item>
              <el-form-item label="版本号">
                <el-input v-model="form.version" placeholder="v2" />
              </el-form-item>
              <el-form-item label="灰度模式">
                <el-select v-model="form.rollout" style="width: 100%">
                  <el-option label="off（不启用）" value="off" />
                  <el-option label="canary（按比例灰度）" value="canary" />
                  <el-option label="full（全量）" value="full" />
                </el-select>
              </el-form-item>
              <el-form-item label="灰度比例">
                <el-slider
                  v-model="form.canaryRatio"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  :disabled="form.rollout !== 'canary'"
                  show-input
                />
              </el-form-item>
              <el-form-item label="基线版本">
                <el-input v-model="form.baselineVersion" placeholder="v1" />
              </el-form-item>
              <el-form-item label="Prompt">
                <el-input
                  v-model="form.promptText"
                  type="textarea"
                  :rows="4"
                  placeholder="Prompt 正文（不含任何凭据）"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="saving" @click="savePrompt">
                  保存
                </el-button>
              </el-form-item>
            </el-form>
          </div>
        </div>

        <!-- 灰度对比 -->
        <div class="compare-block">
          <h4 class="section-title">版本对比（依据人工反馈）</h4>

          <el-alert
            v-if="comparison && !comparison.sufficient"
            type="warning"
            :closable="false"
            show-icon
            :title="comparison.conclusion"
          />
          <el-alert
            v-else-if="comparison?.sufficient"
            type="success"
            :closable="false"
            show-icon
            :title="comparison.conclusion"
          />

          <el-table
            v-if="comparison?.versions?.length"
            :data="comparison.versions"
            stripe
            size="small"
            style="width: 100%; margin-top: 10px"
          >
            <el-table-column prop="version" label="版本" width="110" />
            <el-table-column prop="total" label="样本数" width="90" />
            <el-table-column prop="ACCEPT" label="采纳" width="80" />
            <el-table-column prop="MODIFY" label="修改" width="80" />
            <el-table-column prop="REJECT" label="拒绝" width="80" />
            <el-table-column label="加权采纳率" width="110">
              <template #default="{ row }">
                {{ row.acceptRate === null ? '—' : `${(row.acceptRate * 100).toFixed(1)}%` }}
              </template>
            </el-table-column>
          </el-table>

          <p v-if="comparison" class="note">{{ comparison.note }}</p>
        </div>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  feedbackApi,
  type FeedbackStats,
  type PromptVersionItem,
  type RolloutComparison,
} from '@/api/p2'

const props = withDefaults(
  defineProps<{
    title?: string
    /** 是否允许管理 Prompt（HR） */
    canManage?: boolean
    /** 预填记录 ID */
    recordId?: string
  }>(),
  { title: 'AI 反馈与灰度', canManage: false, recordId: '' },
)

const activeTab = ref('submit')

const recordId = ref(props.recordId)
const action = ref<'ACCEPT' | 'MODIFY' | 'REJECT'>('ACCEPT')
const promptVersion = ref('')
const diffSummary = ref('')
const reason = ref('')
const submitting = ref(false)

const stats = ref<FeedbackStats | null>(null)
const statsLoading = ref(false)

const prompts = ref<PromptVersionItem[]>([])
const comparison = ref<RolloutComparison | null>(null)
const saving = ref(false)

const form = reactive({
  agent: 'comment_draft',
  version: '',
  rollout: 'off' as 'off' | 'canary' | 'full',
  canaryRatio: 0.1,
  baselineVersion: '',
  promptText: '',
})

const agent = computed(() => form.agent || 'comment_draft')

function rolloutType(rollout: string): string {
  const map: Record<string, string> = { full: 'success', canary: 'warning', off: 'info' }
  return map[rollout] ?? 'info'
}

function rolloutLabel(rollout: string): string {
  const map: Record<string, string> = { full: '全量', canary: '灰度', off: '停用' }
  return map[rollout] ?? rollout
}

async function submit() {
  if (!recordId.value) {
    ElMessage.warning('请填写考核记录 ID')
    return
  }
  submitting.value = true
  try {
    await feedbackApi.submit({
      record_id: recordId.value,
      action: action.value,
      agent: agent.value,
      prompt_version: promptVersion.value || null,
      diff_summary: diffSummary.value || null,
      reason: reason.value || null,
    })
    ElMessage.success('反馈已记录')
    diffSummary.value = ''
    reason.value = ''
    await loadStats()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '反馈提交失败')
  } finally {
    submitting.value = false
  }
}

async function loadStats() {
  statsLoading.value = true
  try {
    const data = await feedbackApi.list(agent.value)
    stats.value = data.stats
  } catch {
    stats.value = null
  } finally {
    statsLoading.value = false
  }
}

async function loadPrompts() {
  if (!props.canManage) return
  try {
    prompts.value = await feedbackApi.listPrompts(agent.value)
    comparison.value = await feedbackApi.compare(agent.value)
  } catch {
    prompts.value = []
    comparison.value = null
  }
}

async function savePrompt() {
  if (!form.version || !form.promptText) {
    ElMessage.warning('版本号与 Prompt 正文不能为空')
    return
  }
  saving.value = true
  try {
    await feedbackApi.upsertPrompt({
      agent: form.agent,
      version: form.version,
      prompt_text: form.promptText,
      rollout: form.rollout,
      canary_ratio: form.canaryRatio,
      baseline_version: form.baselineVersion || null,
    })
    ElMessage.success('已保存')
    await loadPrompts()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void loadStats()
  void loadPrompts()
})
</script>

<style scoped>
.feedback-panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
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

.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.feedback-form {
  max-width: 620px;
  margin-bottom: 12px;
}

.stat-row {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat em {
  font-size: 11px;
  color: #909399;
  font-style: normal;
}

.stat strong {
  font-size: 18px;
  color: #303133;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 10px 0;
  color: #303133;
}

.prompt-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 20px;
}

@media (max-width: 1100px) {
  .prompt-layout {
    grid-template-columns: 1fr;
  }
}

.compare-block {
  padding-top: 16px;
  border-top: 1px solid #ebeef5;
}

.note {
  font-size: 12px;
  color: #909399;
  margin: 10px 0 0 0;
  line-height: 1.7;
}
</style>
