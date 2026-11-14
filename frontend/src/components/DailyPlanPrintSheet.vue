<template>
  <section class="daily-plan-print-sheet" aria-hidden="true">
    <table>
      <thead>
        <tr class="daily-plan-print-title-row">
          <th colspan="7">项目组人员日计划表</th>
        </tr>
        <tr class="daily-plan-print-meta-row">
          <th colspan="2">项目名称</th>
          <td colspan="2" class="daily-plan-print-project">&nbsp;</td>
          <th>计划日期</th>
          <td colspan="2">{{ planDate }}</td>
        </tr>
        <tr class="daily-plan-print-column-row">
          <th class="print-compact-column">姓名</th>
          <th>今日工作计划</th>
          <th>工作完成情况</th>
          <th class="print-compact-column">审核人</th>
          <th class="print-compact-column">个人确认</th>
          <th>备注</th>
          <th class="print-compact-column">签字</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.rowKey">
          <td class="print-center print-compact-column">{{ row.employeeName }}</td>
          <td class="print-work-content">{{ row.workContent }}</td>
          <td>&nbsp;</td>
          <td class="print-compact-column">&nbsp;</td>
          <td class="print-compact-column">&nbsp;</td>
          <td>&nbsp;</td>
          <td class="print-compact-column">&nbsp;</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  rows: Array<{
    rowKey: string | number
    employeeName: string
    workContent: string
  }>
  planDate: string
}>()
</script>

<style scoped>
.daily-plan-print-sheet {
  display: none;
}

@page {
  size: A4 landscape;
  margin: 9mm;
}

@media print {
  :global(html),
  :global(body),
  :global(#app),
  :global(.app-shell),
  :global(.main) {
    display: block !important;
    width: 100% !important;
    height: auto !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: visible !important;
    background: #fff !important;
  }

  :global(.sidebar),
  :global(.topbar) {
    display: none !important;
  }

  :global(body *) {
    visibility: hidden !important;
  }

  :global(.leader-people-page),
  :global(.daily-review-page) {
    display: block !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    background: #fff !important;
  }

  :global(.leader-people-page > :not(.daily-plan-print-sheet):not(.project-week-print-sheet)),
  :global(.daily-review-page > :not(.daily-plan-print-sheet)) {
    display: none !important;
  }

  .daily-plan-print-sheet,
  .daily-plan-print-sheet * {
    visibility: visible !important;
  }

  .daily-plan-print-sheet {
    position: static;
    display: block;
    width: 100%;
    margin: 0;
    padding: 0;
    color: #000;
    background: #fff;
    font-family: "Microsoft YaHei", "SimSun", sans-serif;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
    break-after: avoid;
    page-break-after: avoid;
  }

  .daily-plan-print-sheet table {
    width: 100%;
    border: 1px solid #8699ac;
    border-collapse: collapse;
    table-layout: auto;
    color: #000;
    font-size: 9px;
    line-height: 1.45;
    break-after: avoid;
    page-break-after: avoid;
  }

  .daily-plan-print-sheet th,
  .daily-plan-print-sheet td {
    padding: 5px 6px;
    border: 1px solid #8699ac;
    overflow-wrap: anywhere;
    vertical-align: middle;
  }

  .daily-plan-print-sheet th {
    background: #d9eaf7;
    font-size: 10px;
    font-weight: 700;
    text-align: center;
  }

  .daily-plan-print-title-row th {
    height: 34px;
    padding: 7px 8px;
    color: #fff;
    background: #5b8ff9;
    font-size: 15px;
    line-height: 1.35;
    text-align: center;
  }

  .daily-plan-print-meta-row {
    height: 38px;
    font-size: 10px;
  }

  .daily-plan-print-meta-row > * {
    padding: 5px 7px;
    background: #fff;
    vertical-align: middle;
  }

  .daily-plan-print-meta-row th {
    background: #eef5fb;
    font-size: 10px;
  }

  .daily-plan-print-meta-row td:last-child {
    white-space: nowrap;
    text-align: center;
  }

  .daily-plan-print-sheet thead {
    display: table-header-group;
  }

  .daily-plan-print-sheet tbody tr {
    break-inside: avoid;
    page-break-inside: avoid;
  }

  .daily-plan-print-sheet tbody td {
    min-height: 38px;
  }

  .daily-plan-print-sheet .print-compact-column {
    width: 1%;
    white-space: nowrap;
  }
  .print-center { text-align: center; }
  .print-work-content { white-space: pre-wrap; }
}
</style>
