<template>
  <section class="version-history"><div v-for="version in versions" :key="version.resultId" class="version-item"><div><strong>{{ version.versionNo || 'V1' }}</strong><el-tag v-if="version.current" size="small" type="success" effect="plain">当前版本</el-tag><span v-else class="readonly-label">历史版本，只读</span></div><p>{{ version.submittedAt || '--' }} · {{ version.changeSummary || '提交成果' }}</p><small v-if="version.rejectionComment">退回原因：{{ version.rejectionComment }}</small><el-button v-if="!version.current" link type="primary" @click="$emit('compare', version.resultId)">比较相邻版本</el-button></div><div v-if="!versions.length" class="empty">暂无历史版本</div></section>
</template>
<script setup lang="ts">
// BUG-R6-14：成果 ID 是字符串主键（不是数字），故 props / emit 全部用 string。
defineProps<{ versions: Array<{ resultId: string; versionNo?: string; submittedAt?: string; changeSummary?: string; rejectionComment?: string; current?: boolean }> }>()
defineEmits<{ compare: [id: string] }>()
</script>
<style scoped>.version-history { width:100%; padding:26px 16px; box-sizing:border-box; }.version-item { padding:0 0 20px 18px; border-left:2px solid #d8e1dc; }.version-item + .version-item { padding-top:20px; }.version-item > div { display:flex; align-items:center; gap:8px; }.version-item p { margin:7px 0; color:#65756d; }.version-item small { color:#b64d45; }.readonly-label { color:#75847d; font-size:12px; }.empty { color:#75847d; }</style>
