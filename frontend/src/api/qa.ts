/**
 * 问答（Q&A）前端 API 客户端。
 *
 * ⚠️ 语义提醒（贯穿本文件）：
 *   1. **问答只读** —— 前端不得提供任何"改分/提交/审批"入口。
 *   2. **AI 不产生数字** —— 回答里的分数一律来自业务层规则引擎，
 *      前端不解析、不二次计算、不展示"AI 建议分数"。
 *   3. **降级要显性** —— `degraded` 为真时必须显示降级提示，
 *      不能把"AI 不可用"伪装成正常回答。
 *   4. **引用不可信** —— `citations` 来自模型，必须校验它在 `sources` 内，
 *      否则视为无效回答。
 */
import { http } from './http'

/** 可问答的考核任务。 */
export interface QaTask {
  task_id: string
  name: string
  /**
   * 人类可区分的展示名。
   *
   * ⚠️ 为什么不能直接用 `name`：真实库里存在**多个同名任务**
   *    （如 23 个「2026 年 11 月月度考核」）。后端在同名时会给 `label`
   *    追加区分后缀（部门 / 状态 / 创建时间 / 短 ID）。
   *    前端**直接展示 label**，不要自己拼 —— 后端才知道哪个才是「本期」。
   */
  label: string
  period: string
  status: string
  /** `self`（查本人） / `department`（查部门聚合）。 */
  scope: 'self' | 'department'
  /** 该任务下实际可用的只读工具。 */
  allowed_tools: string[]
  /** 是否有冻结配置 —— 没有则指标类问题查不到数。 */
  has_snapshot: boolean
  created_at: string | null
}

/** 一次工具调用的留痕。 */
export interface QaToolCall {
  tool: string
  ok: boolean
  error: string | null
  latency_ms: number
}

/** 数据质量 —— 由 L2 业务层计算，**不接受模型自报**。 */
export interface QaDataQuality {
  completeness: number
  as_of: string
  degraded: boolean
  degrade_reason: string | null
  note: string
}

/** 问答回答。 */
export interface QaAnswer {
  answer: string
  /** 模型给出的引用标识（如 `tool:call_xxx`、evidence id）。 */
  citations: string[]
  /** 本次回答的全部来源标识（`citations` 必须是它的子集）。 */
  sources: string[]
  /** 是否降级（AI 不可用或工具部分失败）。 */
  degraded: boolean
  /** 预取的原始结构化数据（AI 不可用时用户仍能看明细）。 */
  data: Record<string, any>
  follow_up_questions: string[]
  tool_calls: QaToolCall[]
  trace_id: string
  data_quality: QaDataQuality
  /**
   * 被剔除的无效引用数量。
   *
   * ⚠️ > 0 表示模型引用了**不存在**的来源，已被服务端剔除。
   *    这不等于降级（工具正常、回答可读），但用户应当知道
   *    「这份回答有一部分引用没能核验」。
   */
  dropped_citations: number
}

export const qaApi = {
  /**
   * 列出当前用户**有权问答**的考核任务。
   *
   * ⚠️ 无权问答的任务不会出现在列表里 —— 这是后端的权限收敛，
   *    前端不需要（也不应该）再自己过滤一遍。
   */
  tasks: () => http.get<unknown, { items: QaTask[] }>('/qa/tasks'),

  /**
   * 提问。
   *
   * @param question 自然语言问题（后端限制 500 字）
   * @param taskId  考核任务 ID —— 决定数据范围，必须来自 `qaApi.tasks()`
   * @param period  周期（紧凑格式 `202611`）；不传则用任务自身的周期
   */
  ask: (question: string, taskId: string, period?: string | null) =>
    http.post<unknown, QaAnswer>('/qa/ask', {
      question,
      task_id: taskId,
      period: period ?? undefined,
    }),
}

/**
 * 回答是否**结构完整**。
 *
 * ⚠️ 后端已经做了一次校验（`citations ⊆ sources`，否则整份回答丢弃并降级），
 *    这里再校验一次是**防御性**的 —— 前端渲染的是用户直接信任的内容，
 *    不能假设上游永远正确。
 */
export function isValidQaAnswer(answer: QaAnswer | null | undefined): answer is QaAnswer {
  if (!answer || typeof answer.answer !== 'string') return false
  if (!answer.answer.trim()) return false
  const sources = new Set(answer.sources || [])
  return (answer.citations || []).every((citation) => sources.has(citation))
}
