export const MONTH_PLAN_APPROVAL_COMMENT_LIMIT = 5000

export interface MonthPlanOpinionItem {
  id: string | number
  sortNo?: string | number
  taskName?: string
}

export function monthPlanOpinionKey(item: MonthPlanOpinionItem) {
  return String(item.id)
}

export function buildMonthPlanRejectionComment(
  items: MonthPlanOpinionItem[],
  opinions: Record<string, string>,
) {
  const taskComments = items.flatMap((item, index) => {
    const opinion = opinions[monthPlanOpinionKey(item)]?.trim()
    if (!opinion) return []

    const sortNo = item.sortNo || index + 1
    const taskName = item.taskName?.trim() || '未命名任务'
    return [`【任务 ${sortNo}：${taskName}】\n意见：${opinion}`]
  })

  if (!taskComments.length) return ''
  return `${taskComments.join('\n\n')}\n\n【补充总体意见】\n`
}
