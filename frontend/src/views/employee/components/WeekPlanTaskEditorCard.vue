<template>
  <article class="week-plan-card" :class="{ 'has-error': hasError }">
    <header>
      <button type="button" class="week-plan-card__toggle" :aria-expanded="expanded" @click="$emit('toggle')">
        <span class="week-plan-card__mark">周</span>
        <span><strong>本周计划</strong><small>{{ pointCount }} 个工作要点 · {{ weekStart }} 至 {{ weekEnd }}</small></span>
      </button>
      <el-tooltip :content="expanded ? '收起' : '展开'" placement="top">
        <el-button text circle :icon="expanded ? ArrowUp : ArrowDown" :aria-label="expanded ? '收起周计划' : '展开周计划'" @click="$emit('toggle')" />
      </el-tooltip>
    </header>

    <div v-show="expanded" class="week-plan-card__body">
      <section id="week-item-0-content" class="week-plan-field is-content" :class="{ 'is-error': fieldErrors.content }">
        <div class="week-plan-field__label"><span>01</span><strong>工作内容</strong><small>所有任务统一分点填写</small></div>
        <el-input v-model="model.content" type="textarea" :rows="9" maxlength="20000" show-word-limit placeholder="1. 第一项工作&#10;2. 第二项工作&#10;3. 第三项工作" :disabled="disabled" />
        <small v-if="fieldErrors.content" class="week-plan-field__error">{{ fieldErrors.content }}</small>
      </section>

      <section class="week-plan-standard-card">
        <div class="week-plan-standard-card__title">考核标准（交付物、质量）</div>
        <label id="week-item-0-deliverable" class="week-plan-field" :class="{ 'is-error': fieldErrors.deliverable }">
          <div class="week-plan-field__label"><span>02</span><strong>交付物</strong></div>
          <el-input v-model="model.deliverable" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="填写交付物" :disabled="disabled" />
          <small v-if="fieldErrors.deliverable" class="week-plan-field__error">{{ fieldErrors.deliverable }}</small>
        </label>
        <label id="week-item-0-quality" class="week-plan-field" :class="{ 'is-error': fieldErrors.quality }">
          <div class="week-plan-field__label"><span>03</span><strong>质量</strong></div>
          <el-input v-model="model.quality" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="填写质量标准" :disabled="disabled" />
          <small v-if="fieldErrors.quality" class="week-plan-field__error">{{ fieldErrors.quality }}</small>
        </label>
      </section>

      <div class="week-plan-card__period"><span>统计周期</span><strong>{{ weekStart }} 至 {{ weekEnd }}</strong></div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import type { WeekPlanSavePayload } from '@/api/weekPlan'
import { countWeekWorkPoints } from '@/utils/weekPlanDisplay'

const props = defineProps<{ weekStart: string; weekEnd: string; expanded: boolean; disabled?: boolean; fieldErrors: { content?: string; deliverable?: string; quality?: string } }>()
defineEmits<{ toggle: [] }>()
const model = defineModel<WeekPlanSavePayload['items'][number]>({ required: true })
const pointCount = computed(() => countWeekWorkPoints(model.value.content))
const hasError = computed(() => Object.values(props.fieldErrors).some(Boolean))
</script>

<style scoped>
.week-plan-card { overflow:hidden; border:1px solid var(--line,#dce3df); border-radius:8px; background:#fff; transition:border-color .18s ease,box-shadow .18s ease; }
.week-plan-card:hover { border-color:#b9cec4; box-shadow:0 9px 24px rgb(35 57 49 / 7%); }
.week-plan-card.has-error { border-color:#e4b9ab; }
.week-plan-card header { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:14px 16px; border-bottom:1px solid var(--line,#e4e9e6); background:#f6f9f6; }
.week-plan-card__toggle { display:flex; flex:1; min-width:0; align-items:center; gap:12px; padding:0; border:0; color:inherit; background:transparent; text-align:left; cursor:pointer; }
.week-plan-card__toggle > span:last-child { display:grid; min-width:0; gap:3px; }
.week-plan-card__toggle strong,.week-plan-card__toggle small { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.week-plan-card__toggle strong { font-size:16px; }
.week-plan-card__toggle small { color:var(--muted,#687a74); font-size:12px; }
.week-plan-card__mark { display:grid; width:38px; height:38px; flex:0 0 auto; place-items:center; border-radius:7px; color:#fff; background:var(--blue,#2d776c); font-weight:800; }
.week-plan-card__body { display:grid; gap:14px; padding:18px; }
.week-plan-field { display:grid; min-width:0; gap:8px; padding:13px; border:1px solid #e6ebe8; border-radius:8px; background:#fcfdfb; }
.week-plan-field.is-error { border-color:#d88770; background:#fff9f6; }
.week-plan-field__label { display:flex; align-items:baseline; gap:9px; }
.week-plan-field__label span { color:var(--blue,#2d776c); font-size:12px; font-weight:800; }
.week-plan-field__label strong { font-size:14px; }
.week-plan-field__label small { margin-left:auto; color:var(--muted,#687a74); font-size:11px; }
.week-plan-field__error { color:#b5533d; font-size:12px; }
.week-plan-standard-card { display:grid; gap:10px; }
.week-plan-standard-card__title { color:#304a42; font-size:14px; font-weight:750; }
.week-plan-card__period { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:11px 13px; border:1px solid #e1e8e4; border-radius:8px; color:var(--muted,#687a74); background:#f6f8f6; font-size:12px; }
.week-plan-card__period strong { color:var(--ink,#263a34); }
</style>
