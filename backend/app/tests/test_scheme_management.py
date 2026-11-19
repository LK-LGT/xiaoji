"""Acceptance checks for HR imports, immutable versions, ACL and frozen scopes."""

import copy
import io
import zipfile
from xml.sax.saxutils import escape

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    AssessmentTask,
    Base,
    ConfigSnapshot,
    Department,
    Employee,
    HrDepartmentConfig,
)
from backend.app.domain.scheme_models import SchemeImportBatch, SchemeVersion
from backend.app.routes.schemes import router
from backend.app.services import scheme_service as service


@pytest.fixture
def setup():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all(
        [
            Department(id="HR", name="人事"),
            Department(id="D", name="业务", leader_id="L"),
            Department(id="X", name="其他"),
        ]
    )
    db.flush()
    people = {
        k: Employee(id=k, name=k, department_id=d, position=p)
        for k, d, p in [
            ("H", "HR", "HR"),
            ("L", "D", "负责人"),
            ("E", "D", "销售"),
            ("X", "X", "销售"),
        ]
    }
    db.add_all(people.values())
    db.add(HrDepartmentConfig(department_id="HR", active=True))
    db.commit()
    app = FastAPI()
    app.include_router(router)

    def session():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: people["H"]
    yield db, people, app, TestClient(app)
    db.close()
    engine.dispose()


def payload(position=""):
    return {
        "code": "SALES",
        "name": "正式方案",
        "output_mode": "quantitative",
        "binding": {
            "department_id": "D",
            "position": position,
            "period_type": "monthly",
            "effective_from": "2027-01-01",
            "effective_to": "2027-12-31",
        },
        "metrics": [
            {
                "code": "KPI",
                "name": "正式指标",
                "type": "number",
                "weight": 100,
                "unit": "单",
                "scoring_method": "ratio",
                "direction": "higher_is_better",
                "scoring_description": "按HR确认的区间评分",
                "target": 10,
                "rules": [
                    {"level": "B", "min_ratio": 0, "max_ratio": 1, "score": 60},
                    {"level": "A", "min_ratio": 1, "max_ratio": None, "score": 100},
                ],
            }
        ],
        "grades": [
            {"level": "B", "min_score": 0, "max_score": 80},
            {"level": "A", "min_score": 80, "max_score": 100},
        ],
        "fields": [
            {
                "metric_code": "KPI",
                "key": "actual",
                "label": "实际值",
                "stage": "result",
                "type": "number",
                "required": True,
            }
        ],
    }


def excel(p):
    wb = load_workbook(io.BytesIO(service.workbook_template()))
    b, m = p["binding"], p["metrics"][0]
    row = {
        "scheme_code": p["code"],
        "scheme_name": p["name"],
        **b,
        "output_mode": p["output_mode"],
        "metric_code": m["code"],
        "metric_name": m["name"],
        "metric_type": m["type"],
        "weight": m["weight"],
        "unit": m["unit"],
        "scoring_method": m["scoring_method"],
        "direction": m["direction"],
        "scoring_description": m["scoring_description"],
        "target": m["target"],
    }
    wb["指标明细"].append([row.get(k) for k in service.COLUMNS])
    for r in m["rules"]:
        wb["评分区间"].append(
            [
                {"scheme_code": p["code"], "metric_code": m["code"], **r}.get(k)
                for k in service.RULE_COLUMNS
            ]
        )
    for g in p["grades"]:
        wb["等级规则"].append(
            [{"scheme_code": p["code"], **g}.get(k) for k in service.GRADE_COLUMNS]
        )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_import_preview_commit_idempotency_and_publish_lock(setup):
    db, people, app, client = setup
    content = excel(payload())
    response = client.post("/v1/performance/scheme-imports", files={"file": ("方案.xlsx", content)})
    assert response.status_code == 200, response.text
    batch = response.json()["data"]
    assert batch["status"] == "validated", batch
    assert db.query(SchemeVersion).count() == 0
    repeated = client.post(
        "/v1/performance/scheme-imports", files={"file": ("方案.xlsx", content)}
    ).json()["data"]
    assert repeated["id"] == batch["id"]
    path = f"/v1/performance/scheme-imports/{batch['id']}/commit"
    response = client.post(path, json={"revision": batch["revision"]})
    assert response.status_code == 200, response.text
    version = response.json()["data"][0]
    assert (
        client.post(path, json={"revision": batch["revision"]}).json()["data"][0]["id"]
        == version["id"]
    )
    assert db.query(SchemeVersion).count() == 1
    path = f"/v1/performance/scheme-versions/{version['id']}"
    assert client.post(path + "/publish").status_code == 200
    assert client.patch(path, json={"payload": payload(), "revision": 1}).status_code == 409
    assert client.post(path + "/publish").status_code == 409


def test_weight_nonfinite_gaps_and_unknown_department(setup):
    db, *_ = setup
    for field, value in [("weight", 90), ("weight", float("nan"))]:
        p = payload()
        p["metrics"][0][field] = value
        assert service.validate_payload(db, p)
    p = payload()
    p["metrics"][0]["rules"][1]["min_ratio"] = 1.1
    assert service.validate_payload(db, p)
    p = payload()
    p["binding"]["department_id"] = "missing"
    assert service.validate_payload(db, p)


def test_conflict_position_precedence_and_missing(setup):
    db, people, _, _ = setup
    generic = service.create_version(db, people["H"], payload())
    service.publish(db, people["H"], generic)
    exact = payload("销售")
    exact["code"] = "EXACT"
    exact = service.create_version(db, people["H"], exact)
    service.publish(db, people["H"], exact)
    assert service.resolve(db, people["E"], "202701").id == exact.id
    assert service.resolve(db, people["L"], "202701").id == generic.id
    with pytest.raises(HTTPException) as exc:
        service.resolve(db, people["X"], "202701")
    assert exc.value.status_code == 409
    clash = payload()
    clash["code"] = "CLASH"
    clash = service.create_version(db, people["H"], clash)
    with pytest.raises(HTTPException) as exc:
        service.publish(db, people["H"], clash)
    assert exc.value.status_code == 409


def test_department_acl_and_employee_no_library_access(setup):
    db, people, app, client = setup
    version = service.create_version(db, people["H"], payload())
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: people["L"]
    assert len(client.get("/v1/performance/schemes").json()["data"]) == 1
    assert client.post(f"/v1/performance/scheme-versions/{version.id}/publish").status_code == 403
    app.dependency_overrides[get_current_user] = lambda: people["X"]
    assert client.get("/v1/performance/schemes").status_code == 403
    assert client.get(f"/v1/performance/scheme-versions/{version.id}").status_code == 403


def test_task_snapshot_frozen_and_start_retry_rejected(setup):
    db, people, app, client = setup
    v = service.create_version(db, people["H"], payload())
    service.publish(db, people["H"], v)
    db.commit()
    request = {
        "title": "一月考核",
        "period": "202701",
        "employee_ids": ["E"],
        "self_review_deadline": "2027-02-01T12:00:00",
        "manager_review_deadline": "2027-02-05T12:00:00",
    }
    response = client.post("/v1/performance/scheme-tasks", json=request)
    assert response.status_code == 200, response.text
    task = db.get(AssessmentTask, response.json()["data"][0]["id"])
    snapshot = db.get(ConfigSnapshot, task.snapshot_id)
    old = copy.deepcopy(snapshot.form_template_json)
    new = service.create_version(db, people["H"], payload(), copied_from=v.id)
    changed = copy.deepcopy(new.payload)
    changed["metrics"][0]["name"] = "新指标"
    new.payload = changed
    people["E"].department_id = "X"
    db.commit()
    assert snapshot.form_template_json == old
    assert snapshot.form_template_json["employee_scope"][0]["department_id"] == "D"
    people["E"].department_id = "D"
    db.commit()
    assert client.post("/v1/performance/scheme-tasks", json=request).status_code == 409


def test_bad_import_does_not_create_versions_and_formula_blocked(setup):
    db, people, app, client = setup
    p = payload()
    p["metrics"][0]["weight"] = 90
    batch = client.post(
        "/v1/performance/scheme-imports", files={"file": ("bad.xlsx", excel(p))}
    ).json()["data"]
    assert batch["status"] == "invalid"
    assert (
        client.post(
            f"/v1/performance/scheme-imports/{batch['id']}/commit",
            json={"revision": batch["revision"]},
        ).status_code
        == 409
    )
    assert db.query(SchemeVersion).count() == 0
    wb = load_workbook(io.BytesIO(excel(payload())))
    wb["指标明细"]["L2"] = "=100"
    out = io.BytesIO()
    wb.save(out)
    batch = client.post(
        "/v1/performance/scheme-imports", files={"file": ("formula.xlsx", out.getvalue())}
    ).json()["data"]
    assert any("公式" in e["message"] for e in batch["preview"]["errors"])


def word_from_excel(content):
    wb = load_workbook(io.BytesIO(content))
    body = []
    for ws in wb.worksheets:
        if ws.title not in service.SHEETS:
            continue
        body.append(f"<w:p><w:r><w:t>{escape(ws.title)}</w:t></w:r></w:p>")
        body.append("<w:tbl>")
        for row in ws.iter_rows(values_only=True):
            body.append(
                "<w:tr>"
                + "".join(
                    f"<w:tc><w:p><w:r><w:t>{escape(str(value)) if value is not None else ''}</w:t></w:r></w:p></w:tc>"
                    for value in row
                )
                + "</w:tr>"
            )
        body.append("</w:tbl>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + "".join(body)
            + "</w:body></w:document>",
        )
    return out.getvalue()


def test_word_import_preview_and_commit(setup):
    db, _, _, client = setup
    response = client.post(
        "/v1/performance/scheme-imports",
        files={"file": ("正式方案.docx", word_from_excel(excel(payload())))},
    )
    assert response.status_code == 200, response.text
    batch = response.json()["data"]
    assert batch["status"] == "validated", batch
    assert (
        batch["preview"]["schemes"][0]["metrics"][0]["scoring_description"] == "按HR确认的区间评分"
    )
    assert (
        client.post(
            f"/v1/performance/scheme-imports/{batch['id']}/commit",
            json={"revision": batch["revision"]},
        ).status_code
        == 200
    )
    assert db.query(SchemeVersion).count() == 1


@pytest.mark.parametrize("word", [False, True])
def test_common_tables_aliases_percent_and_supplementary_scope(setup, word):
    _, _, _, client = setup
    wb = load_workbook(io.BytesIO(service.workbook_template()))
    ws = wb["指标明细"]
    ws.delete_rows(1)
    ws.append(["考核指标", "权重（%）", "评分标准"])
    ws.append(["交付质量", "100%", "由主管按正式标准评价"])
    ws.insert_rows(1)
    ws["A1"] = "年度正式考核方案"
    wb["等级规则"].append([None, "合格", 0, 100, "正式等级规则"])
    out = io.BytesIO()
    wb.save(out)
    content = word_from_excel(out.getvalue()) if word else out.getvalue()
    batch = client.post(
        "/v1/performance/scheme-imports",
        files={"file": ("正式方案.docx" if word else "正式方案.xlsx", content)},
    ).json()["data"]
    assert batch["status"] == "invalid"
    mapping = {
        "_defaults": {
            "department_id": "D",
            "period_type": "月度",
            "effective_from": "2027-01-01",
            "effective_to": "2027-12-31",
        }
    }
    response = client.put(
        f"/v1/performance/scheme-imports/{batch['id']}/mapping",
        json={"mapping": mapping, "revision": batch["revision"]},
    )
    assert response.status_code == 200
    batch = client.post(f"/v1/performance/scheme-imports/{batch['id']}/validate").json()["data"]
    assert batch["status"] == "validated", batch
    assert batch["preview"]["schemes"][0]["metrics"][0]["weight"] == 100


def test_empty_template_and_invalid_documents(setup):
    _, _, _, client = setup
    batch = client.post(
        "/v1/performance/scheme-imports",
        files={"file": ("空模板.xlsx", service.workbook_template())},
    ).json()["data"]
    assert batch["status"] == "invalid"
    assert any("仅含表头" in e["message"] for e in batch["preview"]["errors"])
    for filename in ("旧版.doc", "损坏.docx", "损坏.xlsx"):
        assert (
            client.post(
                "/v1/performance/scheme-imports", files={"file": (filename, b"invalid")}
            ).status_code
            == 422
        )


def test_mixed_scheme_fill_review_and_score(setup):
    from backend.app.domain.models import Score

    db, people, app, client = setup
    p = payload()
    p["metrics"][0]["weight"] = 60
    p["metrics"].append(
        {
            "code": "TEXT",
            "name": "工作成果",
            "type": "narrative",
            "weight": 40,
            "scoring_method": "manager_review",
            "scoring_description": "按HR标准由主管评价",
            "rules": [],
        }
    )
    p["fields"].append(
        {
            "metric_code": "TEXT",
            "key": "summary",
            "label": "成果说明",
            "stage": "result",
            "type": "textarea",
            "required": True,
        }
    )
    v = service.create_version(db, people["H"], p)
    service.publish(db, people["H"], v)
    db.commit()
    response = client.post(
        "/v1/performance/scheme-tasks",
        json={
            "title": "混合考核",
            "period": "202701",
            "employee_ids": ["E"],
            "self_review_deadline": "2027-02-01T12:00:00",
            "manager_review_deadline": "2027-02-05T12:00:00",
        },
    )
    assert response.status_code == 200, response.text
    record_id = client.get("/v1/performance/scheme-records").json()["data"][0]["id"]
    path = f"/v1/performance/scheme-records/{record_id}"
    app.dependency_overrides[get_current_user] = lambda: people["E"]
    assert client.get(path).status_code == 200
    request = {
        "revision": 0,
        "values": {"actual": "10", "summary": "完成交付"},
        "metric_values": {"KPI": {"actual": 10, "target": 10}},
    }
    response = client.put(path, json=request)
    assert response.status_code == 200, response.text
    assert client.put(path, json=request).status_code == 409
    app.dependency_overrides[get_current_user] = lambda: people["L"]
    response = client.put(
        path,
        json={"revision": 2, "reviews": {"TEXT": {"score": 80, "comment": "按正式标准验收通过"}}},
    )
    assert response.status_code == 200, response.text
    assert db.query(Score).filter_by(record_id=record_id).one().total == 92
    assert client.put(path, json={"revision": 3, "reviews": {}}).status_code == 409


def business_document():
    wb = load_workbook(io.BytesIO(service.workbook_template()))
    for name in list(wb.sheetnames):
        del wb[name]
    for name, points in (("交付", 60), ("协作", 40)):
        ws = wb.create_sheet(name)
        ws.append(["考核指标", "分值", "评分标准"])
        ws.append([name, f"{points}分", f"按{name}正式标准评定"])
    ws = wb.create_sheet("等级")
    ws.append(["等级", "总分区间", "结果应用"])
    ws.append(["S", ">100分", "优秀"])
    ws.append(["A", "80-100分", "合格"])
    ws = wb.create_sheet("加减分")
    ws.append(["类型", "情形", "加减分"])
    ws.append(["加分", "业务收益", "+5分"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_business_document_draft_publish_and_direct_points(setup):
    from backend.app.domain.models import Score
    from backend.app.services.review_service import compute_score

    db, people, app, client = setup
    batch = client.post(
        "/v1/performance/scheme-imports", files={"file": ("部门标准.xlsx", business_document())}
    ).json()["data"]
    assert batch["status"] == "validated"
    p = batch["preview"]["schemes"][0]
    assert len(p["metrics"]) == 2
    assert p["scoring_mode"] == "direct_points"
    assert len(p["document"]["grade_rules"]) == 2
    assert len(p["document"]["adjustment_rules"]) == 1
    assert len(p["document"]["blocks"]) == 4
    assert p["document"]["blocks"][0]["rows"][1][2] == "按交付正式标准评定"
    versions = client.post(
        f"/v1/performance/scheme-imports/{batch['id']}/commit", json={"revision": batch["revision"]}
    ).json()["data"]
    v = versions[0]
    path = f"/v1/performance/scheme-versions/{v['id']}"
    assert client.post(path + "/publish").status_code == 422
    p["binding"] = payload()["binding"]
    p["document"].update(
        review_confirmed=True,
        review_note="已核对公司正式原文",
        adjustment_min=-10,
        adjustment_max=10,
    )
    assert client.patch(path, json={"payload": p, "revision": 1}).status_code == 200
    assert client.post(path + "/publish").status_code == 200
    response = client.post(
        "/v1/performance/scheme-tasks",
        json={
            "title": "部门正式标准",
            "period": "202701",
            "employee_ids": ["E"],
            "self_review_deadline": "2027-02-01T12:00:00",
            "manager_review_deadline": "2027-02-05T12:00:00",
        },
    )
    assert response.status_code == 200, response.text
    record_id = client.get("/v1/performance/scheme-records").json()["data"][0]["id"]
    path = f"/v1/performance/scheme-records/{record_id}"
    app.dependency_overrides[get_current_user] = lambda: people["E"]
    values = {f["key"]: "已交付，依据附件" for f in p["fields"]}
    self_scores = {m['code']: m['max_score'] for m in p['metrics']}
    assert client.put(path, json={"revision": 0, "values": values, 'self_scores': self_scores}).status_code == 200
    assert client.get(path).json()['data']['self_scores'] == self_scores
    app.dependency_overrides[get_current_user] = lambda: people["L"]
    reviews = {
        m["code"]: {"score": m["max_score"], "comment": "符合原文标准"} for m in p["metrics"]
    }
    reviews.update(
        _adjustment={"score": 5, "comment": "业务收益佐证"},
        _grade={"level": "S", "comment": "105分符合原文S"},
    )
    response = client.put(path, json={"revision": 2, "reviews": reviews})
    assert response.status_code == 200, response.text
    score = db.query(Score).filter_by(record_id=record_id).one()
    assert score.total == 105
    assert score.trace_json["scheme_rating"] == "S"
    assert compute_score(db, record_id=record_id).total == 105
    workflow_path = path + '/workflow'
    def act(action, note='测试面谈及改进记录'):
        revision = client.get(path).json()['data']['revision']
        return client.post(workflow_path, json={'action': action, 'note': note, 'revision': revision})
    assert act('finalize').status_code == 403
    stale_revision = client.get(path).json()['data']['revision']
    assert act('interview').status_code == 200
    assert client.post(workflow_path, json={'action': 'interview', 'note': '重复提交', 'revision': stale_revision}).status_code == 409
    app.dependency_overrides[get_current_user] = lambda: people['E']
    assert act('finalize').status_code == 403
    assert act('dispute', '交付结果需重新核对').status_code == 200
    app.dependency_overrides[get_current_user] = lambda: people['H']
    assert act('finalize').status_code == 409
    assert act('reopen', '核查证据后重新评分').status_code == 200
    assert client.get(path).json()['data']['result'] is None
    app.dependency_overrides[get_current_user] = lambda: people['L']
    revision = client.get(path).json()['data']['revision']
    assert client.put(path, json={'revision': revision, 'reviews': reviews}).status_code == 200
    assert act('interview', '复核结果与员工面谈，记录改进目标').status_code == 200
    app.dependency_overrides[get_current_user] = lambda: people['E']
    assert act('acknowledge', '已知悉并确认复核结果').status_code == 200
    app.dependency_overrides[get_current_user] = lambda: people['H']
    assert act('finalize', 'HR核对证据并定稿').status_code == 200
    from backend.app.domain.models import AssessmentRecord
    assert db.get(AssessmentRecord, record_id).confirmed_rating == 'S'
    assert act('archive', '归档留存').status_code == 200
    assert act('reopen').status_code == 409
    assert client.get(path).json()['data']['result']['total'] == 105


def test_business_document_score_boundaries():
    from backend.app.services.scheme_document import parse_document
    from backend.app.services.scheme_points import score_points
    from backend.app.services.scoring import ScoringError

    p = parse_document(business_document(), "部门.xlsx", {})
    reviews = {m["code"]: {"score": m["max_score"]} for m in p["metrics"]}
    reviews["_adjustment"] = {"score": 1}
    with pytest.raises(ScoringError):
        score_points(p, reviews)
    reviews["_adjustment"] = {"score": 0}
    reviews["KPI_1"]["score"] = 61
    with pytest.raises(ScoringError):
        score_points(p, reviews)


def test_stale_draft_save_rejected(setup):
    db, people, app, client = setup
    v = service.create_version(db, people["H"], payload())
    db.commit()
    path = f"/v1/performance/scheme-versions/{v.id}"
    assert client.patch(path, json={"payload": payload(), "revision": 1}).status_code == 200
    assert client.patch(path, json={"payload": payload(), "revision": 1}).status_code == 409
