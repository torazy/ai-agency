#!/usr/bin/env python3
"""
task-log-writer.py
セッション終了時（SessionEnd フック）にタスクログを自動記録するスクリプト。
Antigravity / Gemini CLI のフックから呼び出される。

SessionEnd の stdin JSON は最小限（session_id / transcript_path / cwd / timestamp 等）なので、
ここではセッションの活動マーカーを残す。リッチな実績（実行した外部操作・学んだこと）は会話中に
memory-update スキル／各ワークフローが decisions.md・insights.md・task-log へ追記する設計。

後方互換：もし stdin に task_summary 等のカスタムフィールドが渡された場合はそれも記録する。
出力規約：標準出力には JSON のみ（{"suppressOutput": true}）。診断は stderr へ。
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def get_workspace_root() -> Path:
    # tools/hooks/task-log-writer.py -> workspace root
    return Path(__file__).resolve().parent.parent.parent


def get_log_file_path(workspace_root: Path, now: datetime) -> Path:
    log_dir = workspace_root / "memory" / "task-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{now.strftime('%Y-%m')}.md"


def format_log_entry(summary: dict, now: datetime) -> str:
    date_str = now.strftime("%Y-%m-%d %H:%M")
    task_summary = summary.get("task_summary", "（セッション終了 ── 詳細サマリーなし）")
    lines = [f"## {date_str}: {task_summary}", ""]

    project_name = summary.get("project_name", "")
    backlog_tickets = summary.get("backlog_tickets", [])
    github_repos = summary.get("github_repos", [])
    operations = summary.get("operations", [])  # [{"category":..., "target":..., "approved":true}]
    learned = summary.get("learned", "")

    if project_name:
        lines.append(f"**関連プロジェクト**: {project_name}")
    if backlog_tickets:
        lines.append("**BacklogチケットNo**: " + ", ".join(f"#{t}" for t in backlog_tickets))
    if github_repos:
        lines.append("**GitHubリポジトリ**: " + ", ".join(github_repos))
    if operations:
        # /propose-delegation がカテゴリ別の承認回数を集計するための構造化行
        for op in operations:
            cat = op.get("category", "?")
            target = op.get("target", "")
            approved = "承認済" if op.get("approved") else "要確認"
            lines.append(f"**操作**: [{cat}] {target} ── {approved}")
    if learned:
        lines.append(f"**新たに学んだこと**: {learned}")

    lines.append("")
    return "\n".join(lines)


def ensure_month_header(log_file: Path, now: datetime) -> None:
    if not log_file.exists():
        log_file.write_text(f"# タスクログ {now.strftime('%Y年%m月')}\n\n", encoding="utf-8")


def read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        # フックは UTF-8 バイト列を渡す。Windows の cp932 デコードを避けるため bytes で読む。
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    now = datetime.now()
    workspace_root = get_workspace_root()
    summary = read_stdin_json()

    log_file = get_log_file_path(workspace_root, now)
    ensure_month_header(log_file, now)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(format_log_entry(summary, now))

    print(f"[task-log-writer] 記録しました: {log_file}", file=sys.stderr)
    print(json.dumps({"suppressOutput": True}))


if __name__ == "__main__":
    main()
