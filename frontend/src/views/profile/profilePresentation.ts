import { availableWorkspaces } from '../../navigation/workspaces.ts'

/**
 * 角色码 → 中文标签。
 *
 * ⚠️⚠️ **key 必须与后端 `roles` 字段发来的字符串完全一致**
 *
 * 后端权威来源（两处，必须同步）：
 * - `backend/app/services/access_control.py` → `roles.add("...")`
 * - `backend/app/routes/auth.py` → `ROLE_UI_PERMISSIONS` 的 key
 *
 * 实际只有 **5 个**角色，且可叠加（一个人可能同时是员工 + 主管 + HR）：
 *
 * | 角色码 | 含义 | 判定依据 |
 * |---|---|---|
 * | `employee` | 员工 | 基础角色，人人都有 |
 * | `manager` | 主管 | 有直属下属 / 汇报线 / 代理授权 |
 * | `department_leader` | 部门负责人 | 管辖某个部门 |
 * | `hr` | HR | 所属部门命中 `hr_department_config` |
 * | `admin` | 系统管理员 | `system_admin` 表显式授权 |
 *
 * ⚠️ **历史坑（已修）**：这份表以前写的是
 * `SUPER_ADMIN` / `DEPT_LEADER` / `EMPLOYEE` 等**全大写自造码**，
 * 与后端发的 `admin` / `department_leader` / `employee` **完全对不上**
 * ⇒ `roleLabel()` 匹配不到就**原样返回英文** ⇒ 用户在个人中心看到
 * 「admin department_leader employee hr manager」一串英文。
 *
 * ⚠️ **为什么没有「超级管理员」**：
 * 后端**不存在**这个角色，也不该存在 —— 管理员是**公司内真实员工**
 * （`system_admin` 表绑定 `employee_id`），只是被授予了系统配置权限，
 * **不是**能随意查看/伪装全公司数据的万能账号。
 * ⇒ 「超级管理员」这个词会误导用户以为存在后门角色，直接不写。
 */
const BUILT_IN_ROLE_LABELS: Record<string, string> = {
  employee: '员工',
  manager: '主管',
  department_leader: '部门负责人',
  hr: 'HR',
  admin: '系统管理员',
}

/**
 * 单个角色码 → 中文标签。
 *
 * ⚠️ **未知角色码原样返回**（不返回「未知角色」）——
 * 后端新增角色而前端还没跟上时，至少能看到原始码便于排查，
 * 总比显示一个含糊的「未知角色」好。
 */
export function roleLabel(code: string) {
  return BUILT_IN_ROLE_LABELS[code] || code
}

/**
 * 角色列表 → 中文标签列表（保持后端给的顺序）。
 */
export function roleLabels(roles: readonly string[]) {
  return roles.length ? roles.map(roleLabel) : ['未配置角色']
}

/**
 * 权限位 → 工作空间标题列表。
 */
export function workspaceLabels(permissions: readonly string[]) {
  const labels = availableWorkspaces(permissions).map((workspace) => workspace.title)
  return labels.length ? labels : ['未配置工作空间']
}
