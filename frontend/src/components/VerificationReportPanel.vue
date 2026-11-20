/**
 * P0-3 智能核验报告（Verification Report）展示组件。
 *
 * 用途：把申诉单上的**中立核验报告**从原始 JSON 变成可读材料：
 * 确认事实、差异、待核实事项、评分历史、局限性。
 *
 * ⚠️⚠️ 三条硬约束（这是本组件存在的全部意义）：
 * 1. **不作裁决** —— 组件**不显示**"申诉成立 / 不成立 / 应予调整"这类结论，
 *    也不提供任何改分按钮。报告是证据材料，裁决由授权人员另行作出。
 * 2. **局限性必须可见** —— 后端 `limitations` 原样透出，
 *    不能因为"报告已生成"就让人以为核查已完备。
 * 3. **历史只作线索** —— 评分历史与差异都以"核查线索"措辞呈现，
 *    不暗示"历史分变了就是错"。
 */
<template>
  <section class="verification-report">
    <el-empty
      v-if="!reports.length"
      :image-size="80"
      description="尚未生成核验报告"
    />

    <article
      v-for="r in reports"
      :key="r.id"
      class="report-card"
    >
      <header class="report-head">
        <div>
          <h4 class="report-version">报告版本 {{ r.version }}</h4>
          <p class="report-status">{{ r.content?.ai_status }}</p>
        </div>
        <el-tag size="small" effect="plain">{{ r.content?.engine }}</el-tag>
      </header>

      <!-- 局限性：始终展示在最前面 -->
      <el-alert
        v-if="r.content?.limitations?.length"
        type="warning"
        :closable="false"
        show-icon
        class="limitation-alert"
      >
        <template #title>报告局限</template>
        <ul class="limitation-list">
          <li v-for="(text, i) in r.content.limitations" :key="i">{{ text }}</li>
        </ul>
      </el-alert>

      <!-- 确认事实 -->
      <div v-if="r.content?.facts?.length" class="block">
        <h5 class="block-title">确认事实</h5>
        <ul class="fact-list">
          <li v-for="(fact, i) in r.content.facts" :key="i">
            <strong>{{ factTypeLabel(fact.type) }}</strong>
            <span v-if="fact.source" class="fact-meta">来源 {{ fact.source }}</span>
            <span v-if="fact.rule_version" class="fact-meta">
              规则版本 {{ fact.rule_version }}
            </span>
            <span v-if="fact.original !== undefined && fact.original !== null" class="fact-meta">
              冻结总分 {{ fact.original }}
            </span>
            <span
              v-if="fact.recomputed !== undefined && fact.recomputed !== null"
              class="fact-meta"
            >
              规则复算 {{ fact.recomputed }}
            </span>
          </li>
        </ul>
      </div>

      <!-- 差异 -->
      <div v-if="r.content?.differences?.length" class="block">
        <h5 class="block-title">差异</h5>
        <div v-for="(diff, i) in r.content.differences" :key="i" class="diff-item">
          <el-tag type="warning" size="small" effect="plain">
            {{ diffTypeLabel(diff.type) }}
          </el-tag>
          <span class="diff-delta">
            差值 {{ diff.delta > 0 ? '+' : '' }}{{ diff.delta }}
          </span>
        </div>
      </div>

      <!-- 缺失与待核实 -->
      <div v-if="r.content?.missing?.length" class="block">
        <h5 class="block-title">缺失与待核实</h5>
        <ul class="missing-list">
          <li v-for="(text, i) in r.content.missing" :key="i">{{ text }}</li>
        </ul>
      </div>

      <!-- 评分历史（线索） -->
      <div v-if="r.content?.history?.length" class="block">
        <h5 class="block-title">
          评分历史
          <span class="block-hint">（仅作核查线索，不构成评分错误结论）</span>
        </h5>
        <el-table :data="r.content.history" size="small">
          <el-table-column label="版本" width="80">
            <template #default="{ row }">v{{ row.version }}</template>
          </el-table-column>
          <el-table-column label="总分" width="90">
            <template #default="{ row }">{{ row.total }}</template>
          </el-table-column>
          <el-table-column label="来源" min-width="140">
            <template #default="{ row }">
              <span class="muted">{{ row.source }}</span>
            </template>
          </el-table-column>
          <el-table-column label="生成时间" min-width="160">
            <template #default="{ row }">
              <span class="muted">{{ formatTime(row.created_at) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 快照指纹：让用户能确认看的是同一份冻结数据 -->
      <p v-if="r.content?.snapshot_hash" class="snapshot-hash">
        冻结快照指纹：<code>{{ r.content.snapshot_hash }}</code>
      </p>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { VerificationReportRow } from '@/api/p0p1'

const props = withDefaults(
  defineProps<{
    /** 核验报告列表（来自案件的 `reports` 字段） */
    reports?: VerificationReportRow[]
  }>(),
  { reports: () => [] },
)

const reports = computed(() => props.reports ?? [])

function factTypeLabel(type: string): string {
  const map: Record<string, string> = {
    RULE_RECALCULATION: '规则复算比对',
    METRIC_TRACE: '指标溯源',
    EVIDENCE: '证据核对',
    SCORE_HISTORY: '评分历史',
  }
  return map[type] ?? type
}

function diffTypeLabel(type: string): string {
  const map: Record<string, string> = {
    TOTAL_DIFFERENCE: '总分差异',
    METRIC_DIFFERENCE: '指标差异',
    GRADE_DIFFERENCE: '等级差异',
  }
  return map[type] ?? type
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}
</script>

<style scoped>
.verification-report {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.report-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 14px 16px;
}
.report-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.report-version {
  margin: 0;
  font-size: 14px;
}
.report-status {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.limitation-alert {
  margin-bottom: 12px;
}
.limitation-list {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.block {
  margin-bottom: 14px;
}
.block-title {
  margin: 0 0 6px;
  font-size: 13px;
}
.block-hint {
  font-size: 12px;
  font-weight: normal;
  color: var(--el-text-color-secondary);
}
.fact-list,
.missing-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
}
.fact-list li {
  margin-bottom: 4px;
}
.fact-meta {
  margin-left: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.diff-item {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.diff-delta {
  font-size: 13px;
}
.snapshot-hash {
  margin: 0;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  word-break: break-all;
}
.muted {
  color: var(--el-text-color-secondary);
}
</style>
