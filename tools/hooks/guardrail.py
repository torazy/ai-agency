#!/usr/bin/env python3
"""
guardrail.py ── PreToolUse フック（Bash / PowerShell・コネクタ mcp__・Edit / Write）で起動する物理ガードレール。

無人実行（AI_AGENCY_UNATTENDED）のときは、--allowedTools と同じ範囲をフックでも守る（利用者の「常に許可」は無人にも効くため）：
ファイルの編集は報告・ブリーフの場所だけ、シェルは tools/ の命令を1つずつだけ。

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


# 無人のときの範囲（--allowedTools と同じ。利用者の「常に許可」は無人にも効くので、フックでも守る。R-042）。
# 役は起動側が AI_AGENCY_ROLE で渡す（worker＝作業セッション・dispatch＝定期の見直し・brief＝朝のブリーフ）。
# 渡されないときは、AI_AGENCY_RUN があれば worker、無ければ dispatch（以前の起動スクリプト）。
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
ALL = "*"
ROLE_COMMANDS = {   # 命令ごとに許す副命令（mem は2語まで見る）。正本は run_task.allowed_tools・dispatch.allowed_tools・morning-brief.*
    "worker": {"work": {"show", "note", "handoff", "status"}, "recall": ALL,
               "mem": {"commitment list", "inbox list", "check", "sync show"}},
    "dispatch": {"work": ALL, "run_task": ALL, "recall": ALL,
                 "mem": {"commitment add", "commitment list", "inbox add", "inbox list", "check", "sync show"}},
    "brief": {"work": {"needs"}, "recall": ALL,
              "mem": {"inbox add", "inbox list", "commitment add", "commitment list", "person observe", "person note",
                      "sync mark", "sync show", "check"}},
}
CMD_RE = re.compile(r"^\s*(?:python3?|py)\s+(?:\./)?tools/(work|run_task|recall|mem)\.py(?=\s|$)(.*)$", re.S)
SMART_QUOTES = "‘’‚‛“”„‟"


def unattended_role() -> str:
    role = os.environ.get("AI_AGENCY_ROLE", "")
    if role in ROLE_COMMANDS:
        return role
    return "worker" if os.environ.get("AI_AGENCY_RUN") else "dispatch"


def unattended_write_allowed(path: str) -> bool:
    """作業セッションは自分の報告 memory/work/results/{AI_AGENCY_RUN}.md だけ（他の実行の報告を書かない）。
    定期の見直し・朝のブリーフは memory/briefs/ の中のファイルだけ（場所そのものは書かない）。"""
    if not path or "\x00" in path:
        return False
    root = workspace_root()
    try:
        p = Path(path)
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if unattended_role() == "worker":
            run = os.environ.get("AI_AGENCY_RUN", "")
            if not re.fullmatch(r"C-\d{3,}-R\d+", run):
                return False
            target = (root / "memory" / "work" / "results" / f"{run}.md").resolve()
            return os.path.normcase(str(p)) == os.path.normcase(str(target))
        base = (root / "memory" / "briefs").resolve()
        rel = os.path.relpath(os.path.normcase(str(p)), os.path.normcase(str(base)))
    except (OSError, ValueError):   # 別のドライブ・NUL など
        return False
    return rel not in (".", "") and rel != ".." and not rel.startswith(".." + os.sep) and not os.path.isabs(rel)


def bash_operator(cmd: str) -> bool:
    """bash として、引用符の外に区切り・リダイレクト・置換があるか（\\ の次の1文字は飛ばす。'…' は何もしない。
    "…" の中は \\ の次を飛ばし、` と $( を見る）。引用符が閉じていなければ断る。"""
    q, i = None, 0
    while i < len(cmd):
        c = cmd[i]
        if q == "'":
            if c == "'":
                q = None
        elif q == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                q = None
            elif c == "`" or cmd.startswith("$(", i):
                return True
        else:
            if c == "\\":
                i += 1
            elif c in "'\"":
                q = c
            elif c in ";&|<>()`\n\r" or cmd.startswith("$(", i):
                return True
        i += 1
    return q is not None


def powershell_operator(cmd: str) -> bool:
    """PowerShell として、引用符の外に区切り・式・変数・呼び出しがあるか。'…' は '' で引用符そのもの。"…" の中は ` と $ を断る。
    全角の引用符（‘ ’ “ ” など）は PowerShell が引用符として扱うので、どこにあっても断る。"""
    if any(ch in cmd for ch in SMART_QUOTES):
        return True
    q, i = None, 0
    while i < len(cmd):
        c = cmd[i]
        if q == "'":
            if c == "'":
                if cmd.startswith("''", i):
                    i += 1
                else:
                    q = None
        elif q == '"':
            if c in "`$":
                return True
            if c == '"':
                if cmd.startswith('""', i):
                    i += 1
                else:
                    q = None
        else:
            if c in "'\"":
                q = c
            elif c in ";&|<>(){}@$`\n\r":
                return True
        i += 1
    return q is not None


def unattended_command_allowed(cmd: str, shell: str = "Bash") -> bool:
    """無人のシェル：tools/ の製品の命令1つだけ。役ごとに許す副命令だけ。--root（別の場所の記録を書く）は使わない。"""
    cmd = cmd or ""
    if (powershell_operator if shell == "PowerShell" else bash_operator)(cmd):
        return False
    m = CMD_RE.match(cmd.replace("\\", "/") if shell == "PowerShell" else cmd)
    if not m:
        return False
    script, rest = m.group(1), m.group(2)
    words = rest.split()
    if any(w == "--root" or w.startswith("--root=") for w in words):
        return False
    allowed = ROLE_COMMANDS[unattended_role()].get(script)
    if allowed is None:
        return False
    if allowed == ALL:
        return True
    head = [w for w in words[:2]]
    return bool(head) and (head[0] in allowed or " ".join(head[:2]) in allowed)


def tool_input_of(event: dict) -> dict:
    for key in ("tool_input", "toolInput", "input", "arguments"):
        v = event.get(key)
        if isinstance(v, dict):
            return v
    return {}


def deny(reason: str) -> None:
    print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
    print(reason, file=sys.stderr)
    sys.exit(2)


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
        ev = json.loads(raw) if raw else {}
        return ev if isinstance(ev, dict) else {}   # 配列などは「入力なし」（無人なら下で断る）
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
    """無人のときは、フックの中の例外でも断る（exit 2 以外は許可と扱われる。壊れた入力・壊れたフックで通さない）。"""
    try:
        _main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        if os.environ.get("AI_AGENCY_UNATTENDED"):
            deny(f"無人実行でガードレールが判定できなかったので断る（{type(e).__name__}: {e}）")
        print(f"[guardrail] 判定できなかった（{type(e).__name__}: {e}）", file=sys.stderr)
        sys.exit(0)


def _main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    event = read_event()
    tool = extract_tool_name(event)
    unattended = bool(os.environ.get("AI_AGENCY_UNATTENDED"))
    if unattended and not tool:
        deny("無人実行で、道具の分からない呼び出しは断る")
    if tool in WRITE_TOOLS:
        # ファイルの編集：無人のときは許した場所だけ（約束台帳・作業記録・不足の記録などを直接書き換えさせない）。人の会話では何もしない
        inp = tool_input_of(event)
        path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
        if unattended and not unattended_write_allowed(path):
            where = (f"自分の報告 memory/work/results/{os.environ.get('AI_AGENCY_RUN', '')}.md" if unattended_role() == "worker"
                     else "memory/briefs/ の中")
            deny(f"無人実行では {where} しか書かない（{path or '場所なし'}）。記録は tools/ の命令で残す")
        print(json.dumps({"suppressOutput": True}))
        sys.exit(0)
    if unattended and tool in ("Bash", "PowerShell"):
        cmd = str(tool_input_of(event).get("command") or event.get("command") or "")
        if not unattended_command_allowed(cmd, tool):
            deny(f"無人実行（{unattended_role()}）では、この役に許した tools/ の命令を1つずつだけ実行する"
                 "（cd・&&・パイプ・リダイレクト・$( )・バッククォート・--root や他の命令はしない。"
                 f"PowerShell では @handle や ( ) を引用符で囲む）：{cmd[:120]}")
    if tool.startswith("mcp__"):
        # コネクタ（MCP）の道具：無人のとき（定期の見直し・作業セッション）は、読み取りと下書きだけを許す（送信・返信・投稿・
        # 予定の作成・共有・ブラウザ操作などを断る）。送るのは人（設計 #12 段階3・独立レビュー R-037 の M5・J）。
        # allowedTools だけでは、利用者の「常に許可」が無人にも効いて送れてしまう。人の会話では何もしない
        if unattended and not unattended_allowed(tool):
            deny(f"無人実行では、読み取りと下書き以外のコネクタの操作（{tool}）をしない。相手に届く操作は人が行う"
                 "（下書きまで作り、送ったらボードの「送った」で記録する）")
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
