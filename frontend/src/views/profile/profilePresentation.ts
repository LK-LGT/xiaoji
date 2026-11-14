import { availableWorkspaces } from '../../navigation/workspaces.ts'

const BUILT_IN_ROLE_LABELS: Record<string, string> = {
  SUPER_ADMIN: '超级管理员',
  DEPT_LEADER: '部门负责人',
  PROJECT_MANAGER: '项目经理',
  EMPLOYEE: '员工',
  DIRECT_LEADER: '直属领导',
  DEPT_OWNER: '部门负责人',
  SYS_ADMIN: '系统管理员',
  REVIEWER: '裁决评审员',
}

export function roleLabel(code: string) {
  return BUILT_IN_ROLE_LABELS[code] || code
}

export function roleLabels(roles: readonly string[]) {
  return roles.length ? roles.map(roleLabel) : ['未配置角色']
}

export function workspaceLabels(permissions: readonly string[]) {
  const labels = availableWorkspaces(permissions).map((workspace) => workspace.title)
  return labels.length ? labels : ['未配置工作空间']
}
