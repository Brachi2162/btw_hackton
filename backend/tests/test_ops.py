"""Endpoints around review: propagation, evidence, tubes, follow-ups,
reports, handover, assistant, demo data. Synthetic values only."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, text

from tests.test_api import FakeReader, upload, wait_done
from vera.batch import Readers, page_quality
from vera.config import get_settings
from vera.db import FollowupRow, now
from vera.demo import seed


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    monkeypatch.setenv("CPU_WORKERS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    from vera.api.main import app

    with TestClient(app) as c:
        svc = app.state.batches
        svc.dpi, svc.orient = 60, False
        svc._readers = Readers(FakeReader("m1"), FakeReader("m2"), "prompt")
        yield c, app
    get_settings.cache_clear()


@pytest.fixture
def demo(app_client):
    c, app = app_client
    totals = seed(app.state.batches, weeks=1)
    today = c.get("/api/overview").json()["latest_batch_id"]
    return c, app, today, totals


def subs_of(c, bid):
    return c.get(f"/api/batches/{bid}").json()["submissions"]


# --- one fix that fixes everything ---


def test_confirming_a_date_offers_it_to_the_same_stamp(demo):
    c, app, bid, _ = demo
    likely = [
        s for s in subs_of(c, bid) if s["fields"]["receipt_date"]["status"] == "likely"
    ]
    assert len(likely) >= 3
    first = likely[0]
    r = c.post(f"/api/submissions/{first['id']}/fields/receipt_date/accept")
    assert r.status_code == 200
    prop = r.json()["propagation"]
    assert prop["field"] == "receipt_date"
    assert prop["count"] == len(likely) - 1 == len(prop["submission_ids"])
    assert first["id"] not in prop["submission_ids"]

    applied = c.post(
        f"/api/batches/{bid}/propagate",
        json={
            "field": "receipt_date",
            "value": prop["value"],
            "submission_ids": prop["submission_ids"],
        },
        headers={"X-User": "dina"},
    ).json()
    assert len(applied["applied"]) == prop["count"] and not applied["skipped"]
    after = c.get(f"/api/submissions/{prop['submission_ids'][0]}").json()
    fr = after["fields"]["receipt_date"]
    assert fr["status"] == "human" and fr["history"][-1]["user"] == "dina"
    # already applied: nothing left to do
    again = c.post(
        f"/api/batches/{bid}/propagate",
        json={
            "field": "receipt_date",
            "value": prop["value"],
            "submission_ids": prop["submission_ids"],
        },
    ).json()
    assert again["applied"] == [] and len(again["skipped"]) == prop["count"]


def test_no_propagation_for_other_fields(demo):
    c, _, bid, _ = demo
    sid = subs_of(c, bid)[0]["id"]
    r = c.post(f"/api/submissions/{sid}/fields/first_name/accept")
    assert r.json()["propagation"] is None


def test_fixing_a_misread_sender_offers_the_same_fix(demo):
    c, _, bid, _ = demo
    subs = subs_of(c, bid)
    by_sender = {}
    for x in subs:
        f = x["fields"]["sender"]
        if f["status"] != "human" and len(f["values"]) == 1:
            by_sender.setdefault(f["values"][0], []).append(x["id"])
    wrong, ids = max(by_sender.items(), key=lambda kv: len(kv[1]))
    assert len(ids) >= 2
    right = "מרכז רפואי רבין" if wrong != "מרכז רפואי רבין" else "מרכז רפואי מאיר"
    r = c.patch(f"/api/submissions/{ids[0]}/fields/sender", json={"values": [right]})
    prop = r.json()["propagation"]
    assert prop["kind"] == "fix" and prop["from_values"] == [wrong]
    assert prop["value"] == right and sorted(prop["submission_ids"]) == sorted(ids[1:])

    applied = c.post(
        f"/api/batches/{bid}/propagate",
        json={
            "field": "sender",
            "value": right,
            "from_values": [wrong],
            "submission_ids": prop["submission_ids"],
        },
    ).json()
    assert sorted(applied["applied"]) == sorted(ids[1:])
    fr = c.get(f"/api/submissions/{ids[1]}").json()["fields"]["sender"]
    assert fr["values"] == [right] and fr["status"] == "human"
    assert "אותה טעות" in fr["explanation_he"]


def test_a_name_fix_is_never_offered_to_other_patients(demo):
    c, _, bid, _ = demo
    sid = subs_of(c, bid)[0]["id"]
    r = c.patch(f"/api/submissions/{sid}/fields/first_name", json={"values": ["דנה"]})
    assert r.json()["propagation"] is None


def _same_sender(c, bid, n=3):
    groups = {}
    for x in subs_of(c, bid):
        f = x["fields"]["sender"]
        if f["status"] != "human" and len(f["values"]) == 1:
            groups.setdefault(f["values"][0], []).append(x["id"])
    sender, ids = max(groups.items(), key=lambda kv: len(kv[1]))
    assert len(ids) >= n
    return sender, ids


def test_a_fix_made_twice_is_offered_first_next_time(demo):
    c, _, bid, _ = demo
    wrong, ids = _same_sender(c, bid)
    right = "מרכז רפואי רבין" if wrong != "מרכז רפואי רבין" else "מרכז רפואי מאיר"
    ev = c.get(f"/api/submissions/{ids[2]}/fields/sender/evidence").json()
    assert not any("בעבר" in r for o in ev["options"] for r in o["readers"])
    for sid in ids[:2]:
        c.patch(f"/api/submissions/{sid}/fields/sender", json={"values": [right]})
    ev = c.get(f"/api/submissions/{ids[2]}/fields/sender/evidence").json()
    first = ev["options"][0]
    assert first["value"] == right and "תוקן כך 2 פעמים בעבר" in first["readers"]
    # a suggestion only: the field itself is untouched
    assert c.get(f"/api/submissions/{ids[2]}").json()["fields"]["sender"]["values"] == [
        wrong
    ]


def test_a_marked_location_is_exact_for_this_form_only(demo):
    c, _, bid, _ = demo
    _, ids = _same_sender(c, bid, n=2)
    sub = c.get(f"/api/submissions/{ids[0]}").json()
    page = sub["page_ids"][0]
    r = c.put(
        f"/api/submissions/{ids[0]}/fields/commitment_numbers/location",
        json={"page_id": page, "bbox": [100, 200, 400, 260]},
        headers={"X-User": "dina"},
    )
    assert r.status_code == 200
    assert r.json()["crop"]["approx"] is False
    assert r.json()["crop"]["bbox"] == [76, 176, 424, 284]  # with padding
    human = [s for s in r.json()["sources"] if s["reader"] == "human"]
    assert human and "dina" in human[0]["quote"]
    # another form of the same sender is untouched: layouts differ
    other = c.get(
        f"/api/submissions/{ids[1]}/fields/commitment_numbers/evidence"
    ).json()
    assert not any(x["reader"] == "human" for x in other["sources"])
    bad = c.put(
        f"/api/submissions/{ids[0]}/fields/commitment_numbers/location",
        json={"page_id": "not-this-form", "bbox": [1, 1, 50, 50]},
    )
    assert bad.status_code == 422


# --- evidence ---


def test_evidence_explains_a_bad_id(demo):
    c, _, bid, _ = demo
    bad = None
    for s in subs_of(c, bid):
        full = c.get(f"/api/submissions/{s['id']}").json()
        if "checksum_failed" in full["fields"]["id_number"]["reasons"]:
            bad = s["id"]
            break
    if bad is None:
        pytest.skip("seed produced no invalid ID")
    ev = c.get(f"/api/submissions/{bad}/fields/id_number/evidence").json()
    check = next(x for x in ev["checks"] if x["label"] == "ספרת ביקורת תקינה")
    assert check["ok"] is False and "ספרת הביקורת" in check["detail"]
    assert ev["status"] == "review" and ev["decision_he"].startswith("לבדיקה")
    assert ev["sources"][0]["reader_he"] == "מודל ראייה"
    assert ev["crop"]["bbox"][2] > ev["crop"]["bbox"][0]
    assert ev["options"][0]["value"] == ev["values"][0]


def test_evidence_404s(demo):
    c, _, bid, _ = demo
    sid = subs_of(c, bid)[0]["id"]
    assert c.get(f"/api/submissions/{sid}/fields/nope/evidence").status_code == 404
    assert c.get("/api/submissions/nope/fields/id_number/evidence").status_code == 404


# --- tubes ---


def test_tube_scan_matches_slots_and_orphans(demo):
    c, _, bid, _ = demo
    st = c.get(f"/api/batches/{bid}/tubes").json()
    assert len(st["slots"]) == 30
    used = sum(s["state"] != "empty" for s in st["slots"])
    waiting = next(w for w in st["waiting"] if w["central"])

    r = c.post(f"/api/batches/{bid}/tubes/scan", json={"number": waiting["central"]})
    hit = r.json()
    assert hit["matched"] and hit["submission"]["id"] == waiting["id"]
    assert (
        hit["slot"] == used and hit["label"] == f"{'ABCDEF'[used % 6]}{used // 6 + 1}"
    )

    dup = c.post(f"/api/batches/{bid}/tubes/scan", json={"number": waiting["central"]})
    assert dup.json()["duplicate"] and dup.json()["slot"] == hit["slot"]

    orphan = c.post(f"/api/batches/{bid}/tubes/scan", json={"number": "0999111"}).json()
    assert not orphan["matched"]
    st = c.get(f"/api/batches/{bid}/tubes").json()
    newest = [s for s in st["slots"] if s["newest"]]
    assert len(newest) == 1 and newest[0]["state"] == "orphan"
    assert waiting["id"] not in {w["id"] for w in st["waiting"]}
    fus = c.get("/api/followups?status=open").json()
    assert any(f["kind"] == "tube_orphan" and "999111" in f["title_he"] for f in fus)

    assert (
        c.post(f"/api/batches/{bid}/tubes/scan", json={"number": " - "}).status_code
        == 422
    )
    assert (
        c.post("/api/batches/nope/tubes/scan", json={"number": "1"}).status_code == 404
    )


# --- follow-ups ---


def test_followups_open_only_when_the_review_is_done(app_client, tmp_path):
    c, _ = app_client
    bid = c.post("/api/batches", json={"name": "t", "ref_date": "2026-09-01"}).json()
    upload(c, bid["id"], tmp_path)
    subs = wait_done(c, bid["id"])["submissions"]
    assert c.get("/api/followups").json() == []  # nothing while fields wait

    first = subs[0]["id"]
    # the form doesn't say: the secretary leaves a field empty, it goes grey
    r = c.patch(f"/api/submissions/{first}/fields/first_name", json={"values": []})
    assert r.json()["fields"]["first_name"]["status"] == "missing"
    assert c.get("/api/followups").json() == []  # other fields still wait

    todo = [
        (x["id"], f)
        for x in subs
        for f, v in x["fields"].items()
        if v["status"] in ("likely", "review")
    ]
    assert todo
    for sid, f in todo:
        assert c.get("/api/followups").json() == []  # still going over the table
        url = f"/api/submissions/{sid}/fields/{f}"
        if f == "id_number":
            # kept as written, though its check digit is wrong
            ids = c.get(f"/api/submissions/{sid}").json()["fields"][f]["values"]
            c.patch(url, json={"values": ids, "force": True})
        else:
            c.post(f"{url}/accept")

    fus = c.get("/api/followups").json()
    kinds = sorted((f["kind"], f["submission_id"] == first) for f in fus)
    # every form: the invalid ID; the first form: its empty first name too
    assert ("missing_field", True) in kinds
    assert sum(k == "invalid_id" for k, _ in kinds) == len(subs)
    assert all(f["status"] == "open" for f in fus)


def test_followup_overdue_patch_and_draft(app_client):
    c, app = app_client
    with app.state.batches.sv.repo.session() as s:
        s.add(
            FollowupRow(
                kind="tube_orphan",
                title_he="מבחנה 123 הגיעה בלי טופס",
                created_at=now() - timedelta(hours=30),
            )
        )
        s.commit()
    f = c.get("/api/followups").json()[0]
    assert f["overdue"] and f["age_hours"] >= 30

    d = c.post(f"/api/followups/{f['id']}/draft").json()
    assert d["source"] == "template" and d["sent"] is False
    assert "מבחנה 123" in d["draft"]

    closed = c.patch(f"/api/followups/{f['id']}", json={"status": "closed"}).json()
    assert closed["status"] == "closed" and not closed["overdue"]
    assert closed["draft"] == d["draft"]
    bad = c.patch(f"/api/followups/{f['id']}", json={"status": "lost"})
    assert bad.status_code == 422
    assert c.patch("/api/followups/999", json={"status": "open"}).status_code == 404


# --- reports ---


def test_weekly_report(demo):
    c, _, _, totals = demo
    r = c.get("/api/reports/weekly").json()
    st = r["stats"]
    assert r["has_demo"] and st["forms"] > 0 and st["hours_saved"] > 0
    assert 0 <= st["auto_approved_pct"] <= 100
    assert len(r["orphans_by_day"]) == 7
    pcts = [x["needs_eye_pct"] for x in r["by_sender"]]
    assert pcts == sorted(pcts, reverse=True)
    assert r["takeaways"]["senders"] and r["takeaways"]["orphans"]
    no_demo = c.get("/api/reports/weekly?demo=false").json()
    assert no_demo["stats"]["forms"] == 0 and not no_demo["has_demo"]

    worst = r["worst_sender"]
    letter = c.post("/api/reports/letter", json={"sender": worst}).json()
    assert letter["text"].startswith(f"לכבוד {worst}")
    assert c.post("/api/reports/letter", json={"sender": "אין כזה"}).status_code == 404


# --- handover ---


def test_handover_note_and_ack(demo):
    c, _, _, _ = demo
    h = c.get("/api/handover").json()
    assert h["pending"]["review_waiting"] > 0 and h["open_handover"] is None
    assert c.post("/api/handover/note", json={"text": "  "}).status_code == 422
    n = c.post(
        "/api/handover/note",
        json={"text": "שתי מבחנות במקרר 2"},
        headers={"X-User": "ruti"},
    ).json()
    assert n["summary"]["review_waiting"] == h["pending"]["review_waiting"]
    assert c.get("/api/handover").json()["open_handover"]["id"] == n["id"]
    acked = c.post(f"/api/handover/{n['id']}/ack", headers={"X-User": "dina"}).json()
    assert acked["acked_by"] == "dina"
    assert c.get("/api/handover").json()["open_handover"] is None
    assert c.post("/api/handover/999/ack").status_code == 404


# --- assistant ---


def test_assistant_offline_answers_from_tools(demo):
    c, _, _, _ = demo
    r = c.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "אילו מבחנות הגיעו בלי טופס?"}]},
    ).json()
    assert r["mode"] == "offline" and r["tools_used"] == ["tubes_status"]
    assert "בלי טופס" in r["reply"] and r["suggestions"]
    bad = c.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "assistant", "content": "x"}]},
    )
    assert bad.status_code == 422


class FakeClient:
    """Turn 1: read the week and propose closing a follow-up. Turn 2: text."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use", id="t1", name="weekly_summary", input={}
                    ),
                    SimpleNamespace(
                        type="tool_use",
                        id="t2",
                        name="update_followup",
                        input={"followup_id": 1, "status": "closed"},
                    ),
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[
                SimpleNamespace(type="text", text="השבוע היה טוב. הכנתי סגירה לאישור.")
            ],
        )


def test_assistant_tools_and_confirmation(demo):
    c, app, _, _ = demo
    fake = FakeClient()
    app.state.assistant._client = fake
    r = c.post(
        "/api/assistant/chat",
        json={
            "messages": [{"role": "user", "content": "איך היה השבוע? וסגרי את בירור 1"}]
        },
    ).json()
    assert r["mode"] == "model" and r["reply"].startswith("השבוע")
    assert r["tools_used"] == ["weekly_summary", "update_followup"]
    assert r["actions"][0]["tool"] == "update_followup"
    assert "בירור #1" in r["actions"][0]["summary_he"]
    # the write tool did not act: the model was told it waits for the user
    results = fake.calls[1]["messages"][-1]["content"]
    assert '"pending_confirmation": true' in results[1]["content"]
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"

    fid = c.get("/api/followups?status=open").json()[0]["id"]
    ok = c.post(
        "/api/assistant/confirm",
        json={
            "tool": "update_followup",
            "args": {"followup_id": fid, "status": "closed"},
        },
    ).json()
    assert ok["ok"] and ok["result"]["status"] == "closed"
    nope = c.post("/api/assistant/confirm", json={"tool": "rm_rf", "args": {}})
    assert nope.status_code == 404


# --- demo data, intake progress, quality, schema ---


def test_demo_seed_and_clear(demo):
    c, app, bid, totals = demo
    assert totals["batches"] >= 4 and c.get("/api/overview").json()["has_demo"]
    b = c.get(f"/api/batches/{bid}").json()
    assert b["demo"] and b["documents"][0]["done"] == len(b["submissions"])
    pid = b["submissions"][0]["page_ids"][0]
    assert c.get(f"/api/pages/{pid}/image").status_code == 200
    from vera.demo import clear

    assert clear(app.state.batches)["batches_removed"] == totals["batches"]
    assert c.get("/api/overview").json()["latest_batch_id"] is None
    assert c.get("/api/followups").json() == []


def test_page_quality_flags_blur(tmp_path):
    flat = tmp_path / "flat.png"
    Image.new("L", (1200, 1600), 255).save(flat)
    assert page_quality(flat, 40)["warnings"] == ["blur"]
    sharp = tmp_path / "sharp.png"
    img = Image.new("L", (1200, 1600), 255)
    d = ImageDraw.Draw(img)
    for y in range(0, 1600, 12):
        d.line((0, y, 1200, y), fill=0, width=2)
    img.save(sharp)
    assert page_quality(sharp, 40)["warnings"] == []


def test_old_database_gets_new_columns(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE batches (id VARCHAR PRIMARY KEY, name VARCHAR, "
                "created_at DATETIME, ref_date DATE, status VARCHAR, "
                "locked BOOLEAN, exported_at DATETIME)"
            )
        )
    eng.dispose()
    from vera.repo import Repository

    repo = Repository(url)
    repo.init_schema()
    with repo.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(batches)"))}
    repo.close()
    assert "demo" in cols


class FakeGemini:
    """Gemini REST: queued (status, body) answers, in order."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def post(self, url, headers, json):
        self.calls.append({"url": url, "json": json})
        status, body = self.answers.pop(0)
        return SimpleNamespace(
            status_code=status, json=lambda: body, text=str(body)[:300]
        )


def gemini_parts(*parts):
    return (200, {"candidates": [{"content": {"parts": list(parts)}}]})


def test_assistant_asks_gemini_first_with_tools(demo):
    c, app, _, _ = demo
    fake = FakeGemini(
        gemini_parts(
            {
                "functionCall": {"name": "work_overview", "args": {}},
                "thoughtSignature": "s",
            }
        ),
        gemini_parts(
            {"text": "חושבת...", "thought": True}, {"text": "יש 3 שדות לבדיקה."}
        ),
    )
    app.state.assistant._http = fake
    r = c.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "מה מחכה?"}]},
    ).json()
    assert r["mode"] == "model" and r["reply"] == "יש 3 שדות לבדיקה."
    assert r["tools_used"] == ["work_overview"]
    assert "gemini-3.5-flash:generateContent" in fake.calls[0]["url"]
    tools = fake.calls[0]["json"]["tools"][0]["functionDeclarations"]
    assert {"work_overview", "correct_field"} <= {t["name"] for t in tools}
    second = fake.calls[1]["json"]["contents"]
    assert second[1]["parts"][0]["thoughtSignature"] == "s"  # sent back as it came
    answer = second[2]["parts"][0]["functionResponse"]
    assert answer["name"] == "work_overview" and "review_waiting" in answer["response"]


def test_assistant_falls_back_to_claude_then_to_the_data(demo):
    c, app, _, _ = demo
    ask = {"messages": [{"role": "user", "content": "איך היה השבוע?"}]}
    app.state.assistant._http = FakeGemini((429, {"error": {"message": "quota"}}))
    app.state.assistant._client = FakeClient()
    r = c.post("/api/assistant/chat", json=ask).json()
    assert r["mode"] == "model" and r["reply"].startswith("השבוע")

    broken = SimpleNamespace(
        beta=SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: 1 / 0)  # any failure
        )
    )
    app.state.assistant._http = FakeGemini((503, {}))
    app.state.assistant._client = broken
    r = c.post("/api/assistant/chat", json=ask)
    assert r.status_code == 200 and r.json()["mode"] == "offline"


def test_a_model_out_of_quota_is_skipped_on_the_next_questions(demo):
    c, app, _, _ = demo
    ask = {"messages": [{"role": "user", "content": "איך היה השבוע?"}]}
    gemini = FakeGemini((429, {"error": {"message": "quota"}}))
    app.state.assistant._http = gemini
    app.state.assistant._client = FakeClient()
    assert c.post("/api/assistant/chat", json=ask).json()["mode"] == "model"
    assert c.post("/api/assistant/chat", json=ask).json()["mode"] == "model"
    assert len(gemini.calls) == 1  # the second question went straight to Claude
