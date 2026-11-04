#!/usr/bin/env python3
"""
permission-marker.py ── PermissionRequest フック。権限の確認画面が出る直前に「出た」という印を残すだけ。

なぜ：operation-logger（PostToolUse）は、その操作が「ユーザーが確認画面で承認したもの」なのか
「許可ルールで自動的に通ったもの」なのかを自分では区別できない。/propose-delegation が委任を広げる根拠に
数えてよいのは前者だけなので、確認画面が出た呼び出しをここで記録し、logger が突き合わせる。

PermissionRequest は確認画面を出す直前（または確認できずに自動で拒否する直前）にだけ発火し、許可ルールで
通る呼び出しでは発火しない（Claude Code の hooks 仕様）。tool_use_id は渡されないので、
session_id・tool_name・tool_input のハッシュで突き合わせる。

判断（allow / deny）は返さない。stdout には何も出さず exit 0（通常の確認画面がそのまま出る）。失敗しても exit 0。
"""

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

KEEP_SEC = 2 * 3600  # 確認画面のあと2時間以内の実行だけ突き合わせる（拒否されて実行されなかった印は捨てる）


def workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def input_hash(tool_input) -> str:
    return hashlib.sha1(json.dumps(tool_input, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def marker_path(root: Path) -> Path:
    return root / "memory" / "task-log" / ".prompted.json"


def load(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save(path: Path, items: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".prompted.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    for _ in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    os.remove(tmp)


def main() -> None:
    # 本体（テンプレート）では記録しない：雛形の memory/ に実データを書かない（OPERATIONS.md §1）
    if (workspace_root() / ".ai-agency-template").exists():
        sys.exit(0)
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip() if not sys.stdin.isatty() else ""
        event = json.loads(raw) if raw else {}
        tool = str(event.get("tool_name") or "")
        if tool:
            root = workspace_root()
            path = marker_path(root)
            now = time.time()
            items = [x for x in load(path) if now - float(x.get("ts", 0)) < KEEP_SEC]
            items.append({"sid": str(event.get("session_id", "")), "tool": tool,
                          "h": input_hash(event.get("tool_input") or {}), "ts": now})
            save(path, items[-200:])
    except Exception as e:  # noqa: BLE001 ── 記録はベストエフォート。確認画面の邪魔をしない
        print(f"[permission-marker] {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
