/**
 * ★ **「部门工作量」API 客户端**（2026-11-25 新增）。
 *
 * ⚠️ **为什么独立一个文件，而不是复用 `panWorkload.ts`**
 * --------------------------------------------------
 * `panWorkload.ts` 里有一堆**剪辑专属**的东西：
 * 百度网盘文件、目录绑定、人工裁定的「成片」概念、
 * 以及**硬编码的 5 个视频指标**（`MANUAL_METRIC_LABELS` 等）。
 *
 * ⇒ 通用板块**根本不需要**那些；而**指标还没定**
 * ⇒ 硬编码指标标签在通用板块上**必然是错的**。
 *
 * ⚠️ **本文件的核心设计：指标由后端下发**
 * ----------------------------------------
 * `myScope` 返回后端算好的「我要填哪些指标 + 它们的标签和量纲」，
 * ⇒ 前端**只渲染**，不定义指标。
 * ⇒ 业务定了「仓储部填拣货量」之后，**前端一行都不用改**。
 */

import { http } from './http'

/**
 * ⚠️ **路径不能带 ``/v1``**（2026-11-25）
 * --------------------------------------------
 * vite 代理把 ``/api/xxx`` 重写成 ``/v1/xxx``（见 ``vite.config.ts``）。
 * ⇒ 这里写 ``/v1/xxx`` 会变成 ``/v1/v1/xxx`` ⇒ **404**。
 *
 * ⚠️ 与 ``panWorkload.ts`` 的 ``BASE = '/baidu/workload'`` 同一口径。
 */
const BASE = '/dept/workload'

/** 一个指标的展示元数据（**后端下发**）。 */
export interface DeptWorkloadMetricSpec {
  code: string
  label: string
  /** `count`（条数/整数）/ `rate`（比率 0~1）。 */
  kind: 'count' | 'rate'
  placeholder: string
  required: boolean
}

/**
 * ★ **我在「部门工作量」里的身份**（后端下发）。
 *
 * ⚠️ **口径表是唯一事实源** —— 前端不猜。
 */
export interface DeptWorkloadMyScope {
  /** `reporter`（要填报）/ `reviewer`（只审）/ `none`（不参与）。 */
  role: 'reporter' | 'reviewer' | 'none'
  /** 我能填的指标码（reporter 角色）。 */
  reporterMetricCodes: string[]
  /** 我能填的指标码（reviewer 角色）。 */
  reviewerMetricCodes: string[]
  /** 指标展示元数据（后端按上面两个列表下发）。 */
  metrics: DeptWorkloadMetricSpec[]
}

/** 一条人工填报指标。 */
export interface DeptWorkloadMetricRow {
  id: string
  employeeId: string
  metricDate: string
  metricCode: string
  metricValue: number
  scope: string
  source: string
  remark: string
  createdAt: string
}

/** 提交一条填报。 */
export interface DeptWorkloadMetricPayload {
  metricCode: string
  metricDate: string
  metricValue: number
  remark?: string
}

export const deptWorkloadApi = {
  /** 我的填报口径（角色 + 要填的指标 + 指标元数据）。 */
  async myScope(): Promise<DeptWorkloadMyScope> {
    const { data } = await http.get(`${BASE}/my-scope`)
    return data as DeptWorkloadMyScope
  },

  /** 我填过的指标。 */
  async myMetrics(params?: { start?: string; end?: string; metricCode?: string }) {
    const { data } = await http.get(`${BASE}/my-metrics`, { params })
    return (data as { items: DeptWorkloadMetricRow[] }).items ?? []
  },

  /** 填报一条指标。 */
  async submitMetric(payload: DeptWorkloadMetricPayload) {
    const { data } = await http.post(`${BASE}/manual-metrics`, {
      metric_code: payload.metricCode,
      metric_date: payload.metricDate,
      metric_value: payload.metricValue,
      remark: payload.remark ?? '',
    })
    return data
  },
}

export default deptWorkloadApi
