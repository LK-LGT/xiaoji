<template>
  <section class="ai-result-card" :class="{ 'is-stale': stale }" aria-label="AI 月计划建议">
    <header>
      <div>
        <span>{{ operationLabel }}</span>
        <strong>{{ suggestion.items.length }} 项计划建议</strong>
      </div>
      <el-tag v-if="stale" size="small" type="warning" effect="plain">计划已更新，仍可应用</el-tag>
      <el-tag v-else-if="suggestion.draftVersionNo" size="small" effect="plain">V{{ suggestion.draftVersionNo }}</el-tag>
    </header>

    <div class="result-metrics">
      <span><strong>{{ suggestion.items.length }}</strong> 项任务</span>
      <span><strong>{{ issueCount }}</strong> 条提醒</span>
      <span><strong>{{ suggestion.knowledgeReferences?.length || 0 }}</strong> 份知识</span>
    </div>

    <p v-if="stale">当前计划已有更新，本条建议仍可查看、调整或再次应用。</p>
    <p v-else-if="suggestion.warnings?.length">{{ suggestion.warnings[0] }}</p>

    <details v-if="suggestion.knowledgeReferences?.length" class="knowledge-sources">
      <summary>查看本次召回的企业知识</summary>
      <ul>
        <li v-for="item in suggestion.knowledgeReferences" :key="`${item.knowledgeId}@${item.version}`">
          <span>{{ item.title || item.knowledgeId }}</span><small>{{ item.knowledgeId }}<template v-if="item.version"> · v{{ item.version }}</template></small>
        </li>
      </ul>
    </details>

    <footer>
      <el-button :icon="ChatLineRound" @click="emit('continue')">继续调整</el-button>
      <el-button type="primary" :icon="View" @click="emit('preview')">查看并应用</el-button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ChatLineRound, View } from '@element-plus/icons-vue'
import type { AiGenerateResponse } from '@/api/employeeAi'

const props = withDefaults(defineProps<{
  suggestion: AiGenerateResponse
  stale?: boolean
}>(), { stale: false })

const emit = defineEmits<{
  preview: []
  continue: []
}>()

const issueCount = computed(() => (props.suggestion.warnings?.length || 0) + (props.suggestion.missingContext?.length || 0))
const operationLabel = computed(() => ({
  GENERATE: '完整月计划',
  SUPPLEMENT: '补充任务',
  OPTIMIZE: '计划优化',
  REVISE_REJECTION: '审批反馈修改',
  CLARIFY: '需要确认',
})[props.suggestion.effectiveOperationMode] || '月计划建议')
</script>

<style scoped>
.ai-result-card { display:grid; gap:13px; max-width:620px; margin-top:12px; padding:15px; border:1px solid #cddbd5; border-radius:8px; background:#f8fbf9; }
.ai-result-card.is-stale { border-color:#e4d2b7; background:#fffaf1; }
.ai-result-card header,.ai-result-card footer,.result-metrics { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.ai-result-card header > div { display:grid; gap:3px; }
.ai-result-card header span { color:#60736d; font-size:11px; }
.ai-result-card header strong { color:#203a35; font-size:14px; }
.result-metrics { justify-content:flex-start; gap:24px; color:#667873; font-size:12px; }
.result-metrics strong { color:#2d776c; font-size:16px; }
.ai-result-card p { margin:0; color:#745d3e; font-size:12px; line-height:1.6; }
.knowledge-sources { color:#40544e; font-size:11px; }
.knowledge-sources summary { cursor:pointer; }
.knowledge-sources ul { display:grid; gap:6px; max-height:120px; margin:8px 0 0; padding:0; overflow:auto; list-style:none; }
.knowledge-sources li { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:6px 8px; background:#eef4f1; }
.knowledge-sources li span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.knowledge-sources li small { flex:none; color:#71817c; }
.ai-result-card footer { justify-content:flex-end; padding-top:2px; }
@media (max-width:600px) { .ai-result-card footer { align-items:stretch; flex-direction:column-reverse; }.ai-result-card footer .el-button { width:100%; margin:0; } }
</style>
