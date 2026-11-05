#!/usr/bin/env python3
"""
agent-activity.py ── エージェント（サブエージェント）の起動と終了を記録し、ボードで「誰が何で動いているか」を見せる。

登録するイベント（.claude/settings.json）：
  PreToolUse（Agent|Task）   起動：エージェントの種類・説明・任されたタスクの ID（説明や指示文の C-001 等）・バックグラウンドか
  PostToolUse（Agent|Task）  前景で呼んだものは終了。バックグラウンドのものは「起動した」だけ（終了は SubagentStop で拾う）。
                             起動の応答（tool_response.agentId）を aid として残し、SubagentStop の agent_id と突き合わせる
  PostToolUseFailure         失敗
  SubagentStop               バックグラウンドで動いていたエージェントの終了

出力：memory/task-log/agent-runs.jsonl（1行1イベント。gitignore 済み。端末ごとの記録）
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
            rec.update({"ev": "launched" if inp.get("run_in_background") else "end", "tuid": ev.get("tool_use_id", "")})
            resp = ev.get("tool_response")
            aid = resp.get("agentId") or resp.get("agent_id") if isinstance(resp, dict) else ""
            if aid:
                rec["aid"] = str(aid)
        elif hook == "PostToolUseFailure" and tool in AGENT_TOOLS:
            rec.update({"ev": "fail", "tuid": ev.get("tool_use_id", "")})
        elif hook == "SubagentStop":
            rec.update({"ev": "stop", "agent": ev.get("agent_type") or "", "aid": ev.get("agent_id") or ""})
        else:
            rec = None
        if rec:
            log = workspace_root() / "memory" / "task-log" / "agent-runs.jsonl"
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 ── 記録はベストエフォート
        print(f"[agent-activity] {e}", file=sys.stderr)
    print(json.dumps({"suppressOutput": True}))
    sys.exit(0)


if __name__ == "__main__":
    main()
