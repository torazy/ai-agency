#!/usr/bin/env python3
"""
operation-logger.py ── PostToolUse / PostToolUseFailure フックで「外部への書き込み・送信」を自動記録する。

目的：/propose-delegation が集計する承認実績（`**操作**: [カテゴリ] 対象 ── 承認済/要確認`）を、
秘書が手で書かなくても貯まるようにする。「育てると承認だけ」エンジンの入力源。

記録するもの（tools/hooks/operation-log.json で調整）：
  - MCP ツールのうち、ツール名が書き込み動詞（create/update/send/…）で始まるもの
  - Bash/PowerShell のうち、git push / gh pr create / curl -X POST などの外部書き込み
  - Write/Edit 系の memory/ 配下への書き込み（委任昇格の対象になるため）
記録しないもの：読み取り系ツール、IDE 内部ツール（mcp__ccd_* 等）、ブラウザ/デスクトップ操作。

stdin: フックイベント JSON（tool_name / tool_input / hook_event_name / session_id / cwd …）。
stdout: JSON のみ（{"suppressOutput": true}）。診断は stderr。失敗しても exit 0（記録はベストエフォート）。
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path


def workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_config() -> dict:
    path = Path(__file__).resolve().parent / "operation-log.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[operation-logger] 設定を読めません: {e}", file=sys.stderr)
        return {}


def read_event() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def truncate(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe_target(tool_input: dict, cfg: dict) -> str:
    """tool_input から人が読める対象を組み立てる（最大3項目）。"""
    if not isinstance(tool_input, dict):
        return truncate(str(tool_input), cfg.get("target_max_chars", 90))
    parts = []
    for key in cfg.get("target_keys_priority", []):
        if key in tool_input and tool_input[key] not in (None, "", [], {}):
            val = tool_input[key]
            if isinstance(val, (list, dict)):
                val = json.dumps(val, ensure_ascii=False)
            parts.append(f"{key}={truncate(val, 40)}")
            if len(parts) >= 3:
                break
    if not parts:
        for key, val in tool_input.items():
            if isinstance(val, (str, int)) and str(val).strip():
                parts.append(f"{key}={truncate(val, 40)}")
            if len(parts) >= 2:
                break
    return truncate(" ".join(parts), cfg.get("target_max_chars", 90)) or "-"


def classify(tool_name: str, tool_input: dict, cfg: dict, root: Path):
    """(category, target) を返す。記録対象外なら (None, None)。"""
    for prefix in cfg.get("skip_tool_prefixes", []):
        if tool_name.startswith(prefix):
            return None, None

    # MCP ツール: mcp__{server}__{tool}
    m = re.match(r"^mcp__(.+?)__(.+)$", tool_name)
    if m:
        server, tool = m.group(1), m.group(2)
        server = cfg.get("server_aliases", {}).get(server, server)
        verbs = cfg.get("mcp_write_verbs", "")
        if verbs and re.search(verbs, tool, re.IGNORECASE):
            return f"{server}:{tool}", describe_target(tool_input, cfg)
        return None, None

    # シェル: 外部書き込みに当たるコマンドだけ
    if tool_name in ("Bash", "PowerShell"):
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = str(tool_input.get("command", ""))
        for rule in cfg.get("bash_categories", []):
            try:
                if re.search(rule["pattern"], cmd, re.IGNORECASE):
                    return rule["category"], truncate(cmd, cfg.get("target_max_chars", 90))
            except (re.error, KeyError):
                continue
        return None, None

    # ファイル書き込み: memory/ 配下は委任昇格の対象なので記録
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        fp = ""
        if isinstance(tool_input, dict):
            fp = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if not fp:
            return None, None
        try:
            rel = Path(fp).resolve().relative_to(root).as_posix()
        except ValueError:
            return None, None
        if rel.startswith("memory/") and not rel.startswith("memory/task-log/"):
            return "memory-write", rel
        return None, None

    return None, None


def append_log(root: Path, line: str, now: datetime) -> Path:
    log_dir = root / "memory" / "task-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{now.strftime('%Y-%m')}.md"
    day_header = f"## {now.strftime('%Y-%m-%d')}"
    existing = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    chunks = []
    if not existing:
        chunks.append(f"# タスクログ {now.strftime('%Y年%m月')}\n\n")
    if not re.search(rf"^{re.escape(day_header)}\s*$", existing, re.MULTILINE):
        chunks.append(f"{day_header}\n\n")
    chunks.append(line + "\n")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("".join(chunks))
    return log_file


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    event = read_event()
    cfg = load_config()
    root = workspace_root()
    tool_name = str(event.get("tool_name") or event.get("toolName") or "")
    tool_input = event.get("tool_input") or event.get("toolInput") or {}
    hook_event = str(event.get("hook_event_name", ""))

    category, target = classify(tool_name, tool_input, cfg, root)
    if category:
        status = "失敗" if hook_event == "PostToolUseFailure" else "承認済"
        now = datetime.now()
        sid = str(event.get("session_id", ""))[:8]
        line = f"**操作**: [{category}] {target} ── {status}（{now.strftime('%H:%M')} {sid}）"
        try:
            log_file = append_log(root, line, now)
            print(f"[operation-logger] {log_file.name}: {line}", file=sys.stderr)
        except OSError as e:
            print(f"[operation-logger] 記録に失敗: {e}", file=sys.stderr)

    print(json.dumps({"suppressOutput": True}))
    sys.exit(0)


if __name__ == "__main__":
    main()
