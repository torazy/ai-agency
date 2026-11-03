#!/usr/bin/env python3
"""
guardrail.py ── PreToolUse フック（Bash / PowerShell）で起動する物理ガードレール。

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
import re
import sys
from pathlib import Path


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
