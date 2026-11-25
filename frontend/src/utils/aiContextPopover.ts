/**
 * 🧩 **月计划 AI 上下文选择器的纯逻辑**（CI-5 阶段 2 第五轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``MonthPlanAiContextPopover.vue`` 有 **323 行**，其中「可用项筛选 /
 * 父子联动 / 半选判定 / 增删选择」全是**纯计算**——不需要挂载组件就能测。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖 el-popover / 大量 props / 双向 emit，挂载失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 上下文类型。
 *
 * ⚠️ **泛型参数**让调用方保留自己的联合类型
 * （例如 ``MonthPlanContextType`` 是有限枚举）。
 */
export type AiContextType = string

/** 历史月份选项。 */
export interface AiHistoryMonthOption {
  month: string
  available: boolean
}

/** 知识类型选项。 */
export interface AiKnowledgeTypeOption {
  type: string
  available: boolean
}

/** 远端返回的上下文选项。 */
export interface AiRemoteContextOption<T extends AiContextType = AiContextType> {
  type: T
  label?: string
  available?: boolean
  summary?: string
}

/** 上下文信息（``AiContextResponse`` 的子集）。 */
export interface AiContextInfo<T extends AiContextType = AiContextType> {
  historyMonthOptions?: AiHistoryMonthOption[]
  knowledgeTypeOptions?: AiKnowledgeTypeOption[]
  contextOptions?: AiRemoteContextOption<T>[]
  availableContext?: string[]
}

/** 分组的可选值 / 已选值 / 总数。 */
export interface AiContextGroupState {
  available: string[]
  selected: string[]
  total: number
}

/** 上下文选项的展示行。 */
export interface AiContextOptionRow<T extends AiContextType = AiContextType> {
  type: T
  label: string
  available: boolean
  summary: string
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 可用项筛选
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 可用的历史月份。
 *
 * ⚠️ **只取 ``available`` 为真的** ⇒ 用户不会去选一个拿不到数据的月份。
 */
export function selectAvailableHistoryMonths(
  options: AiHistoryMonthOption[] | undefined,
): string[] {
  return (options || []).filter((item) => item.available).map((item) => item.month)
}

/**
 * 可用的知识类型。
 *
 * ⚠️ 同上：**只取 ``available`` 为真的**。
 */
export function selectAvailableKnowledgeTypes(
  options: AiKnowledgeTypeOption[] | undefined,
): string[] {
  return (options || []).filter((item) => item.available).map((item) => item.type)
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 分组判定
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 该上下文类型是否是「分组型」。
 *
 * ⚠️ **只有历史计划和企业知识是分组型**（它们各有子项）；
 *    其余是单选 ⇒ 不显示复选框的半选态。
 */
export function isGroupedAiContextType(type: AiContextType): boolean {
  return type === 'HISTORY_PLANS' || type === 'ENTERPRISE_KNOWLEDGE'
}

/**
 * 取分组的可选 / 已选 / 总数。
 *
 * ⚠️ **非分组型返回 ``undefined``** ⇒ 调用方据此走「单选」分支。
 */
export function resolveAiContextGroupState(
  type: AiContextType,
  input: {
    availableHistoryMonths: string[]
    selectedHistoryMonths: string[]
    historyMonthTotal: number
    availableKnowledgeTypes: string[]
    selectedKnowledgeTypes: string[]
    knowledgeTypeTotal: number
  },
): AiContextGroupState | undefined {  if (type === 'HISTORY_PLANS') {
    return {
      available: input.availableHistoryMonths,
      selected: input.selectedHistoryMonths,
      total: input.historyMonthTotal,
    }
  }
  if (type === 'ENTERPRISE_KNOWLEDGE') {
    return {
      available: input.availableKnowledgeTypes,
      selected: input.selectedKnowledgeTypes,
      total: input.knowledgeTypeTotal,
    }
  }
  return undefined
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 选中 / 半选判定
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 该上下文是否「全选」。
 *
 * ⚠️ **分组型**：可用项**全部**被选中才算全选；
 *    **可用项为空时不算全选**（否则会显示一个没法取消的勾）。
 * ⚠️ **单选型**：看父级选择里有没有它。
 */
export function isAiContextChecked<T extends AiContextType>(
  type: T,
  group: AiContextGroupState | undefined,
  selectedContextTypes: T[],
): boolean {
  if (group) {
    return group.available.length > 0
      && group.available.every((value) => group.selected.includes(value))
  }
  return selectedContextTypes.includes(type)
}

/**
 * 该上下文是否「半选」。
 *
 * ⚠️ **只对分组型有意义**（单选型没有半选）。
 * ⚠️ **部分选中且未全选** ⇒ 半选。
 */
export function isAiContextIndeterminate(
  group: AiContextGroupState | undefined,
): boolean {
  if (!group) return false
  const selected = group.available.filter((value) => group.selected.includes(value)).length
  return selected > 0 && selected < group.available.length
}

/**
 * 可选的上下文类型。
 *
 * ⚠️ **历史计划要有可用的月份才可选**；
 *    **企业知识要有可用的类型才可选**；
 *    **其余一律可选**。
 */
export function selectAiContextTypes<T extends AiContextType>(
  allTypes: T[],
  availableHistoryMonths: string[],
  availableKnowledgeTypes: string[],
): T[] {
  return allTypes.filter((type) => {
    if (type === 'HISTORY_PLANS') return availableHistoryMonths.length > 0
    if (type === 'ENTERPRISE_KNOWLEDGE') return availableKnowledgeTypes.length > 0
    return true
  })
}

/**
 * 父级上下文是否全选。
 *
 * ⚠️ **空列表 ⇒ 全选**（`every` 对空数组返回 true）——
 *    这是 Vue checkbox 的标准语义。
 */
export function isAllAiContextSelected<T extends AiContextType>(
  selectableContextTypes: T[],
  selectedContextTypes: T[],
): boolean {
  return selectableContextTypes.every((type) => selectedContextTypes.includes(type))
}

/**
 * 父级上下文是否半选。
 *
 * ⚠️ **选了但没全选** ⇒ 半选。
 */
export function isAiContextSelectionIndeterminate<T extends AiContextType>(
  selectableContextTypes: T[],
  selectedContextTypes: T[],
): boolean {
  const selected = selectableContextTypes.filter((type) => selectedContextTypes.includes(type)).length
  return selected > 0 && selected < selectableContextTypes.length
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 选择集合的增删
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 增删一个选择值（**返回新数组**，不改原数组）。
 *
 * ⚠️ **用 ``Set``** ⇒ 天然去重，重复勾选不会产生重复项。
 */
export function updateAiSelection(
  values: string[],
  value: string,
  selected: boolean,
): string[] {
  const next = new Set(values)
  if (selected) next.add(value)
  else next.delete(value)
  return [...next]
}

/**
 * 增删一个父级上下文类型。
 */
export function updateAiContextTypeSelection<T extends AiContextType>(
  values: T[],
  type: T,
  selected: boolean,
): T[] {
  const next = new Set(values)
  if (selected) next.add(type)
  else next.delete(type)
  return [...next]
}

/**
 * 「全选 / 全不选」的结果。
 *
 * ⚠️ **全选时选的是「可选类型」而不是「全部类型」**
 *    ⇒ 没有数据的历史计划不会被勾上。
 */
export function resolveAiToggleAll<T extends AiContextType>(
  value: boolean | string | number,
  selectableContextTypes: T[],
  availableHistoryMonths: string[],
  availableKnowledgeTypes: string[],
): {
  contextTypes: T[]
  historyMonths: string[]
  knowledgeTypes: string[]
} {
  const selected = Boolean(value)
  return {
    contextTypes: selected ? [...selectableContextTypes] : [],
    historyMonths: selected ? [...availableHistoryMonths] : [],
    knowledgeTypes: selected ? [...availableKnowledgeTypes] : [],
  }
}

/**
 * 切换某个分组（历史计划 / 企业知识）后的联动结果。
 *
 * ⚠️ **子项全选 ⇒ 父级也选中；子项全清 ⇒ 父级也取消**。
 */
export function resolveAiGroupToggle(
  type: AiContextType,
  selected: boolean,
  availableHistoryMonths: string[],
  availableKnowledgeTypes: string[],
): { values: string[]; parentSelected: boolean } {
  if (type === 'HISTORY_PLANS') {
    const values = selected ? [...availableHistoryMonths] : []
    return { values, parentSelected: values.length > 0 }
  }
  const values = selected ? [...availableKnowledgeTypes] : []
  return { values, parentSelected: values.length > 0 }
}

/**
 * 切换单个子项后的联动结果。
 *
 * ⚠️ **父级是否选中 = 「还有没有剩下的已选子项」**
 *    ⇒ 取消最后一个子项时父级自动取消。
 */
export function resolveAiChildToggle(
  type: AiContextType,
  values: string[],
  value: string,
  selected: boolean,
): { values: string[]; parentSelected: boolean } {
  const next = updateAiSelection(values, value, selected)
  return { values: next, parentSelected: next.length > 0 }
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 展示行
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 构造上下文选项的展示行。
 *
 * ⚠️ **标签**：远端标签优先 ⇒ 没有则用本地常量表。
 * ⚠️ **可用性判定有优先级**：
 *   ① 分组型 ⇒ 有可选项才算可用；
 *   ② 岗位描述 ⇒ 文本非空才算可用；
 *   ③ 其余 ⇒ 远端 ``available`` 优先，没有则看中文标签是否在 ``availableContext`` 里。
 * ⚠️ **摘要**：
 *   ① 分组型 ⇒ 「已选 x/y 个月/类」；
 *   ② 岗位描述 ⇒ 描述文本；
 *   ③ 其余 ⇒ 远端摘要（去空白）。
 */
export function buildAiContextOptionRows<T extends AiContextType>(
  allTypes: T[],
  contextInfo: AiContextInfo<T> | undefined,
  jobDescription: string,
  localLabels: Record<T, string>,
  input: {
    availableHistoryMonths: string[]
    selectedHistoryMonths: string[]
    historyMonthTotal: number
    availableKnowledgeTypes: string[]
    selectedKnowledgeTypes: string[]
    knowledgeTypeTotal: number
  },
): AiContextOptionRow<T>[] {
  return allTypes.map((type) => {
    const remote = contextInfo?.contextOptions?.find((item) => item.type === type)
    const jobSummary = jobDescription.trim()
    const group = resolveAiContextGroupState(type, input)
    return {
      type,
      label: remote?.label || localLabels[type],
      available: group
        ? group.available.length > 0
        : type === 'JOB_DESCRIPTION'
          ? Boolean(jobSummary)
          : remote?.available ?? Boolean(contextInfo?.availableContext?.includes(localLabels[type])),
      summary: group
        ? `已选 ${group.selected.length}/${group.total} ${type === 'HISTORY_PLANS' ? '个月' : '类'}`
        : type === 'JOB_DESCRIPTION'
          ? jobDescription.trim()
          : String(remote?.summary || '').trim(),
    }
  })
}

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 知识引用与标签
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 过滤出「已选知识类型」的知识引用。
 *
 * ⚠️ **只保留被勾选的类型** ⇒ 用户没勾的类型不该在预览里出现。
 */
export function selectAiKnowledgeReferences<T extends { knowledgeType: string }>(
  references: T[] | undefined,
  selectedKnowledgeTypes: string[],
): T[] {
  return (references || []).filter((item) => selectedKnowledgeTypes.includes(item.knowledgeType))
}

/** 知识引用类型 ⇒ 中文文案。 */
export const AI_KNOWLEDGE_BINDING_LABELS: Record<string, string> = {
  NORMATIVE: '正式规范',
  REFERENCE: '参考资料',
  EXAMPLE: '示例案例',
}

/**
 * 知识引用类型 ⇒ 中文文案。
 *
 * ⚠️ **未知类型原样返回**；**空值回落「知识资料」**。
 */
export function resolveAiBindingLabel(value: string): string {
  return AI_KNOWLEDGE_BINDING_LABELS[value] || value || '知识资料'
}
