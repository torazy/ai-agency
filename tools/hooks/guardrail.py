#!/usr/bin/env python3
"""
guardrail.py ── PreToolUse フック（Bash / PowerShell・コネクタ mcp__）で起動する物理ガードレール。

コネクタの道具は、無人実行のときに読み取りと下書き以外（送信・返信・予定の作成・ブラウザ操作など）を断る。

memory/guardrails.json の deny パターンに一致するツール呼び出しを、モデルの判断に関係なく
ブロックする。delegation.md の「禁止」を機械的に強制する最後の砦。

ブロック手段は二重化：
  1) exit code 2（＋ stderr に理由）── Claude Code が確実にブロックする主手段
  2) stdout に {"decision":"deny","reason":...} ── JSON 判定向け（併用）

stdin から PreToolUse イベントの JSON を受け取る（tool_name / tool_input を含む）。
入力キーの表記揺れ（snake_case / CamelCase）に頑健に作ってある。
許可する場合は exit 0、stdout に空 or {"suppressOutput": true}。

注意：標準出力には JSON 以外を出さない（フックの出力規約）。診断は stderr へ。
"""

import json
import os
import re
import sys
from pathlib import Path

# 無人のときに許すコネクタ（MCP）の操作：読み取りと、自分の下書き（送らない）だけ。ほかは断る（許可する方を数える。
# 送信系の名前は send_message・chat_postMessage・messages.send・create-events・replyToThread など形が多く、断る方を数えると漏れる。
# 独立レビュー R-037 実装の J）。ブラウザ操作（computer・form_input・navigate）も送れるので断る
READ_VERBS = {"list", "get", "search", "read", "fetch", "query", "download", "find", "describe", "view", "lookup", "whoami"}
WRITE_VERBS = {"send", "reply", "forward", "post", "publish", "share", "invite", "respond", "notify", "create", "update", "delete",
               "cancel", "move", "add", "remove", "trash", "set", "write", "upload", "edit", "insert", "apply", "run", "execute",
               "comment", "label", "unlabel", "mark", "archive", "restore", "copy", "duplicate", "resolve", "spawn", "stop", "convert",
               "computer", "navigate", "form", "click", "type", "submit", "schedule", "book", "accept", "decline", "approve", "pay",
               "transfer", "javascript", "upsert", "patch", "put", "rename", "close", "open"}
DRAFT_WORDS = {"draft", "drafts"}


def tool_words(tool: str) -> set:
    """道具名の最後の部分を語に分ける（snake・kebab・ドット・camelCase）。mcp__gmail__send_message → {send, message}。"""
    last = tool.split("__")[-1]
    last = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", last)
    return {w for w in re.split(r"[^A-Za-z0-9]+", last.lower()) if w}


def unattended_allowed(tool: str) -> bool:
    words = tool_words(tool)
    if words & DRAFT_WORDS:   # 下書き（送らない）：作る・直す・読むだけ
        return not (words & WRITE_VERBS - {"create", "update"})
    return bool(words & READ_VERBS) and not (words & WRITE_VERBS)


def workspace_root() -> Path:
    # tools/hooks/guardrail.py -> workspace root
    return Path(__file__).resolve().parent.parent.parent


def load_guardrails() -> dict:
    path = workspace_root() / "memory" / "guardrails.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        # ガードレール定義が読めない場合は安全側に倒し、ブロックはしないが警告を出す
        print(f"[guardrail] guardrails.json を読めません: {e}", file=sys.stderr)
        return {"deny": []}


def read_event() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        # フックは UTF-8 バイト列を渡す。Windows の cp932 デコードを避けるため bytes で読む。
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def extract_tool_name(event: dict) -> str:
    for key in ("tool_name", "toolName", "name", "original_request_name"):
        if event.get(key):
            return str(event[key])
    return ""


def extract_payload(event: dict) -> str:
    """ツール入力を1つの検査対象文字列に平坦化する。"""
    parts = [extract_tool_name(event)]
    for key in ("tool_input", "toolInput", "input", "args", "arguments", "command"):
        val = event.get(key)
        if val is None:
            continue
        if isinstance(val, str):
            parts.append(val)
        else:
            try:
                parts.append(json.dumps(val, ensure_ascii=False))
            except (TypeError, ValueError):
                parts.append(str(val))
    return "\n".join(parts)


def find_violation(payload: str, guardrails: dict):
    for rule in guardrails.get("deny", []):
        for pattern in rule.get("patterns", []):
            try:
                if re.search(pattern, payload, re.IGNORECASE):
                    return rule, pattern
            except re.error:
                continue
    return None, None


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    event = read_event()
    tool = extract_tool_name(event)
    if tool.startswith("mcp__"):
        # コネクタ（MCP）の道具：無人のとき（定期の見直し・作業セッション）は、読み取りと下書きだけを許す（送信・返信・投稿・
        # 予定の作成・共有・ブラウザ操作などを断る）。送るのは人（設計 #12 段階3・独立レビュー R-037 の M5・J）。
        # allowedTools だけでは、利用者の「常に許可」が無人にも効いて送れてしまう。人の会話では何もしない
        if os.environ.get("AI_AGENCY_UNATTENDED") and not unattended_allowed(tool):
            reason = (f"無人実行では、読み取りと下書き以外のコネクタの操作（{tool}）をしない。相手に届く操作は人が行う"
                      "（下書きまで作り、送ったらボードの「送った」で記録する）")
            print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
            print(reason, file=sys.stderr)
            sys.exit(2)
        print(json.dumps({"suppressOutput": True}))
        sys.exit(0)
    guardrails = load_guardrails()
    payload = extract_payload(event)

    rule, pattern = find_violation(payload, guardrails)
    if rule:
        reason = (
            f"ガードレール違反によりブロックしました [{rule.get('id')}]: "
            f"{rule.get('reason')}（一致パターン: {pattern}）。"
            "実行が必要な場合はユーザーの明示承認を得て、memory/guardrails.json を更新してください。"
        )
        # 手段2: JSON 判定
        print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
        # 手段1: stderr + exit 2（確実なブロック）
        print(reason, file=sys.stderr)
        sys.exit(2)

    # 許可
    print(json.dumps({"suppressOutput": True}))
    sys.exit(0)


if __name__ == "__main__":
    main()
