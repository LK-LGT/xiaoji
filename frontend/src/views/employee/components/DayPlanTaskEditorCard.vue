<template>
  <article class="day-plan-card" :class="{ 'has-error': showContentError }">
    <header>
      <button type="button" class="day-plan-card__toggle" :aria-expanded="expanded" @click="$emit('toggle')">
        <span class="day-plan-card__mark">日</span>
        <span>
          <strong>当天工作计划</strong>
          <small>{{ pointCount }} 个工作要点 · 日期由日计划入口自动确定</small>
        </span>
      </button>
      <el-tooltip :content="expanded ? '收起' : '展开'" placement="top">
        <el-button text circle :icon="expanded ? ArrowUp : ArrowDown" :aria-label="expanded ? '收起日计划' : '展开日计划'" @click="$emit('toggle')" />
      </el-tooltip>
    </header>

    <div v-show="expanded" class="day-plan-card__body">
      <section id="day-plan-content" class="day-plan-field" :class="{ 'is-error': showContentError }">
        <div class="day-plan-field__label">
          <span>01</span><strong>工作内容</strong><small>所有任务统一分点填写</small>
        </div>
        <el-input
          v-if="editable"
          v-model="content"
          type="textarea"
          :rows="9"
          maxlength="2000"
          show-word-limit
          placeholder="1. 第一项工作\n2. 第二项工作\n3. 第三项工作"
          :disabled="disabled"
        />
        <p v-else class="day-plan-card__readonly">{{ content || '未填写工作内容' }}</p>
        <small v-if="showContentError" class="day-plan-field__error">请填写当天工作内容</small>
      </section>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import { countWeekWorkPoints } from '@/utils/weekPlanDisplay'

defineProps<{ expanded: boolean; editable: boolean; disabled?: boolean; showContentError?: boolean }>()
defineEmits<{ toggle: [] }>()
const content = defineModel<string>('content', { required: true })
const pointCount = computed(() => countWeekWorkPoints(content.value))
</script>

<style scoped>
.day-plan-card { overflow:hidden; border:1px solid var(--line,#dce3df); border-radius:8px; background:#fff; transition:border-color .18s ease; }
.day-plan-card:hover { border-color:#b9cec4; }
.day-plan-card.has-error { border-color:#d88770; }
.day-plan-card header { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 16px; border-bottom:1px solid var(--line,#e4e9e6); background:#f6f9f6; }
.day-plan-card__toggle { display:flex; flex:1; min-width:0; align-items:center; gap:12px; padding:0; border:0; color:inherit; background:transparent; text-align:left; cursor:pointer; }
.day-plan-card__toggle > span:last-child { display:grid; min-width:0; gap:3px; }
.day-plan-card__toggle strong,.day-plan-card__toggle small { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.day-plan-card__toggle strong { font-size:16px; }
.day-plan-card__toggle small { color:var(--muted,#687a74); font-size:12px; }
.day-plan-card__mark { display:grid; width:38px; height:38px; flex:0 0 auto; place-items:center; border-radius:7px; color:#fff; background:var(--blue,#2d776c); font-weight:800; }
.day-plan-card__body { display:grid; gap:12px; padding:18px; }
.day-plan-field { display:grid; min-width:0; gap:8px; padding:13px; border:1px solid #e6ebe8; border-radius:8px; background:#fcfdfb; }
.day-plan-field.is-error { border-color:#d88770; background:#fff9f6; }
.day-plan-field__label { display:flex; align-items:baseline; gap:9px; }
.day-plan-field__label span { color:var(--blue,#2d776c); font-size:12px; font-weight:800; }
.day-plan-field__label strong { font-size:14px; }
.day-plan-field__label small { margin-left:auto; color:var(--muted,#687a74); font-size:11px; }
.day-plan-field__error { color:#b5533d; font-size:12px; }
.day-plan-card__readonly { min-height:72px; margin:0; line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }
@media (prefers-reduced-motion:reduce) { .day-plan-card { transition:none; } }
</style>
