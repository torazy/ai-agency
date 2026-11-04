#!/usr/bin/env python3
"""
run_task.py ── エージェントに任せたタスク（約束台帳の「出した指示」で相手が @handle）を、無人の Claude Code で着手させる。

  python tools/run_task.py C-007              # 裏で起動してすぐ戻る（ボードの「着手させる」と同じ）
  python tools/run_task.py C-007 --wait       # 終わるまで待つ
  python tools/run_task.py C-007 --dry-run    # 実行するコマンドだけ表示
  python tools/run_task.py --status C-007     # 状態（JSON）

結果：memory/task-results/C-007.md（@chief がまとめた報告。記憶に入れる候補を含むが、記憶そのものは書き換えない）
状態：memory/task-results/C-007.json（running / done / failed・終了コード・時刻）、実行ログ：C-007.log
安全：無人実行（AI_AGENCY_UNATTENDED=1）。許すのは読み取り・調べもの（Web 検索）・サブエージェント・結果ファイルへの書き込みだけ。
      外部への送信・作成は、普段の会話で「常に許可」したもの以外は自動で拒否される。完了にするのは人（ボードの「承認して完了」）。
費用：1回あたり --budget（既定は環境変数 MAX_BUDGET_USD、無ければ 1 USD）で打ち切る。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CID_RE = re.compile(r"C-\d{3,}")
RUNNING_STALE_SEC = 3 * 3600


def load_mem(root: Path):
    spec = importlib.util.spec_from_file_location("mem_run_task", root / "tools" / "mem.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def results_dir(root: Path) -> Path:
    return root / "memory" / "task-results"


def read_status(root: Path, cid: str) -> dict:
    try:
        st = json.loads((results_dir(root) / f"{cid}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if st.get("state") == "running" and time.time() - st.get("start", 0) > RUNNING_STALE_SEC:
        st["state"] = "unknown"   # 終了の記録が無いまま時間がたった（PC の再起動など）
    return st


def write_status(root: Path, cid: str, **kw) -> None:
    p = results_dir(root) / f"{cid}.json"
    st = read_status(root, cid) if p.exists() else {}
    st.update(kw)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def check_task(root: Path, cid: str, child: bool = False) -> dict:
    """着手できるタスクか確かめ、その行を返す。だめなら ValueError（理由つき）。"""
    if not CID_RE.fullmatch(cid or ""):
        raise ValueError(f"ID の形が違う：{cid}")
    if (root / ".ai-agency-template").exists():
        raise ValueError("本体（テンプレート）では実行しない。インスタンスで使う")
    mem = load_mem(root)
    row = next((r for r in mem.commitment_rows(root) if r.get("ID") == cid), None)
    if not row:
        raise ValueError(f"{cid} が約束台帳に無い")
    who = (row.get("相手") or "").strip()
    if row.get("kind") != "given" or not who.startswith("@"):
        raise ValueError(f"{cid} はエージェントに任せたタスクではない（出した指示で相手が @handle のものだけ）")
    handle = who[1:]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", handle) or not (root / ".claude" / "agents" / f"{handle}.md").exists():
        raise ValueError(f"エージェント {who} がいない")
    if row.get("状態") in mem.CLOSED:
        raise ValueError(f"{cid} は既に {row.get('状態')}")
    if not child and read_status(root, cid).get("state") == "running":
        raise ValueError(f"{cid} は実行中")
    return row


def allowed_tools() -> list[str]:
    tools = ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Task",
             "Write(memory/task-results/**)", "Edit(memory/task-results/**)"]
    for py in ("python", "python3"):
        tools.append(f"Bash({py} tools/recall.py *)")
        for op in ("commitment list", "inbox list", "check", "sync show"):
            tools.append(f"Bash({py} tools/mem.py {op}*)")
    return tools


def build_command(cid: str, budget: float) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise ValueError("claude コマンドが見つからない（Claude Code の CLI をインストールし PATH を通す）")
    return [exe, "-p", f"/run-task {cid}", "--allowedTools", *allowed_tools(),
            "--max-budget-usd", f"{budget:g}", "--no-session-persistence"]


def run_foreground(root: Path, cid: str, budget: float, child: bool = False) -> int:
    check_task(root, cid, child=child)
    cmd = build_command(cid, budget)
    rd = results_dir(root)
    rd.mkdir(parents=True, exist_ok=True)
    write_status(root, cid, state="running", start=round(time.time(), 1), end=None, exit=None, budget=budget, pid=os.getpid())
    env = dict(os.environ, AI_AGENCY_UNATTENDED="1", AI_AGENCY_TASK=cid, PYTHONUTF8="1")
    with open(rd / f"{cid}.log", "w", encoding="utf-8") as log:
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] start {cid}\n")
        log.flush()
        try:
            code = subprocess.run(cmd, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
        except OSError as e:
            log.write(f"起動できない: {e}\n")
            code = 127
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] exit {code}\n")
    has_result = (rd / f"{cid}.md").exists()
    write_status(root, cid, state="done" if code == 0 and has_result else "failed", end=round(time.time(), 1), exit=code)
    return code


def launch(root: Path, cid: str, budget: float) -> dict:
    """裏で起動して、すぐ戻る（ボードから呼ぶ）。"""
    row = check_task(root, cid)
    build_command(cid, budget)   # claude が無ければここで止める
    # 子が状態を書く前にボードが読みに来ても「実行中」に見えるよう、先に書いておく
    write_status(root, cid, state="running", start=round(time.time(), 1), end=None, exit=None, budget=budget)
    args = [sys.executable, str(root / "tools" / "run_task.py"), cid, "--wait", "--child", "--budget", f"{budget:g}",
            "--root", str(root)]
    kw: dict = {"cwd": root, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(args, **kw)
    except OSError as e:
        write_status(root, cid, state="failed", end=round(time.time(), 1), exit=127)
        raise ValueError(f"起動できない: {e}") from e
    return {"id": cid, "agent": row.get("相手"), "budget": budget}


def default_budget() -> float:
    try:
        return float(os.environ.get("MAX_BUDGET_USD", "1"))
    except ValueError:
        return 1.0


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="エージェントに任せたタスクを無人で着手させる")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--wait", action="store_true", help="終わるまで待つ")
    ap.add_argument("--dry-run", action="store_true", help="コマンドを表示するだけ")
    ap.add_argument("--status", metavar="ID", help="状態を JSON で表示")
    ap.add_argument("--budget", type=float, default=None, help="1回の上限（USD）")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    root = a.root.resolve()
    if a.status:
        print(json.dumps(read_status(root, a.status), ensure_ascii=False))
        return
    if not a.id:
        ap.error("ID（C-007 など）が必要")
    budget = a.budget if a.budget is not None else default_budget()
    if not 0 < budget <= 20:
        sys.exit("--budget は 0〜20 USD")
    try:
        if a.dry_run:
            check_task(root, a.id)
            print(" ".join(build_command(a.id, budget)))
            return
        if a.wait:
            sys.exit(run_foreground(root, a.id, budget, child=a.child))
        info = launch(root, a.id, budget)
        print(f"▶ {a.id} を {info['agent']} に着手させた（上限 {budget:g} USD）。結果は memory/task-results/{a.id}.md")
    except ValueError as e:
        sys.exit(f"✗ {e}")


if __name__ == "__main__":
    main()
