<template>
  <section class="page-panel performance-analytics">
    <div class="page-header">
      <div>
        <span class="eyebrow">PERFORMANCE ANALYTICS</span>
        <h1 class="page-title">绩效分析</h1>
        <p class="page-subtitle">
          薪酬建议导出与案例库。全部为统计描述与参考做法，不构成评价结论，也不替代任何审批流程。
        </p>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="analytics-tabs">
      <el-tab-pane label="薪酬建议导出" name="payroll">
        <PayrollExportPanel />
      </el-tab-pane>

      <el-tab-pane label="案例库" name="cases">
        <CaseLibraryPanel can-publish />
      </el-tab-pane>

      <el-tab-pane label="AI 反馈闭环" name="feedback">
        <FeedbackPanel can-manage title="人工反馈与灰度对比" />
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import PayrollExportPanel from '@/components/PayrollExportPanel.vue'
import CaseLibraryPanel from '@/components/CaseLibraryPanel.vue'
import FeedbackPanel from '@/components/FeedbackPanel.vue'

const activeTab = ref('payroll')
</script>

<style scoped>
.performance-analytics {
  padding: 20px;
}

.page-header {
  margin-bottom: 20px;
}

.eyebrow {
  display: block;
  font-size: 11px;
  color: #909399;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  margin-bottom: 6px;
}

.page-title {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 6px 0;
}

.page-subtitle {
  font-size: 13px;
  color: #606266;
  margin: 0;
  max-width: 720px;
  line-height: 1.7;
}
</style>
