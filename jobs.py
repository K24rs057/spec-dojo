"""AIの仕事(ジョブ)を裏で動かす。

画面は POST で仕事を登録し、GET の SSE で文字と結果を受け取る。
接続が切れても仕事は最後まで続き、結果は保存される(基本設計書 4 API一覧 A14・A15)。
"""
import itertools
import json
import threading

from pydantic import ValidationError

from llm import LLMError
from prompts import MARKER


class Job:
    def __init__(self, job_id, kind):
        self.id = job_id
        self.kind = kind
        self.events = []  # (連番, 種類, データ)
        self.done = False
        self.cond = threading.Condition()

    def emit(self, kind, data):
        with self.cond:
            self.events.append((len(self.events) + 1, kind, data))
            if kind in ("result", "error"):
                self.done = True
            self.cond.notify_all()

    def wait_events(self, after, timeout=1.0):
        with self.cond:
            if len(self.events) <= after and not self.done:
                self.cond.wait(timeout)
            return self.events[after:], self.done


class Splitter:
    """流れてくる文字を、区切り行の前(説明文)と後(JSON)に分ける。

    区切り行が2つの断片にまたがって届いても取りこぼさないように、
    区切り行の長さぶんは画面に出さずに手元に残しておく。
    """

    def __init__(self):
        self.buf = ""
        self.sent = 0
        self.json_part = None

    def feed(self, chunk):
        if self.json_part is not None:
            self.json_part += chunk
            return ""
        self.buf += chunk
        i = self.buf.find(MARKER)
        if i >= 0:
            out = self.buf[self.sent:i]
            self.json_part = self.buf[i + len(MARKER):]
            self.sent = i
            return out
        safe = max(self.sent, len(self.buf) - len(MARKER))
        out = self.buf[self.sent:safe]
        self.sent = safe
        return out

    def text(self):
        end = self.buf.find(MARKER)
        return (self.buf if end < 0 else self.buf[:end]).strip()

    def flush(self):
        """区切り行が来ないまま終わったときに、手元に残した文字を出す。"""
        if self.json_part is None:
            out = self.buf[self.sent:]
            self.sent = len(self.buf)
            return out
        return ""


def parse_json(raw):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("JSONが見つかりません")
    return json.loads(raw[start:end + 1])


class JobRunner:
    def __init__(self, llm):
        self.llm = llm
        self.jobs = {}
        self.seq = itertools.count(1)
        self.lock = threading.Lock()

    def submit(self, kind, prompt, model, on_done, sync=False):
        """prompt で AI を呼び、JSON を model で検証し、on_done(text, parsed) の戻り値を結果として返す。

        形が違えば1回だけ自動でやり直す。
        """
        with self.lock:
            job = Job(str(next(self.seq)), kind)
            self.jobs[job.id] = job
        if sync:
            self._run(job, prompt, model, on_done)
        else:
            threading.Thread(target=self._run, args=(job, prompt, model, on_done), daemon=True).start()
        return job

    def _run(self, job, prompt, model, on_done):
        last_err = None
        for attempt in range(2):
            if attempt:
                job.emit("reset", {"message": "AIの返事の形が崩れていたので、もう一度頼んでいます"})
            sp = Splitter()
            try:
                for chunk in self.llm.stream(prompt):
                    out = sp.feed(chunk)
                    if out:
                        job.emit("text", out)
                tail = sp.flush()
                if tail:
                    job.emit("text", tail)
                parsed = model.model_validate(parse_json(sp.json_part))
            except LLMError as e:
                job.emit("error", {"message": str(e)})
                return
            except (ValueError, ValidationError) as e:
                last_err = e
                continue
            try:
                result = on_done(sp.text(), parsed)
            except Exception as e:  # noqa: BLE001 保存に失敗しても画面にはエラーとして返す
                job.emit("error", {"message": f"保存に失敗しました:{e}"})
                return
            job.emit("result", result)
            return
        job.emit("error", {"message": "AIの返事の形が2回続けて崩れていました。もう一度試してください",
                           "detail": str(last_err)[:300]})

    def get(self, job_id):
        return self.jobs.get(job_id)
