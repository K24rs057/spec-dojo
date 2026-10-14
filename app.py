"""仕様道場(spec-dojo)のエントリポイント。

`python app.py` でローカルWebアプリとして起動し、Chromeで画面を開く。
API の一覧は基本設計書 4 API一覧 と対応している(A1〜A15)。
"""
import json
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import documents
import prompts
import rules
from jobs import JobRunner
from llm import ClaudeCLI
from store import Store

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
OUTPUT = BASE / "output"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


# ---- リクエストの形 ----
class NewProject(BaseModel):
    name: str


class DecisionIn(BaseModel):
    value: str
    reason: str = ""
    source: str = ""


class LintIn(BaseModel):
    text: str


class JobIn(BaseModel):
    project_id: int
    kind: str  # question / advice / chat / review
    item_key: Optional[str] = None
    turn_id: Optional[int] = None
    selected: List[str] = []
    free_input: str = ""
    message: str = ""


class ExportIn(BaseModel):
    formats: List[str] = ["md", "xlsx"]
    zip: bool = False


def create_app(store, runner, output_dir=OUTPUT):
    app = FastAPI(title="spec-dojo")

    def project_or_404(pid):
        p = store.get_project(pid)
        if not p:
            raise HTTPException(404, "プロジェクトが見つかりません")
        return p

    def current_item(project, decs):
        if project["current_phase"] == "done":
            return None
        its = rules.items(project["current_phase"])
        for status in ("needs_check", "undecided"):
            for it in its:
                if decs[it["key"]]["status"] == status:
                    return it["key"]
        return None

    # A1〜A3
    @app.get("/api/projects")
    def list_projects(archived: int = 0):
        return store.list_projects(bool(archived))

    @app.post("/api/projects")
    def create_project(body: NewProject):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "作りたいアプリの名前を入れてください")
        return {"id": store.create_project(name)}

    @app.post("/api/projects/{pid}/archive")
    def archive(pid: int):
        project_or_404(pid)
        store.set_archived(pid, True)
        return {"ok": True}

    @app.post("/api/projects/{pid}/restore")
    def restore(pid: int):
        project_or_404(pid)
        store.set_archived(pid, False)
        return {"ok": True}

    # A4 作業画面の状態
    @app.get("/api/projects/{pid}")
    def project_state(pid: int):
        p = project_or_404(pid)
        decs = store.decisions(pid)
        phases = []
        for ph in rules.phases():
            phases.append({"key": ph["key"], "label": ph["label"], "items": [
                {"key": it["key"], "label": it["label"], "guide": it["guide"],
                 "depends_on": it.get("depends_on", []),
                 "status": decs[it["key"]]["status"], "value": decs[it["key"]]["value"],
                 "reason": decs[it["key"]]["reason"]} for it in ph["items"]]})
        cur = current_item(p, decs)
        chat = [{"role": r, "text": t} for turn in store.recent_turns(pid, 20) if turn["mode"] == "free"
                for r, t in (("user", turn["free_input"]), ("ai", turn["ai_text"]))]
        return {"project": p, "phases": phases, "current_item": cur,
                "unreviewed": len(store.unreviewed(pid)), "findings": store.findings(pid),
                "open_question": store.open_question(pid, cur) if cur else None,
                "chat": chat, "glossary": store.glossary(pid)}

    # A8 決定する/変更する/このままでいい
    @app.post("/api/projects/{pid}/decisions/{item_key}")
    def decide(pid: int, item_key: str, body: DecisionIn):
        project_or_404(pid)
        if not body.value.strip():
            raise HTTPException(400, "決定する内容が空です")
        try:
            marked = store.decide(pid, item_key, body.value.strip(), body.reason, body.source)
        except KeyError:
            raise HTTPException(404, "項目が見つかりません")
        return {"needs_check": marked}

    @app.post("/api/projects/{pid}/decisions/{item_key}/keep")
    def keep(pid: int, item_key: str):
        project_or_404(pid)
        store.keep(pid, item_key)
        return {"ok": True}

    # A9 曖昧語・秘密情報のチェック(AIなし)
    @app.post("/api/lint")
    def lint(body: LintIn):
        return rules.lint(body.text)

    # A11 工程を完了する
    @app.post("/api/projects/{pid}/phases/{phase}/complete")
    def complete_phase(pid: int, phase: str):
        p = project_or_404(pid)
        if p["current_phase"] != phase:
            raise HTTPException(409, "今の工程ではありません")
        decs = store.decisions(pid)
        its = rules.items(phase)
        if any(decs[it["key"]]["status"] == "needs_check" for it in its):
            raise HTTPException(409, "⚠ 要確認の項目があるので、まだ完了できません")
        left = [it["label"] for it in its if decs[it["key"]]["status"] == "undecided"]
        if left:
            raise HTTPException(409, f"未決定の項目が残っています:{'、'.join(left)}")
        keys = rules.phase_keys()
        nxt = keys[keys.index(phase) + 1] if keys.index(phase) + 1 < len(keys) else "done"
        store.set_phase(pid, nxt)
        return {"next_phase": nxt}

    # A12 資料のプレビュー
    @app.get("/api/projects/{pid}/documents/{doc_type}")
    def document(pid: int, doc_type: str):
        p = project_or_404(pid)
        if doc_type not in documents.DOC_TYPES:
            raise HTTPException(404, "資料の種類が違います")
        return documents.preview(store, p, doc_type)

    # A13 出力する/フォルダを開く
    @app.post("/api/projects/{pid}/exports")
    def export(pid: int, body: ExportIn):
        p = project_or_404(pid)
        folder, results = documents.export(store, p, output_dir, tuple(body.formats))
        out = {"folder": str(folder), "results": results}
        if body.zip:
            documents.make_zip(folder)
            out["zip_url"] = f"/api/projects/{pid}/exports/zip"
        return out

    @app.get("/api/projects/{pid}/exports/zip")
    def export_zip(pid: int):
        p = project_or_404(pid)
        z = Path(output_dir) / f"{p['id']}_{documents.safe_name(p['name'])}.zip"
        if not z.exists():
            raise HTTPException(404, "先に出力してください")
        return FileResponse(z, filename=z.name, media_type="application/zip")

    @app.post("/api/projects/{pid}/open-folder")
    def open_folder(pid: int):
        p = project_or_404(pid)
        folder = Path(output_dir) / f"{p['id']}_{documents.safe_name(p['name'])}"
        if not folder.exists():
            raise HTTPException(404, "先に出力してください")
        if hasattr(os, "startfile"):
            os.startfile(folder)  # noqa: S606 自分のPCのフォルダを開くだけ
        return {"folder": str(folder)}

    @app.post("/api/findings/{fid}/resolve")
    def resolve(fid: int):
        store.resolve_finding(fid)
        return {"ok": True}

    # A14 AIの仕事を登録する
    @app.post("/api/jobs")
    def create_job(body: JobIn):
        p = project_or_404(body.project_id)
        pid = p["id"]
        if rules.contains_secret(body.free_input + body.message):
            raise HTTPException(400, "パスワードやAPIキーのような文字列があります。消してから送ってください")
        decs, gloss = store.decisions(pid), store.glossary(pid)

        if body.kind == "question":
            if not body.item_key:
                raise HTTPException(400, "項目を指定してください")
            open_q = store.open_question(pid, body.item_key)
            if open_q:  # 答えていない質問があれば作り直さない
                return {"turn": open_q}

            def done(text, q):
                store.add_terms(pid, [t.model_dump() for t in q.terms])
                tid = store.add_turn(pid, body.item_key, "guided", text, q.model_dump())
                return {"turn": store.turn(tid)}
            job = runner.submit("question", prompts.question_prompt(p, decs, gloss, body.item_key),
                                prompts.QuestionOut, done)
            return {"job_id": job.id}

        if body.kind == "advice":
            turn = store.turn(body.turn_id or 0)
            if not turn or not turn["question"]:
                raise HTTPException(404, "質問が見つかりません")
            q = turn["question"]
            if not body.selected and not body.free_input.strip():
                raise HTTPException(400, "選択肢を選ぶか、自由に書いてください")
            if len(body.selected) == 1 and not body.free_input.strip():
                # 先回りして作っておいたアドバイスを、待ち時間なしで返す
                opt = next((o for o in q["options"] if o["id"] == body.selected[0]), None)
                if not opt:
                    raise HTTPException(400, "選択肢が見つかりません")
                store.answer_turn(turn["id"], body.selected, "", opt["advice"])
                return {"advice": {"text": opt["advice"], "value": opt["label"], "reason": "",
                                   "contradiction": "", "recommended": q.get("recommended", [])}}

            def done(text, a):
                store.answer_turn(turn["id"], body.selected, body.free_input, text)
                return {"advice": {"text": text, "value": a.value, "reason": a.reason,
                                   "contradiction": a.contradiction, "recommended": q.get("recommended", [])}}
            job = runner.submit("advice", prompts.advice_prompt(p, decs, gloss, turn["item_key"], q,
                                                                body.selected, body.free_input),
                                prompts.AdviceOut, done)
            return {"job_id": job.id}

        if body.kind == "chat":
            if not body.message.strip():
                raise HTTPException(400, "何か書いてから送ってください")
            phase = p["current_phase"] if p["current_phase"] != "done" else rules.phase_keys()[-1]
            history = [{"role": r, "text": t} for turn in store.recent_turns(pid, 8) if turn["mode"] == "free"
                       for r, t in (("user", turn["free_input"]), ("ai", turn["ai_text"]))]

            def done(text, c):
                tid = store.add_turn(pid, None, "free", text)
                store.answer_turn(tid, [], body.message, None)
                return {"reply": text, "candidates": [x.model_dump() for x in c.candidates]}
            job = runner.submit("chat", prompts.chat_prompt(p, decs, gloss, history, body.message, phase),
                                prompts.ChatOut, done)
            return {"job_id": job.id}

        if body.kind == "review":
            targets = store.unreviewed(pid)
            if not targets:
                raise HTTPException(400, "未レビューの決定はありません")
            keys = [d["item_key"] for d in targets]
            # ①曖昧語辞書:AIを使わずにその場で照合する
            first = [{"item_key": d["item_key"], "layer": 1, "viewpoint_id": None, "message": w["message"]}
                     for d in targets for w in rules.lint(d["value"] or "") if w["kind"] == "vague"]

            def done(text, r):
                ai = [{"item_key": f.item_key, "layer": 3 if f.viewpoint_id == "V-07" else 2,
                       "viewpoint_id": f.viewpoint_id, "message": f.message}
                      for f in r.findings if f.item_key in decs]
                store.add_findings(pid, first + ai)
                store.mark_reviewed(pid, keys)
                return {"summary": text, "findings": store.findings(pid)}
            job = runner.submit("review", prompts.review_prompt(p, decs, gloss, keys), prompts.ReviewOut, done)
            return {"job_id": job.id}

        raise HTTPException(400, "仕事の種類が違います")

    # A15 仕事の文字と結果を受け取る(SSE)
    @app.get("/api/jobs/{job_id}/stream")
    def stream(job_id: str, request: Request):
        job = runner.get(job_id)
        if not job:
            raise HTTPException(404, "仕事が見つかりません(サーバーを再起動した可能性があります)")
        after = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)

        def gen():
            nonlocal after
            yield "retry: 1000\n\n"
            while True:
                events, done = job.wait_events(after, timeout=10)
                for seq, kind, data in events:
                    after = seq
                    yield f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if done and len(job.events) <= after:
                    return
                if not events:
                    yield ": keep-alive\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")
    return app


def free_port(start=8795):
    for port in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def open_browser(url):
    """Chromeで開く。見つからなければ既定のブラウザで開く。"""
    for path in CHROME_PATHS:
        if os.path.exists(path):
            try:
                subprocess.Popen([path, url])
                return
            except OSError:
                break
    webbrowser.open(url)


def main():
    import uvicorn
    if sys.stdout is None:
        # pythonw で起動すると出力先がなく、uvicorn のログ設定で落ちる。data/server.log に書く
        DATA.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(DATA / "server.log", "a", encoding="utf-8", buffering=1)
    store = Store(DATA / "spec-dojo.db")
    app = create_app(store, JobRunner(ClaudeCLI(DATA / "ai-workdir")))
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, open_browser, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
