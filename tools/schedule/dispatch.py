#!/usr/bin/env python3
"""
dispatch.py ── 定期の見直し（/dispatch --unattended）を無人で1回まわす。スケジューラから15分ごとに呼ぶ。

  python tools/schedule/dispatch.py            # 間隔が来ていなければ何もしない（memory/work/settings.md の実行間隔）
  python tools/schedule/dispatch.py --force    # 間隔を無視して今まわす
  python tools/schedule/dispatch.py --dry-run  # 実行するコマンドだけ表示

登録：Windows は dispatch.ps1、macOS / Linux は dispatch.sh の冒頭を参照（どちらもこのファイルを呼ぶだけ）。
前提：このフォルダを一度 Claude Code で開いて「信頼」済み。claude と python（Mac は python3）が PATH にある。
出力：memory/briefs/dispatch.md（割り振りの記録・gitignore）、実行ログ memory/work/.state/dispatch.log。
安全：AI_AGENCY_UNATTENDED=1。許すのは読み取り、work.py（記録）、run_task.py（作業セッションの起動・停止）、
      mem.py の追記と読み取り、memory/briefs/ への書き込みだけ。外部への送信・作成は、普段の会話で「常に許可」したもの以外は拒否。
      同時に2つ走らないようロックする。費用は1回の上限（既定 1.5 USD）と work/settings.md の1日の上限で抑える。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOCK_STALE_SEC = 2 * 3600


def _load_work(root: Path):
    spec = importlib.util.spec_from_file_location("work_dispatch", root / "tools" / "work.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def allowed_tools() -> list[str]:
    tools = ["Read", "Glob", "Grep", "Edit(memory/briefs/**)"]
    shells = ("Bash", "PowerShell")   # Windows では PowerShell ツールで同じ命令を呼ぶことがある
    for sh in shells:
        for py in ("python", "python3"):
            tools += [f"{sh}({py} tools/work.py *)", f"{sh}({py} tools/run_task.py *)", f"{sh}({py} tools/recall.py *)"]
            for op in ("commitment add", "commitment list", "inbox add", "inbox list", "check", "sync show"):
                tools.append(f"{sh}({py} tools/mem.py {op}*)")
    return tools


def build_command(budget: float) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("claude コマンドが見つからない（Claude Code の CLI をインストールし PATH を通す）")
    return [exe, "-p", "/dispatch --unattended", "--allowedTools", *allowed_tools(),
            "--max-budget-usd", f"{budget:g}", "--no-session-persistence", "--output-format", "json"]


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget", type=float, default=float(os.environ.get("DISPATCH_BUDGET_USD", "1.5")))
    a = ap.parse_args()
    root = a.root.resolve()
    if (root / ".ai-agency-template").exists():
        print("本体（テンプレート）では実行しない")
        return
    if not (root / "memory" / "dashboard.md").exists():
        sys.exit(f"memory/ が無い: {root}")
    work = _load_work(root)
    work.ensure_work(root)
    if a.dry_run:
        print(" ".join(build_command(a.budget)))
        return
    lim = work.limits(root)
    if work.spent_today(root) >= lim["daily_usd"]:
        print(f"今日の費用が上限 {lim['daily_usd']:g} USD に達している")
        return
    if not a.force and not work.tick(root):
        return   # 間隔が来ていない
    sd = work.state_dir(root)
    sd.mkdir(parents=True, exist_ok=True)
    lock = sd / "dispatch.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(time.time()).encode())
        os.close(fd)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < LOCK_STALE_SEC:
            print("前回の見直しがまだ動いている")
            return
        lock.unlink()
        return main()
    try:
        cmd = build_command(a.budget)
        env = dict(os.environ, AI_AGENCY_UNATTENDED="1", PYTHONUTF8="1")
        (root / "memory" / "briefs").mkdir(parents=True, exist_ok=True)
        with open(sd / "dispatch.log", "w", encoding="utf-8") as log:
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] start\n")
            log.flush()
            r = subprocess.run(cmd, cwd=root, env=env, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace")
            try:
                out = json.loads(r.stdout or "")
            except json.JSONDecodeError:
                out = {}
            log.write(str(out.get("result") or r.stdout or "") + "\n")
            cost = out.get("total_cost_usd")
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] exit {r.returncode}"
                      + (f" cost {cost:.2f} USD" if isinstance(cost, (int, float)) else "") + "\n")
        st = sd / "last-dispatch.json"
        try:
            last = json.loads(st.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = {}
        last.update(exit=r.returncode, cost_usd=cost if isinstance(cost, (int, float)) else None, finished=time.strftime("%Y-%m-%d %H:%M"))
        st.write_text(json.dumps(last, ensure_ascii=False), encoding="utf-8")
        sys.exit(r.returncode)
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
