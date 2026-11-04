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

import hashlib
import json
import os
import re
import sys
import time
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
        # 自作サーバーはツール名にサーバー名を前置することがある（backlog_create_issue）。前置を外して動詞を見る
        bare = re.sub(rf"^{re.escape(m.group(1))}[_-]", "", tool, flags=re.IGNORECASE)
        bare = re.sub(rf"^{re.escape(server)}[_-]", "", bare, flags=re.IGNORECASE)
        if verbs and (re.search(verbs, tool, re.IGNORECASE) or re.search(verbs, bare, re.IGNORECASE)):
            return f"{server}:{tool}", describe_target(tool_input, cfg)
        return None, None

    # シェル: 外部書き込みに当たるコマンドだけ。&& ; | 改行で区切った各コマンドを個別に分類する
    # （1つの Bash に mem.py を2回書いても2行記録される）
    if tool_name in ("Bash", "PowerShell"):
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = str(tool_input.get("command", ""))
        # ヒアドキュメント本文（<<'EOF' … EOF）はコマンドではないので外す
        cmd_wo_heredoc = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?\n.*?\n\1\s*$", "<<HEREDOC", cmd, flags=re.S | re.M)
        segments = [s.strip() for s in re.split(r"&&|\|\||;|\n", cmd_wo_heredoc) if s.strip()]
        hits = []
        for seg in segments:
            if re.search(r"(^|\s)(--help|-h)(\s|$)", seg):  # ヘルプ表示は操作ではない
                continue
            for rule in cfg.get("bash_categories", []):
                try:
                    if re.search(rule["pattern"], seg, re.IGNORECASE):
                        hits.append((rule["category"], truncate(seg, cfg.get("target_max_chars", 90))))
                        break
                except (re.error, KeyError):
                    continue
        if hits:
            return hits, None
        # セグメントで拾えなくても、ヒアドキュメント本文（python スクリプト等）が memory/ に書いていれば記録する
        for rule in cfg.get("bash_categories", []):
            if rule.get("category", "").startswith("memory-write"):
                try:
                    if re.search(rule["pattern"], cmd, re.IGNORECASE):
                        return [(rule["category"], truncate(cmd, cfg.get("target_max_chars", 90)))], None
                except (re.error, KeyError):
                    pass
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
        # task-log（このフック自身の出力）と briefs（無人ブリーフの生成物）は委任の判断材料ではないので記録しない
        if rel.startswith("memory/") and not rel.startswith(("memory/task-log/", "memory/briefs/")):
            return "memory-write", rel
        return None, None

    return None, None


PROMPT_KEEP_SEC = 2 * 3600


def input_hash(tool_input) -> str:
    return hashlib.sha1(json.dumps(tool_input, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def take_prompt(root: Path, sid: str, tool: str, tool_input) -> bool:
    """permission-marker.py が残した「確認画面が出た」印のうち、この呼び出しに当たるものを1つ消費する。"""
    path = root / "memory" / "task-log" / ".prompted.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    now, h, hit = time.time(), input_hash(tool_input), None
    keep = []
    for x in items if isinstance(items, list) else []:
        if now - float(x.get("ts", 0)) >= PROMPT_KEEP_SEC:
            continue
        if hit is None and x.get("sid") == sid and x.get("tool") == tool and x.get("h") == h:
            hit = x
            continue
        keep.append(x)
    if hit is not None or len(keep) != len(items):
        try:
            path.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return hit is not None


def append_log(root: Path, line: str, now: datetime) -> Path:
    log_dir = root / "memory" / "task-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{now.strftime('%Y-%m')}.md"
    day_header = f"## {now.strftime('%Y-%m-%d')}"
    existing = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    chunks = []
    if not existing:
        # strftime の書式に日本語を入れない（Windows の Python 3.11 は英語ロケールで UnicodeEncodeError になる）
        chunks.append(f"# タスクログ {now.year}年{now.month:02d}月\n\n")
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
    # 本体（テンプレート）では記録しない：雛形の memory/ に実データを書かない（OPERATIONS.md §1）
    if (workspace_root() / ".ai-agency-template").exists():
        sys.exit(0)

    event = read_event()
    cfg = load_config()
    root = workspace_root()
    tool_name = str(event.get("tool_name") or event.get("toolName") or "")
    tool_input = event.get("tool_input") or event.get("toolInput") or {}
    hook_event = str(event.get("hook_event_name", ""))

    category, target = classify(tool_name, tool_input, cfg, root)
    # Bash は複数ヒットをリストで返す。単一ヒットと同じ経路に揃える
    entries = category if isinstance(category, list) else ([(category, target)] if category else [])
    # 「承認済」＝確認画面が実際に出て、ユーザーが通したもの（permission-marker.py の印と一致）。
    # 確認画面が出ずに通ったものは、通常モードなら「自動許可」（許可ルールで通過）、auto/acceptEdits/bypass なら「自動実行」。
    # /propose-delegation は承認済だけを昇格の根拠に数える
    sid_full = str(event.get("session_id", ""))
    prompted = take_prompt(root, sid_full, tool_name, tool_input) if entries and hook_event != "PostToolUseFailure" else False
    for category, target in entries:
        mode = str(event.get("permission_mode", "") or "")
        if os.environ.get("AI_AGENCY_UNATTENDED"):
            mode = "unattended"  # tools/schedule/ の無人実行。人が承認したわけではないので承認済に数えない
        if hook_event == "PostToolUseFailure":
            status = "失敗"
        elif mode == "unattended":
            status = "自動実行[unattended]"
        elif prompted:
            status = "承認済"
        elif mode in ("", "default", "plan"):
            status = "自動許可"
        else:
            status = f"自動実行[{mode}]"
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
