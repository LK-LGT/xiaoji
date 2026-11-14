<template>
  <section class="result-state-panel" :class="`is-${state}`" role="status">
    <el-icon v-if="state === 'error'" :size="28"><WarningFilled /></el-icon>
    <el-icon v-else-if="state === 'no-access'" :size="28"><Lock /></el-icon>
    <el-icon v-else :size="28"><FolderOpened /></el-icon>
    <strong>{{ title }}</strong>
    <span>{{ message || defaultMessage }}</span>
    <el-button v-if="state === 'error'" type="primary" plain @click="$emit('retry')">重新加载</el-button>
    <el-button v-else-if="state === 'no-results'" link type="primary" @click="$emit('clear')">清除筛选</el-button>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { FolderOpened, Lock, WarningFilled } from '@element-plus/icons-vue'

const props = defineProps<{ state: 'loading' | 'error' | 'empty' | 'no-results' | 'no-access'; message?: string }>()
defineEmits<{ retry: []; clear: [] }>()
const title = computed(() => ({
  loading: '正在加载成果',
  error: '成果加载失败',
  empty: '还没有成果记录',
  'no-results': '没有匹配的成果',
  'no-access': '暂无成果权限',
}[props.state]))
const defaultMessage = computed(() => ({
  loading: '请稍候。',
  error: '请检查网络后重试。',
  empty: '成果提交并完成审批后，会在这里形成档案。',
  'no-results': '请调整筛选条件后重试。',
  'no-access': '当前账号没有查看该成果范围的权限。',
}[props.state]))
</script>
