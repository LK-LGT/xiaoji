<template>
  <el-tag :type="meta.type" :effect="effect" size="small">
    {{ meta.label }}
  </el-tag>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { resultStatusMeta } from '@/views/employee/resultStatus'

const props = withDefaults(defineProps<{ status: unknown; effect?: 'light' | 'plain' }>(), { effect: 'plain' })
const meta = computed(() => resultStatusMeta(props.status))
</script>
