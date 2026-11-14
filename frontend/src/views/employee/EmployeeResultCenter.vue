<template>
  <main class="employee-result-center page-panel">
    <el-tabs
      :model-value="activeView"
      class="result-center-tabs"
      aria-label="成果管理视图"
      @tab-change="(value: string | number | boolean) => setView(value as ResultCenterView)"
    >
      <el-tab-pane label="本期成果" name="workbench" />
      <el-tab-pane label="成果档案" name="archive" />
    </el-tabs>

    <!-- 周期工作台是本期成果的实际工作视图。 -->
    <EmployeeResultWorkbench v-if="activeView === 'workbench'" />
    <EmployeeResultArchive v-else-if="activeView === 'archive'" />
  </main>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import EmployeeResultArchive from './EmployeeResultArchive.vue'
import EmployeeResultWorkbench from './components/EmployeeResultWorkbench.vue'

type ResultCenterView = 'workbench' | 'archive'

const route = useRoute()
const router = useRouter()
const activeView = computed<ResultCenterView>(() => route.query.view === 'archive' ? 'archive' : 'workbench')

function setView(view: ResultCenterView) {
  void router.replace({ query: { ...route.query, view } })
}
</script>

<style scoped>
.employee-result-center { width:100%; max-width:1580px; margin:0 auto; color:#263a34; }
.result-center-tabs { margin:0 -20px; padding:0 20px; }
.result-center-tabs :deep(.el-tabs__header) { margin:0; }
.result-center-tabs :deep(.el-tabs__item) { height:42px; color:#66766f; font-weight:650; }
.result-center-tabs :deep(.el-tabs__item.is-active) { color:var(--result-accent); }
.result-center-tabs :deep(.el-tabs__active-bar) { background:var(--result-accent); }
@media (max-width:600px) {
  .result-center-tabs { margin-inline:-18px; padding-inline:18px; }
}
</style>
