"""AIを呼び出す層。

画面やジョブはこのインターフェース(stream)だけを使う。
今回は claude -p だけを実装する。Gemini などに差し替えるときは、同じ stream を持つクラスを足す。
"""
import json
import subprocess
import threading
from pathlib import Path

TIMEOUT_SEC = 180


class LLMError(Exception):
    pass


class LLM:
    """prompt を受け取り、AIが書いた文字を少しずつ返す。"""

    def stream(self, prompt, cancel=None):
        raise NotImplementedError


class ClaudeCLI(LLM):
    """Claude Code のヘッドレスモード(claude -p)。契約済みのサブスクで動くので追加料金はかからない。"""

    def __init__(self, workdir):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

    def stream(self, prompt, cancel=None):
        cmd = "claude -p --output-format stream-json --verbose --include-partial-messages --no-session-persistence"
        proc = subprocess.Popen(cmd, shell=True, cwd=self.workdir, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        timer = threading.Timer(TIMEOUT_SEC, proc.kill)
        timer.start()
        got_text = False
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    raise LLMError("中止しました")
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "stream_event":
                    delta = ev.get("event", {}).get("delta", {})
                    if delta.get("type") == "text_delta":
                        got_text = True
                        yield delta["text"]
                elif ev.get("type") == "result" and ev.get("is_error"):
                    raise LLMError(ev.get("result") or "AIの呼び出しに失敗しました")
            proc.wait()
            if not timer.is_alive() and proc.returncode != 0:
                raise LLMError(f"AIの返事が{TIMEOUT_SEC}秒以内に終わらなかったので打ち切りました")
            if not got_text:
                err = (proc.stderr.read() or "").strip()[:300]
                raise LLMError("AIから返事がありませんでした" + (f"({err})" if err else ""))
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()


class FakeLLM(LLM):
    """テスト用。決まった返事を少しずつ返す。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def stream(self, prompt, cancel=None):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        for i in range(0, len(reply), 7):
            yield reply[i:i + 7]
