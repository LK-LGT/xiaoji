"""Feishu scheme filling using frozen tasks and the shared submission service."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from ..domain.enums import SubmitSource
from ..domain.models import AssessmentRecord, AssessmentTask, ConfigSnapshot, Employee
from . import feishu_card as cards
from . import scheme_service
from .scheme_submission import response_context, submit_response

INTENT = "scheme"
ACTION = "scheme_step"
KEYWORDS = ("填考核", "绩效填报", "考核填报", "按方案填报", "填绩效")


def button(label, session, **values):
    result = cards._button(label, ACTION, {
        "session_id": session.id, "generation": session.last_message_id, **values,
    })
    result["width"] = "fill"
    return result


def rotate(session):
    session.last_message_id = str(uuid4())


def start(db, session, employee, text):
    rows = db.execute(
        select(AssessmentRecord, AssessmentTask, ConfigSnapshot)
        .join(AssessmentTask, AssessmentTask.id == AssessmentRecord.task_id)
        .join(ConfigSnapshot, ConfigSnapshot.id == AssessmentTask.snapshot_id)
        .where(AssessmentRecord.employee_id == employee.id)
    ).all()
    options = [(r, t) for r, t, s in rows
               if (s.form_template_json or {}).get("scheme_version_id")
               and not r.submit_locked
               and r.status in ("not_started", "self_review_open", "returned")]
    session.intent = INTENT
    session.state = "collecting"
    session.collected = {}
    rotate(session)
    if not options:
        return cards.build_error_card("没有可填写的考核任务。请让HR发布方案并发起考核；已提交的记录不能重复填写。")
    if len(options) == 1:
        return choose(db, session, employee, options[0][0].id)
    return cards.build_card(title="选择考核任务", elements=[
        cards._markdown("请选择要填写的考核周期："),
        *[button(f"{t.period} · {t.name}", session, record_id=r.id, step="choose") for r, t in options],
    ])


def choose(db, session, employee, record_id):
    record, _, _, form, response = response_context(db, employee, record_id)
    if record.employee_id != employee.id:
        scheme_service.fail("只能填写本人的考核", 403)
    if record.submit_locked or record.status not in ("not_started", "self_review_open", "returned"):
        scheme_service.fail("该考核已提交或锁定", 409)
    session.collected = {"record_id": record.id, "revision": response.revision if response else 0,
                         "values": dict(response.values or {}) if response else {},
                         "metric_values": dict(response.metric_values or {}) if response else {}, "index": 0}
    rotate(session)
    return render(db, session, employee)


def inputs_for(payload, metric):
    specs = []
    code = metric["code"]
    for f in payload["fields"]:
        if f["metric_code"] == code and f["stage"] != "manager_review":
            specs.append({**f, "bucket": "values"})
    if metric["scoring_method"] == "ratio":
        for key, label in (("target", "目标值"), ("actual", "实际完成值")):
            specs.append({"key": key, "label": f"{label}（{metric.get('unit') or '数值'}）",
                          "type": "number", "required": True, "bucket": "metric_values"})
    if metric.get("evidence_required"):
        specs.append({"key": f"evidence_{code}", "label": "成果证据（链接或说明）",
                      "type": "textarea", "required": True, "bucket": "values"})
    return specs


def context(db, session, employee):
    data = session.collected
    record, _, _, form, response = response_context(db, employee, data["record_id"])
    if record.employee_id != employee.id:
        scheme_service.fail("只能填写本人的考核", 403)
    if record.submit_locked or record.status not in ("not_started", "self_review_open", "returned"):
        scheme_service.fail("该考核已提交或锁定", 409)
    if (response.revision if response else 0) != data["revision"]:
        scheme_service.fail("网页端填报已变化，请重新发送考核填报", 409)
    return data, form["scheme_payload"]


def render(db, session, employee):
    data, payload = context(db, session, employee)
    index = data["index"]
    metrics = payload["metrics"]
    if index == len(metrics):
        lines = ["请核对填报内容，确认后正式提交。提交后不能覆盖。"]
        for m in metrics:
            lines.append(f"\n**{m['name']}**")
            for spec in inputs_for(payload, m):
                source = data[spec["bucket"]]
                if spec["bucket"] == "metric_values":
                    source = source.get(m["code"], {})
                lines.append(f"{spec['label']}：{source.get(spec['key'], '')}")
        return cards.build_card(title="确认考核填报", elements=[cards._markdown("\n".join(lines)),
            button("确认并正式提交", session, step="submit"),
            button("返回修改", session, step="restart")])
    metric = metrics[index]
    score_label = f"满分 {metric['max_score']}分" if payload.get("scoring_mode") == "direct_points" else f"权重 {metric['weight']}%"
    elements = [cards._markdown(f"**{metric['name']}** · {score_label}\n"
                               f"{metric['scoring_description']}\n\n第 {index + 1} / {len(metrics)} 项")]
    fields = []
    for i, spec in enumerate(inputs_for(payload, metric)):
        source = data[spec["bucket"]]
        if spec["bucket"] == "metric_values":
            source = source.get(metric["code"], {})
        default = source.get(spec["key"], metric.get("target", "") if spec["key"] == "target" else "")
        fields.append({"tag": "input", "name": f"f{i}", "input_type": "multiline_text"
                       if spec["type"] in ("textarea", "attachment") else "text",
                       "label": cards._text(spec["label"] + (" · 必填" if spec.get("required") else "")),
                       "label_position": "top", "default_value": str(default if default is not None else ""),
                       "required": bool(spec.get("required"))})
    next_button = button("保存并继续", session, step="next")
    next_button.update(name="scheme_next", form_action_type="submit")
    fields.append(next_button)
    elements.append({"tag": "form", "name": "scheme_form", "elements": fields})
    elements.append(cards._markdown("当前内容保存在填表会话中，最后确认才会正式提交。附件字段请填写可访问的文件链接。"))
    return cards.build_card(title=f"{payload['name']} · {'量化填报' if payload['output_mode'] == 'quantitative' else '文字填报'}", elements=elements)


def handle(db, session, value, form):
    employee = db.get(Employee, session.employee_id)
    if not employee or employee.employment_status != "active":
        scheme_service.fail("员工账号不可用", 403)
    if session.intent != INTENT or session.state != "collecting":
        scheme_service.fail("本次填报已结束，请重新发送考核填报", 409)
    from .feishu_bot import is_session_expired
    if is_session_expired(session):
        scheme_service.fail("卡片已过期，请重新发送考核填报", 409)
    step = value.get("step")
    if step == "choose":
        return choose(db, session, employee, value.get("record_id"))
    data, payload = context(db, session, employee)
    index = data["index"]
    if step == "next" and index < len(payload["metrics"]):
        metric = payload["metrics"][index]
        # Replace the JSON object so SQLAlchemy persists every step.
        import copy
        data = copy.deepcopy(data)
        for i, spec in enumerate(inputs_for(payload, metric)):
            raw = form.get(f"f{i}", "")
            if not isinstance(raw, str):
                scheme_service.fail(f"{spec['label']}格式错误")
            raw = raw.strip()
            if spec.get("required") and not raw:
                scheme_service.fail(f"请填写{spec['label']}")
            if spec["type"] == "number" and raw:
                try:
                    parsed = scheme_service.number(raw, spec["label"])
                except ValueError as exc:
                    scheme_service.fail(str(exc))
                if spec["bucket"] == "metric_values" and (parsed < 0 or (spec["key"] == "target" and parsed <= 0)):
                    scheme_service.fail("目标值必须大于0，实际值不得为负")
            dest = data[spec["bucket"]]
            if spec["bucket"] == "metric_values":
                dest = dest.setdefault(metric["code"], {})
            dest[spec["key"]] = raw
        data["index"] += 1
        session.collected = data
    elif step == "restart":
        session.collected = {**data, "index": 0}
    elif step == "submit" and index == len(payload["metrics"]):
        submit_response(db, employee, data["record_id"], SimpleNamespace(
            values=data["values"], metric_values=data["metric_values"], reviews={}, revision=data["revision"]),
            source=SubmitSource.FEISHU_BOT)
        session.state = "completed"
        rotate(session)
        return cards.build_card(title="考核填报已提交", header_template="green", elements=[
            cards._markdown("已提交到绩效系统同一份考核记录，等待主管评价。可在网页端查看提交结果。")])
    else:
        scheme_service.fail("卡片动作已失效，请使用最新卡片", 409)
    rotate(session)
    return render(db, session, employee)
