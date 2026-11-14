<template>
  <section class="approval-timeline"><div v-if="!events.length" class="empty">暂无审批记录</div><div v-for="event in events" :key="`${event.label}-${event.occurredAt}`" class="timeline-item" :class="{ 'is-current': event.current, 'is-danger': event.state === 'REJECTED' }"><span class="timeline-dot"></span><div><strong>{{ event.label }}</strong><small>{{ event.operatorName || '系统' }} · {{ event.occurredAt || '--' }}</small><p v-if="event.comment">{{ event.comment }}</p></div></div></section>
</template>
<script setup lang="ts">
interface ApprovalEvent { label: string; operatorName?: string; occurredAt?: string; comment?: string; state?: string; current?: boolean }
defineProps<{ events: ApprovalEvent[] }>()
</script>
<style scoped>.approval-timeline { width:100%; padding:28px 20px; box-sizing:border-box; }.timeline-item { position:relative; display:grid; grid-template-columns:20px 1fr; gap:12px; min-height:86px; }.timeline-item:not(:last-child)::before { position:absolute; top:17px; bottom:0; left:7px; width:1px; background:#d8e1dc; content:''; }.timeline-dot { z-index:1; width:14px; height:14px; margin-top:3px; border:3px solid #b8c9c1; border-radius:50%; background:#fff; }.timeline-item.is-current .timeline-dot { border-color:#2d776c; }.timeline-item.is-danger .timeline-dot { border-color:#b64d45; }.timeline-item strong { display:block; }.timeline-item small { display:block; margin-top:5px; color:#75847d; }.timeline-item p { max-width:920px; margin:8px 0; color:#53665f; line-height:1.6; }.empty { color:#75847d; }</style>
