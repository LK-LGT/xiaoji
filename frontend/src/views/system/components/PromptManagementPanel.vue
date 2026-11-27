<template>
  <section class="prompt-panel">
    <div class="prompt-panel__header">
      <div>
        <h3>Prompt 版本</h3>
        <p>按场景维护版本；发布后同一场景的最新版本生效。</p>
      </div>
      <div class="prompt-panel__actions">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">发布版本</el-button>
      </div>
    </div>

    <el-table :data="prompts" v-loading="loading" empty-text="暂无 Prompt 版本">
      <el-table-column prop="sceneCode" label="场景" min-width="140" />
      <el-table-column prop="versionNo" label="版本" width="100" />
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'PUBLISHED' ? 'success' : 'info'" effect="plain">
            {{ row.status === 'PUBLISHED' ? '已发布' : row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="系统提示词" min-width="280">
        <template #default="{ row }">
          <span class="prompt-preview">{{ row.systemPrompt }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="outputSchemaVersion" label="输出契约" width="120" />
      <el-table-column prop="createdAt" label="发布时间" width="180" />
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openCopy(row)">基于此版新建</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="发布 Prompt 版本" width="min(820px, 94vw)" destroy-on-close>
      <el-form label-position="top">
        <div class="form-grid two-columns">
          <el-form-item label="场景标识" required>
            <el-input v-model="form.sceneCode" maxlength="64" placeholder="如 plan_generate" />
          </el-form-item>
          <el-form-item label="版本号" required>
            <el-input v-model="form.versionNo" maxlength="32" placeholder="如 v2" />
          </el-form-item>
        </div>
        <el-form-item label="系统提示词" required>
          <el-input v-model="form.systemPrompt" type="textarea" :rows="8" maxlength="20000" show-word-limit />
        </el-form-item>
        <el-form-item label="用户模板" required>
          <el-input v-model="form.userTemplate" type="textarea" :rows="6" maxlength="10000" show-word-limit />
        </el-form-item>
        <el-form-item label="输出契约版本" required>
          <el-input v-model="form.outputSchemaVersion" maxlength="64" placeholder="如 v1" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="publish">发布</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  listAiPromptsApi,
  publishAiPromptApi,
  type AiPrompt,
} from '@/api/aiManagement'

const prompts = ref<AiPrompt[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const form = reactive({
  sceneCode: '',
  versionNo: '',
  systemPrompt: '',
  userTemplate: '',
  outputSchemaVersion: 'v1',
})

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败'
}

async function load() {
  loading.value = true
  try {
    prompts.value = await listAiPromptsApi()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, {
    sceneCode: '',
    versionNo: '',
    systemPrompt: '',
    userTemplate: '',
    outputSchemaVersion: 'v1',
  })
  dialogVisible.value = true
}

function openCopy(row: AiPrompt) {
  Object.assign(form, {
    sceneCode: row.sceneCode,
    versionNo: '',
    systemPrompt: row.systemPrompt,
    userTemplate: row.userTemplate,
    outputSchemaVersion: row.outputSchemaVersion,
  })
  dialogVisible.value = true
}

async function publish() {
  if (!form.sceneCode.trim() || !form.versionNo.trim() || !form.systemPrompt.trim() || !form.userTemplate.trim()) {
    ElMessage.warning('场景、版本、系统提示词、用户模板均为必填')
    return
  }
  saving.value = true
  try {
    await publishAiPromptApi({
      sceneCode: form.sceneCode.trim(),
      versionNo: form.versionNo.trim(),
      systemPrompt: form.systemPrompt,
      userTemplate: form.userTemplate,
      outputSchemaVersion: form.outputSchemaVersion.trim() || 'v1',
    })
    ElMessage.success('Prompt 版本已发布')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.prompt-panel { display: flex; flex-direction: column; gap: 14px; }
.prompt-panel__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.prompt-panel__header h3 { margin: 0; font-size: 16px; }
.prompt-panel__header p { margin: 4px 0 0; color: #75847d; font-size: 12px; }
.prompt-panel__actions { display: flex; gap: 8px; }
.prompt-preview { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; white-space: pre-wrap; color: #4b5d55; }
.form-grid.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
</style>
