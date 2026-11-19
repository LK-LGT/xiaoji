"""消息矩阵模板；业务触发点仅传变量，不拼通知正文。"""

SCENES = {
    "N11": ("考核流程待办与进展", "{label}，请查看方案考核记录。", "TODO", "/performance/scheme-filling"),
    "N1": ("月计划待审批", "{label}已提交月计划，请进入绩效系统审批。", "TODO", "/leader/month-plan-approval"),
    "N2": ("周计划待审批", "{label}已提交周计划，请进入绩效系统审批。", "TODO", "/leader/week-plan-approval"),
    "N3": ("自评截止提醒", "{label}自评即将截止，请及时完成自评。", "TODO", "/performance/self-reviews"),
    "N4": ("主管评分待办", "{label}有待完成的主管评分，请及时处理。", "TODO", "/performance"),
    "N5": ("计划已驳回", "{label}已被驳回，请查看审批意见后修改并重新提交。", "NOTICE", "/employee/month-plans"),
    "N6": ("成果确认待办", "{label}已提交成果，请按审批权限确认。", "TODO", "/leader/result-suggest"),
    "N7": ("申诉结果通知", "{label}申诉已完成裁决，请登录系统查看结果。", "NOTICE", "/dispute/cases"),
    "N8": ("绩效等级已发布", "{label}绩效结果已确认，请登录系统查看分数和等级。", "NOTICE", "/performance"),
    "N9": ("考核周期开始", "{label}考核已开始，请进入绩效系统查看任务。", "NOTICE", "/performance/self-reviews"),
    "N10": ("同步失败告警", "{label}同步发生异常，请进入系统查看告警。", "SYSTEM", "/system"),
}


def render(scene: str, label: str = "") -> tuple[str, str, str, str]:
    title, template, message_type, route = SCENES[scene]
    return title, template.format(label=label or "当前任务"), message_type, route
