#!/usr/bin/env python3
"""
dispatch.py ── 定期の見直し（/dispatch --unattended）を無人で1回まわす。スケジューラから15分ごとに呼ぶ。

  python tools/schedule/dispatch.py            # 間隔が来ていなければ何もしない（memory/work/settings.md の実行間隔）
  python tools/schedule/dispatch.py --force    # 間隔を無視して今まわす
  python tools/schedule/dispatch.py --dry-run  # 実行するコマンドだけ表示

登録：Windows は dispatch.ps1、macOS / Linux は dispatch.sh の冒頭を参照（どちらもこのファイルを呼ぶだけ）。
前提：このフォルダを一度 Claude Code で開いて「信頼」済み。claude と python（Mac は python3）が PATH にある。
出力：memory/briefs/dispatch.md（割り振りの記録・gitignore）、実行ログ memory/work/.state/dispatch.log。
空振りしない：先にトークンを使わずに確かめ（work.py pending：変化・期限からの提案を足し、未判定の提案と未確認の実行を数える）、
      どちらも無く観察の間隔（work/settings.md）も来ていなければ Claude を起動しない（費用0）。
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
LOCK_STALE_SEC = 2 * 3600   # work.DISPATCH_LOCK_STALE_SEC と同じ


def _load_work(root: Path, name: str = "work"):
    spec = importlib.util.spec_from_file_location(f"{name}_dispatch", root / "tools" / f"{name}.py")
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


def build_command(budget: float, observe: bool = True) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("claude コマンドが見つからない（Claude Code の CLI をインストールし PATH を通す）")
    prompt = "/dispatch --unattended" + ("" if observe else " --no-observe")
    return [exe, "-p", prompt, "--allowedTools", *allowed_tools(),
            "--max-budget-usd", f"{budget:g}", "--no-session-persistence", "--output-format", "json"]


def _save_last(sd: Path, **fields) -> None:
    st = sd / "last-dispatch.json"
    try:
        last = json.loads(st.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    last.update(fields)
    sd.mkdir(parents=True, exist_ok=True)
    st.write_text(json.dumps(last, ensure_ascii=False), encoding="utf-8")


def _settle_stale_lock(work, root: Path) -> bool:
    """落ちた見直しのロック（LOCK_STALE_SEC より古い）を外す。外したら True。費用は分からないので、ロックの上限を
    ロックの日の費用として記録する（予約が消えて1日の上限をすり抜けない）。先に名前を変えて取ってから記録する（二重に記録しない）。"""
    lock = work.state_dir(root) / "dispatch.lock"
    info = work.dispatch_lock_info(root)
    if not info or not info["stale"]:
        return False
    taken = lock.with_name(f"dispatch.lock.stale-{os.getpid()}")
    try:
        os.replace(lock, taken)
    except FileNotFoundError:
        return True   # 別の見直しが先に外した
    except OSError:
        return False  # 外せない（権限・開いている）：動いているものとして扱う（呼び直し続けない）
    try:
        if time.time() - taken.stat().st_mtime < LOCK_STALE_SEC:
            # 調べたあとに別の見直しが外して新しいロックを作っていた（生きている）：奪わずに戻す
            try:
                os.rename(taken, lock)
            except OSError:
                pass
            return False
    except OSError:
        return True
    try:
        work.record_cost(root, "dispatch（終わりの記録なし）", None, budget=info["budget"],
                         date=time.strftime("%Y-%m-%d", time.localtime(info["at"])))
    finally:
        try:
            taken.unlink()
        except OSError:
            pass
    return True


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(allow_abbrev=False)
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
    # 費用を使わない後始末を、費用の判定より先に（外部レビュー 69fab61 の 4：落ちた実行の予約で上限に当たると、後始末に届かず止まったまま）：
    # 終了の記録が無いまま実行のプロセスが無くなったもの（PC の再起動など）を閉じて、同時実行の枠と予約を戻す
    _load_work(root, "run_task").reap(root)
    # 落ちた見直しのロック：費用は分からないので上限で記録してから外す（0 にしない。外部レビュー 69fab61 の 5）
    _settle_stale_lock(work, root)
    lim = work.limits(root)
    if work.committed_today(root) + a.budget > lim["daily_usd"]:
        # 使った額＋実行中の予約＋この見直しの上限が1日の上限を超えるなら起動しない（外部レビュー #7）
        print(f"今日の費用（予約を含む）{work.committed_today(root):g} USD＋見直しの上限 {a.budget:g} USD が"
              f"1日の上限 {lim['daily_usd']:g} USD を超える")
        return
    if not a.force and not work.tick(root):
        return   # 間隔が来ていない
    pd = work.pending(root, a.budget)
    sd = work.state_dir(root)
    if not a.force and not pd["work"] and not pd["observe"]:
        _save_last(sd, finished=time.strftime("%Y-%m-%d %H:%M"), exit=0, cost_usd=0, skipped="仕事なし")
        return   # 判断することが無い：Claude を起動しない
    sd.mkdir(parents=True, exist_ok=True)
    lock = sd / "dispatch.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, json.dumps({"at": time.time(), "budget": a.budget}).encode())   # 予約として数える（work.reserved）
        os.close(fd)
        if work.committed_today(root) > lim["daily_usd"]:
            # 確かめてからロックを書くまでの間に、ボードなどから起動された（予約が増えた）：起動しない
            lock.unlink()
            print(f"今日の費用（予約を含む）が1日の上限 {lim['daily_usd']:g} USD を超えるので見直しを起動しない")
            return
    except FileExistsError:
        if not _settle_stale_lock(work, root):
            print("前回の見直しがまだ動いている")
            return
        return main()
    try:
        observe = pd["observe"] or a.force
        cmd = build_command(a.budget, observe)
        env = dict(os.environ, AI_AGENCY_UNATTENDED="1", AI_AGENCY_ROLE="dispatch", PYTHONUTF8="1")
        env.pop("AI_AGENCY_RUN", None)
        env.pop("AI_AGENCY_TASK", None)
        (root / "memory" / "briefs").mkdir(parents=True, exist_ok=True)
        with open(sd / "dispatch.log", "w", encoding="utf-8") as log:
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] start（未判定の提案 {len(pd['undecided'])}・"
                      f"未確認の実行 {len(pd['unjudged'])}・起動待ち {len(pd.get('waiting', []))}・"
                      f"観察 {'する' if observe else 'しない'}）\n")
            log.flush()
            r = subprocess.run(cmd, cwd=root, env=env, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace")
            try:
                out = json.loads(r.stdout or "")
            except json.JSONDecodeError:
                out = {}
            log.write(str(out.get("result") or r.stdout or "") + "\n")
            cost = out.get("total_cost_usd")
            # 1日の費用上限に数える。費用が分からなければ上限で数える（出力が空・壊れた失敗を0にしない。外部レビュー 69fab61 の 5）
            work.record_cost(root, "dispatch", cost, budget=a.budget)
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] exit {r.returncode}"
                      + (f" cost {cost:.2f} USD" if isinstance(cost, (int, float)) else "") + "\n")
        extra = {"observed_at": time.time()} if observe and r.returncode == 0 else {}
        _save_last(sd, exit=r.returncode, cost_usd=cost if isinstance(cost, (int, float)) else None,
                   finished=time.strftime("%Y-%m-%d %H:%M"), skipped="", **extra)
        sys.exit(r.returncode)
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
