#!/usr/bin/env python3
"""
task-log-writer.py
会話終了時（Stop hookで起動）にタスクログを自動記録するスクリプト。
Antigravity IDEのStop hookから呼び出される。
標準入力から会話サマリーJSONを受け取り、memory/task-log/YYYY-MM.md に追記する。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def get_workspace_root() -> Path:
    """ワークスペースのルートディレクトリを取得する"""
    # スクリプトの場所から2階層上がワークスペースルート
    # tools/hooks/task-log-writer.py -> workspace root
    return Path(__file__).parent.parent.parent


def get_log_file_path(workspace_root: Path, now: datetime) -> Path:
    """ログファイルのパスを取得する（存在しない場合はディレクトリも作成）"""
    log_dir = workspace_root / "memory" / "task-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{now.strftime('%Y-%m')}.md"


def format_log_entry(summary: dict, now: datetime) -> str:
    """ログエントリを整形する"""
    date_str = now.strftime("%Y-%m-%d %H:%M")

    task_summary = summary.get("task_summary", "（サマリーなし）")
    backlog_tickets = summary.get("backlog_tickets", [])
    github_repos = summary.get("github_repos", [])
    project_name = summary.get("project_name", "")
    learned = summary.get("learned", "")

    lines = [
        f"## {date_str}: {task_summary}",
        "",
    ]

    if project_name:
        lines.append(f"**関連プロジェクト**: {project_name}")
    if backlog_tickets:
        tickets_str = ", ".join([f"#{t}" for t in backlog_tickets])
        lines.append(f"**BacklogチケットNo**: {tickets_str}")
    if github_repos:
        repos_str = ", ".join(github_repos)
        lines.append(f"**GitHubリポジトリ**: {repos_str}")
    if learned:
        lines.append(f"**新たに学んだこと**: {learned}")

    lines.append("")
    return "\n".join(lines)


def ensure_month_header(log_file: Path, now: datetime) -> None:
    """月ヘッダーが存在しない場合は追加する"""
    month_header = f"# タスクログ {now.strftime('%Y年%m月')}\n\n"

    if not log_file.exists():
        log_file.write_text(month_header, encoding="utf-8")


def append_log_entry(log_file: Path, entry: str) -> None:
    """ログエントリを追記する"""
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(entry)


def main() -> None:
    now = datetime.now()
    workspace_root = get_workspace_root()

    # 標準入力からサマリーJSONを読み込む（なければデフォルト値を使用）
    summary = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read().strip()
            if raw:
                summary = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # JSON解析失敗時は空のサマリーで続行
            summary = {}

    # サマリーが空の場合は最小限の記録を残す
    if not summary:
        summary = {"task_summary": "（自動記録 ── 詳細サマリーなし）"}

    log_file = get_log_file_path(workspace_root, now)
    ensure_month_header(log_file, now)

    entry = format_log_entry(summary, now)
    append_log_entry(log_file, entry)

    print(f"[task-log-writer] ログを記録しました: {log_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
