#!/usr/bin/env python3
"""
agent-activity.py ── エージェント（サブエージェント）の起動と終了を記録し、ボードで「誰が何で動いているか」を見せる。

登録するイベント（.claude/settings.json）：
  PreToolUse（Agent|Task）   起動：エージェントの種類・説明・任されたタスクの ID（説明や指示文の C-001 等）・バックグラウンドか
  PostToolUse（Agent|Task）  前景で呼んだものは終了。バックグラウンドのものは「起動した」だけ（終了は SubagentStop で拾う）。
                             前景で呼んでも、応答が「背景に回った」もの（状態が async・background 等）は「起動した」（R-044）。
                             起動の応答（tool_response.agentId）を aid として残し、SubagentStop の agent_id と突き合わせる。
                             応答の最上位の鍵と状態（rkeys・rstatus）も残す（中身は残さない。形を後で確かめるため）
  PostToolUseFailure         失敗
  SubagentStop               バックグラウンドで動いていたエージェントの終了

出力：memory/task-log/agent-runs.jsonl（1行1イベント。gitignore 済み。端末ごとの記録）。
      別々のフックのプロセスが同時に書くので、OS のロックで順にして1行を1回の書き込みで追記する（Windows の追記は原子的でない・R-044）
判断は返さない（stdout は {"suppressOutput": true} のみ、常に exit 0）。失敗しても作業の邪魔をしない。
"""

import json
import os
import re
import sys
import time
from pathlib import Path

AGENT_TOOLS = {"Agent", "Task"}


def workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def clip(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


BG_WORDS = {"async", "launched", "background", "backgrounded"}
NOT_BG_WORDS = {"not", "error", "fail", "failed", "stop", "stopped", "completed", "complete", "done", "remote", "cancelled", "canceled"}
STR_FORMS = ("Async agent launched", "Agent moved to background", "Agent is running in the background")
AID_IN_TEXT = re.compile(r"agentId:\s*([A-Za-z0-9_-]{1,64})")


def backgrounded(resp) -> bool:
    """エージェントの応答が「背景で動いている」を示すか（前景で呼んだものが途中で背景に回ったとき・R-044）。
    dict は状態の欄（status・state）の語と旗だけを見る（本文は見ない）。否定の語（not・error・completed・remote 等）があれば背景にしない
    （remote は手元に終了が届かないので、背景にすると稼働中のまま残る）。文字列は、決まった文で始まり agentId を含むものだけ。"""
    if isinstance(resp, dict):
        status = str(resp.get("status") or resp.get("state") or "").lower()
        words = set(re.split(r"[^a-z]+", status)) - {""}
        if words & NOT_BG_WORDS:
            return False
        if words & BG_WORDS:
            return True
        return any(resp.get(k) is True for k in ("isAsync", "is_async", "async", "backgrounded", "isBackground", "run_in_background"))
    if isinstance(resp, str):
        return resp.lstrip().startswith(STR_FORMS) and bool(AID_IN_TEXT.search(resp[:400]))
    return False


def aid_of(resp) -> str:
    if isinstance(resp, dict):
        return str(resp.get("agentId") or resp.get("agent_id") or "")
    if isinstance(resp, str) and resp.lstrip().startswith(STR_FORMS):
        m = AID_IN_TEXT.search(resp[:400])
        return m.group(1) if m else ""
    return ""


LOCK_WAIT_SEC = 2.0
LOCK_OFFSET = 1 << 30   # ロックする位置（ファイルの末尾より遠く。書き込みと重ならない）


def _try_lock(fd: int) -> bool:
    """OS のロック（プロセスが死ねば外れる：置き土産・時計のずれ・消せないロックファイルが無い）。取れなければ False。"""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def append_line(log: Path, line: str) -> None:
    """1行を追記する。OS のロックで順にし、末尾へ移って1回の os.write で書く（Windows の追記は原子的でない・R-044）。
    待っても取れなければそのまま書く（記録はベストエフォート。作業を止めない）。"""
    data = (line.rstrip("\n") + "\n").encode("utf-8")
    fd = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    try:
        deadline = time.time() + LOCK_WAIT_SEC
        got = _try_lock(fd)
        while not got and time.time() < deadline:
            time.sleep(0.005)
            got = _try_lock(fd)
        try:
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, data)
        finally:
            if got:
                _unlock(fd)
    finally:
        os.close(fd)


def main() -> None:
    # 本体（テンプレート）では記録しない：雛形の memory/ に実データを書かない（OPERATIONS.md §1）
    if (workspace_root() / ".ai-agency-template").exists():
        sys.exit(0)
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip() if not sys.stdin.isatty() else ""
        ev = json.loads(raw) if raw else {}
        hook = str(ev.get("hook_event_name", ""))
        tool = str(ev.get("tool_name") or "")
        inp = ev.get("tool_input") or {}
        rec = {"ts": round(time.time(), 1), "sid": str(ev.get("session_id", ""))}
        if os.environ.get("AI_AGENCY_UNATTENDED"):
            rec["unattended"] = True
        if hook == "PreToolUse" and tool in AGENT_TOOLS:
            text = f"{inp.get('description', '')}\n{str(inp.get('prompt', ''))[:4000]}"
            rec.update({"ev": "start", "tuid": ev.get("tool_use_id", ""),
                        "agent": inp.get("subagent_type") or "general-purpose",
                        "desc": clip(inp.get("description") or inp.get("prompt", ""), 80),
                        "tasks": sorted(set(re.findall(r"\bC-\d{3,}\b", text))),
                        "bg": bool(inp.get("run_in_background"))})
        elif hook == "PostToolUse" and tool in AGENT_TOOLS:
            resp = ev.get("tool_response")
            bg = bool(inp.get("run_in_background")) or backgrounded(resp)
            rec.update({"ev": "launched" if bg else "end", "tuid": ev.get("tool_use_id", "")})
            if bg and not inp.get("run_in_background"):
                rec["moved"] = True   # 前景で呼んだが背景に回った
            aid = aid_of(resp)
            if aid:
                rec["aid"] = aid
            if isinstance(resp, dict):
                rec["rkeys"] = sorted(clip(k, 40) for k in resp)[:20]
                for k in ("status", "state"):
                    if isinstance(resp.get(k), (str, int, float)) and not isinstance(resp.get(k), bool):
                        rec["rstatus"] = clip(resp.get(k), 40)
                        break
            else:
                rec["rkind"] = type(resp).__name__
        elif hook == "PostToolUseFailure" and tool in AGENT_TOOLS:
            rec.update({"ev": "fail", "tuid": ev.get("tool_use_id", "")})
        elif hook == "SubagentStop":
            rec.update({"ev": "stop", "agent": ev.get("agent_type") or "", "aid": ev.get("agent_id") or ""})
        else:
            rec = None
        if rec:
            log = workspace_root() / "memory" / "task-log" / "agent-runs.jsonl"
            log.parent.mkdir(parents=True, exist_ok=True)
            append_line(log, json.dumps(rec, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 ── 記録はベストエフォート
        print(f"[agent-activity] {e}", file=sys.stderr)
    print(json.dumps({"suppressOutput": True}))
    sys.exit(0)


if __name__ == "__main__":
    main()
