<template>
  <div class="pan-verify">
    <div class="head">
      <h3>产出核对（网盘实测）</h3>
      <el-button link type="primary" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="warning" :closable="false" show-icon />

    <template v-else-if="data">
      <el-alert :title="data.notice" type="info" :closable="false" show-icon />

      <div v-if="data.snapshot" class="snapshot">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="统计窗口">
            {{ data.snapshot.window_start }} ~ {{ data.snapshot.window_end }}
          </el-descriptions-item>
          <el-descriptions-item label="窗口内成片总数">
            <strong>{{ data.snapshot.total }}</strong> 条
          </el-descriptions-item>
          <el-descriptions-item label="扫描路径">
            <span class="path">{{ data.snapshot.scanned_path }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="数据截至">
            {{ data.snapshot.as_of ? formatTime(data.snapshot.as_of) : '—' }}
          </el-descriptions-item>
        </el-descriptions>

        <details v-if="dailyEntries.length" class="daily">
          <summary>逐日计数（{{ dailyEntries.length }} 天）</summary>
          <div class="daily-grid">
            <div v-for="[day, count] in dailyEntries" :key="day" class="daily-cell">
              <span class="day">{{ day.slice(5) }}</span>
              <span class="count">{{ count }}</span>
            </div>
          </div>
        </details>
      </div>

      <el-empty v-else description="暂无网盘计数快照，请等待定时同步或联系管理员手动取数" :image-size="70" />

      <el-table v-if="data.items.length" :data="data.items" size="small" class="items">
        <el-table-column prop="metric_name" label="指标" min-width="160" />
        <el-table-column label="员工申报" width="100" align="right">
          <template #default="{ row }">{{ row.reported ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="网盘实测" width="100" align="right">
          <template #default="{ row }">{{ row.actual ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="核对结果" min-width="240">
          <template #default="{ row }">
            <el-tag :type="tagType(row.comparison.status)" size="small">
              {{ tagLabel(row.comparison.status) }}
            </el-tag>
            <span class="msg">{{ row.comparison.message }}</span>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <el-skeleton v-else :rows="2" animated />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { schemeVerification, type PanVerification } from '@/api/baiduPan'

const props = defineProps<{ recordId: string }>()

const data = ref<PanVerification | null>(null)
const loading = ref(false)
const error = ref('')

const dailyEntries = computed(() =>
  Object.entries(data.value?.snapshot?.daily_counts || {}).sort((a, b) => a[0].localeCompare(b[0]))
)

function tagType(status: string): 'success' | 'warning' | 'info' {
  if (status === 'match') return 'success'
  if (status === 'mismatch') return 'warning'
  return 'info'
}

function tagLabel(status: string): string {
  return (
    {
      match: '一致',
      mismatch: '不一致',
      no_actual: '无实测',
      no_reported: '未申报',
      no_data: '无数据',
    }[status] || status
  )
}

function formatTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 16)
}

async function load() {
  if (!props.recordId) return
  loading.value = true
  error.value = ''
  try {
    data.value = await schemeVerification(props.recordId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    ElMessage.warning('产出核对数据加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.pan-verify {
  margin: 16px 0;
  padding: 18px;
  border: 1px solid #dce4df;
  border-radius: 10px;
  background: #fbfdfc;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.head h3 {
  margin: 0;
  font-size: 15px;
}
.snapshot {
  margin-top: 14px;
}
.path {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: #61736b;
  word-break: break-all;
}
.daily {
  margin-top: 12px;
}
.daily summary {
  cursor: pointer;
  color: #61736b;
  font-size: 13px;
}
.daily-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
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
.items {
  margin-top: 14px;
}
.msg {
  margin-left: 8px;
  font-size: 12px;
  color: #61736b;
}
</style>
