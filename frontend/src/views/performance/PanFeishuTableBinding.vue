<template>
  <section class="feishu-table-binding">
    <h4 class="section-title">
      飞书表格填报
      <span class="section-hint">在飞书里建表 → 把链接粘过来 → 在表里逐行填写</span>
    </h4>

    <el-alert type="info" :closable="false" class="hint-alert">
      <template #title>
        为什么不是在本页面填表格？
      </template>
      <div class="alert-body">
        飞书交互卡片的表格元素**不能放输入框**（官方限制），
        所以「以表格形式填报」只能落在<strong>飞书原生表格</strong>上 ——
        你在飞书表格里填完，系统定时自动读回来。
      </div>
    </el-alert>

    <!-- ① 我的绑定 -->
    <el-table :data="sources" size="small" border stripe v-loading="loading" class="bind-table">
      <el-table-column prop="title" label="表格" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          <a v-if="row.sourceUrl" :href="row.sourceUrl" target="_blank" rel="noopener">
            {{ row.title || row.objToken }}
          </a>
          <span v-else>{{ row.title || row.objToken }}</span>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">
            {{ row.tableKind === 'bitable' ? '多维表格' : '电子表格' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="上次同步" width="160">
        <template #default="{ row }">
          <span v-if="row.lastSyncedAt">{{ formatTime(row.lastSyncedAt) }}</span>
          <span v-else class="muted">未同步</span>
        </template>
      </el-table-column>
      <el-table-column label="行数" width="70" align="right">
        <template #default="{ row }">{{ row.lastRowCount }}</template>
      </el-table-column>
      <el-table-column label="状态" min-width="160">
        <template #default="{ row }">
          <el-tag v-if="row.lastError" type="danger" size="small" effect="plain">
            失败：{{ row.lastError }}
          </el-tag>
          <el-tag v-else-if="row.lastSyncedAt" type="success" size="small" effect="plain">正常</el-tag>
          <el-tag v-else type="info" size="small" effect="plain">待同步</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="syncOne(row)">立即同步</el-button>
          <el-button link type="danger" size="small" @click="unbind(row)">解绑</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <span class="muted">还没绑定表格 —— 见下方「三步开始」</span>
      </template>
    </el-table>

    <!-- ② 绑定 / 换表 -->
    <el-form :inline="true" :model="bindForm" class="bind-form">
      <el-form-item label="表格链接">
        <el-input
          v-model="bindForm.source_url"
          style="width: 420px"
          placeholder="粘贴飞书表格链接，如 https://xxx.feishu.cn/sheets/xxxx"
        />
      </el-form-item>
      <el-form-item label="类型">
        <el-select v-model="bindForm.table_kind" style="width: 130px">
          <el-option label="电子表格" value="sheet" />
          <el-option label="多维表格" value="bitable" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="binding" @click="bind">绑定</el-button>
      </el-form-item>
    </el-form>

    <!-- ③ 三步开始 -->
    <el-collapse v-model="guideOpen" class="guide">
      <el-collapse-item name="guide" title="三步开始（第一次用看这里）">
        <ol class="guide-steps">
          <li>
            在飞书里新建一个<strong>电子表格</strong>，第一行照抄下面这个表头：
            <div class="template-header">
              <el-tag v-for="h in template.header" :key="h" size="small" effect="plain">{{ h }}</el-tag>
            </div>
          </li>
          <li>
            把表格<strong>共享给应用</strong>（在飞书里把表格权限加给本应用），
            否则系统读不到。
          </li>
          <li>
            把表格链接粘到上面，<strong>点「绑定」</strong>；然后直接在飞书表里逐行填写，
            系统定时自动读回来。
          </li>
        </ol>

        <el-descriptions :column="1" border size="small" class="rules">
          <el-descriptions-item label="填写规则">
            <ul class="rule-list">
              <li v-for="(r, i) in template.rules" :key="i">{{ r }}</li>
            </ul>
          </el-descriptions-item>
          <el-descriptions-item label="表头别名">
            <div v-for="(aliases, key) in template.aliasTable" :key="key" class="alias-row">
              <strong>{{ key }}</strong>
              <span class="muted">：{{ aliases.join(' / ') }}</span>
            </div>
          </el-descriptions-item>
          <el-descriptions-item label="示例">
            <el-table :data="template.example" size="small" border>
              <el-table-column
                v-for="(h, i) in template.header"
                :key="i"
                :prop="String(i)"
                :label="h"
              />
            </el-table>
          </el-descriptions-item>
        </el-descriptions>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  feishuTableApi,
  type PanFeishuMetricSource,
  type PanFeishuTableTemplate,
} from '@/api/panWorkload'

const loading = ref(false)
const binding = ref(false)
const sources = ref<PanFeishuMetricSource[]>([])
const guideOpen = ref<string[]>([])

const bindForm = reactive({
  source_url: '',
  table_kind: 'sheet' as 'sheet' | 'bitable',
})

const template = ref<PanFeishuTableTemplate>({
  sheetKind: '电子表格（飞书「表格」）',
  sheetKindBitable: '多维表格（飞书「多维表格」）',
  header: ['日期', '完播率', '视频留存率', '备注'],
  example: [],
  rules: [],
  aliasTable: {},
})

/** 时间格式化。 */
function formatTime(iso: string) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 加载绑定列表。 */
async function load() {
  loading.value = true
  try {
    const res = await feishuTableApi.listSources()
    sources.value = res.items ?? []
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

/** 绑定表格。 */
async function bind() {
  const url = bindForm.source_url.trim()
  if (!url) {
    ElMessage.warning('请粘贴表格链接')
    return
  }
  binding.value = true
  try {
    await feishuTableApi.bindSource({
      employee_id: '',
      source_url: url,
      table_kind: bindForm.table_kind,
    })
    ElMessage.success('绑定成功 —— 现在可以在飞书表里填了')
    bindForm.source_url = ''
    await load()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '绑定失败')
  } finally {
    binding.value = false
  }
}

/** 立即同步。 */
async function syncOne(row: PanFeishuMetricSource) {
  try {
    await feishuTableApi.syncSource(row.id)
    ElMessage.success('同步完成')
    await load()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '同步失败')
    await load()
  }
}

/** 解绑。 */
async function unbind(row: PanFeishuMetricSource) {
  try {
    await ElMessageBox.confirm(
      '解绑后这张表将不再被读取（历史数据保留）。确定解绑吗？',
      '解除绑定',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await feishuTableApi.unbindSource(row.id)
    ElMessage.success('已解绑')
    await load()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '解绑失败')
  }
}

onMounted(async () => {
  await load()
  try {
    const res = await feishuTableApi.getTemplate()
    if (res.data) template.value = res.data
  } catch {
    // ⚠️ 模板拿不到不影响主流程 —— 用默认值兜底。
  }
})

defineExpose({ reload: load })
</script>

<style scoped>
.feishu-table-binding {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-title {
  margin: 0;
  font-size: 14px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.section-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.hint-alert {
  margin-bottom: 4px;
}

.alert-body {
  font-size: 12px;
  line-height: 1.7;
}

.bind-table {
  margin-bottom: 4px;
}

.bind-form {
  margin-bottom: 4px;
}

.guide-steps {
  margin: 4px 0 8px;
  padding-left: 20px;
  line-height: 1.9;
  font-size: 13px;
}

.template-header {
  margin-top: 4px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.rules {
  margin-top: 8px;
}

.rule-list {
  margin: 0;
  padding-left: 18px;
  line-height: 1.8;
  font-size: 12px;
}

.alias-row {
  font-size: 12px;
  line-height: 1.9;
}
</style>
