#!/usr/bin/env python3
"""
run_task.py ── 作業役の起動。任せたタスク（約束台帳の「出した指示」で相手が @handle）を、**タスクごとに独立した新しい
Claude Code のセッション**（無人）で進める。会話全体ではなく、作業記録 memory/work/tasks/{C-ID}.md の「作業依頼」を渡す。

  python tools/run_task.py C-007              # 裏で起動してすぐ戻る（ボードの「着手させる」・割り振り役と同じ）
  python tools/run_task.py C-007 --wait       # 終わるまで待つ
  python tools/run_task.py C-007 --dry-run    # 実行するコマンドだけ表示
  python tools/run_task.py --status C-007     # 最新の実行・進め方（JSON）
  python tools/run_task.py --stop C-007       # 実行中のセッションを止める（新しい条件でやり直すとき）
  python tools/run_task.py --plan C-007 --review qa --reason "部長に出す提案書"   # 進め方（レビュー）を作業依頼に記録
  python tools/run_task.py --applied C-007    # 最新の報告の「記憶に入れる候補」を記録し終えた

実行ID：C-007-R1, R2…（作業記録の「実行履歴」。開始・終了・結果・使った版・費用・判定・反映）
報告：memory/work/results/{実行ID}.md　実行中の状態とログ：memory/work/.state/{実行ID}.json|.log（端末ごと）
上限：memory/work/settings.md の同時実行の上限・1日の費用上限・1回の上限（レビューなし／あり）を超えるなら起動しない。
      実費は CLI の --output-format json の total_cost_usd を実行履歴の「費用」に残す。
安全：AI_AGENCY_UNATTENDED=1。許すのは読み取り・Web 調査・サブエージェント・報告への書き込み・作業記録の追記（途中の変更・
      引き継ぎ）だけ。記憶・約束の状態・外部サービスは変えない。完了にするのは確認役と人。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CID_RE = re.compile(r"C-\d{3,}")
RUNNING_STALE_SEC = 3 * 3600


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_run_task", Path(__file__).resolve().parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


work = _load("work")
mem = work.mem


def results_dir(root: Path) -> Path:
    return work.results_dir(root)


def state_path(root: Path, rid: str, ext: str = "json") -> Path:
    return work.state_dir(root) / f"{rid}.{ext}"


def latest_run(root: Path, cid: str) -> dict:
    runs = work.read_task(root, cid).get("runs", [])
    return runs[-1] if runs else {}


def read_status(root: Path, cid: str) -> dict:
    """最新の実行の状態（ボード・起動フック用）。state：running / done / failed / stopped / unknown。"""
    run = latest_run(root, cid)
    st = {"plan": plan_of(root, cid)} if work.task_path(root, cid).exists() else {}
    if not run:
        return st
    rid = run.get("実行ID", "")
    try:
        extra = json.loads(state_path(root, rid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        extra = {}
    result = run.get("結果", "")
    state = {"実行中": "running", "完了": "done", "停止": "stopped"}.get(result, "failed")
    if state == "running" and time.time() - extra.get("start", time.time()) > RUNNING_STALE_SEC:
        state = "unknown"   # 終了の記録が無いまま時間がたった（PC の再起動など）
    try:
        cost = float(run.get("費用")) if run.get("費用") else None
    except ValueError:
        cost = None
    st.update({"run": rid, "state": state, "result": result, "verdict": run.get("判定", ""), "applied": run.get("反映", ""),
               "start": extra.get("start"), "end": extra.get("end"), "cost_usd": cost, "used": run.get("使った版", ""),
               "has_result": (results_dir(root) / f"{rid}.md").exists()})
    # 終わった実行の成果が今の条件に合っているか（ボードは承認ボタンを押す前に見せる。承認時にも確かめ直す）
    st["stale"] = work.stale_refs(root, rid) if state != "running" and st["has_result"] else []
    return st


def check_task(root: Path, cid: str, child: bool = False) -> dict:
    """着手できるタスクか確かめ、その行を返す。だめなら ValueError（理由つき）。"""
    if not CID_RE.fullmatch(cid or ""):
        raise ValueError(f"ID の形が違う：{cid}")
    if (root / ".ai-agency-template").exists():
        raise ValueError("本体（テンプレート）では実行しない。インスタンスで使う")
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
        raise ValueError(f"{cid} は実行中（止めてやり直すなら --stop）")
    return row


# ---------------------------------------------------------------- 進め方（レビュー）
REVIEW_RE = re.compile(r"auto|none|[a-z0-9][a-z0-9-]*")


def check_review(root: Path, row: dict, review: str) -> str:
    """レビューの指定を確かめて正規化（auto / none / handle）。だめなら ValueError。"""
    review = (review or "auto").strip().lstrip("@")
    if not REVIEW_RE.fullmatch(review):
        raise ValueError(f"レビューの指定が不正：{review}")
    if review in ("auto", "none"):
        return review
    if not (root / ".claude" / "agents" / f"{review}.md").exists():
        raise ValueError(f"レビュアー @{review} がいない")
    if "@" + review == (row.get("相手") or "").strip():
        raise ValueError(f"作成者（@{review}）は自分の成果物をレビューしない。別のエージェントを選ぶ")
    return review


def plan_of(root: Path, cid: str) -> dict:
    """作業依頼の「レビュー」欄（例「qa（部長に出す報告のため）」「none（下調べ）」「auto（秘書が難易度で判断）」）。"""
    v = work.read_task(root, cid).get("order", {}).get("レビュー", "")
    m = re.match(r"\s*@?([a-z0-9][a-z0-9-]*)\s*(?:[（(](.*)[）)])?", v or "")
    if not m:
        return {}
    review = m.group(1)
    reason = (m.group(2) or "").strip()
    return {"review": review, "reason": "" if reason == "秘書が難易度で判断" else reason}


def set_plan(root: Path, cid: str, review: str, reason: str = "") -> dict:
    row = check_task(root, cid, child=True)
    review = check_review(root, row, review)
    reason = (reason or "").strip()[:200]
    try:
        work.order_set(root, cid, {"レビュー": review + (f"（{reason}）" if reason else "")})
    except work.MemError as e:
        raise ValueError(str(e)) from e
    return {"review": review, "reason": reason}


# ---------------------------------------------------------------- 起動
def allowed_tools() -> list[str]:
    tools = ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Task", "SendMessage",
             "Edit(memory/work/results/**)"]   # Edit(パス) は Write を含むファイル編集すべてに効く（Write(パス) は効かない）
    for sh in ("Bash", "PowerShell"):   # Windows では PowerShell ツールで同じ命令を呼ぶことがある
        for py in ("python", "python3"):
            tools.append(f"{sh}({py} tools/recall.py *)")
            for op in ("commitment list", "inbox list", "check", "sync show"):
                tools.append(f"{sh}({py} tools/mem.py {op}*)")
            for op in ("show", "note", "handoff", "status"):
                tools.append(f"{sh}({py} tools/work.py {op}*)")
    return tools


def build_command(cid: str, rid: str, budget: float, review: str = "auto") -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise ValueError("claude コマンドが見つからない（Claude Code の CLI をインストールし PATH を通す）")
    return [exe, "-p", f"/run-task {cid} --run {rid} --review {review}", "--allowedTools", *allowed_tools(),
            "--max-budget-usd", f"{budget:g}", "--no-session-persistence", "--output-format", "json"]


def default_budget(root: Path, review: str = "auto") -> float:
    """1回の上限。環境変数 MAX_BUDGET_USD があればそれ、無ければ settings.md（レビューありは作成・レビュー・直しの3人分）。"""
    try:
        if os.environ.get("MAX_BUDGET_USD"):
            return float(os.environ["MAX_BUDGET_USD"])
    except ValueError:
        pass
    lim = work.limits(root)
    return lim["run_usd"] if review == "none" else lim["run_usd_review"]


MIN_BUDGET_USD = 0.5   # 作業セッションは起動だけで約 0.3 USD 使う（実測）。それ未満の上限では報告も残らない


def check_budget(budget: float) -> None:
    if not MIN_BUDGET_USD <= budget <= 20:
        raise ValueError(f"1回の上限は {MIN_BUDGET_USD:g}〜20 USD（作業セッションは起動だけで約 0.3 USD 使い、それ未満では報告も残らない）")


def check_capacity(root: Path, budget: float) -> None:
    check_budget(budget)
    lim = work.limits(root)
    running = work.running_runs(root)
    if len(running) >= lim["max_running"]:
        raise ValueError(f"同時実行の上限 {lim['max_running']} に達している（実行中：{', '.join(r['実行ID'] for r in running)}）")
    spent = work.spent_today(root)
    if spent + budget > lim["daily_usd"]:
        raise ValueError(f"今日の費用 {spent:g} USD＋今回の上限 {budget:g} USD が1日の上限 {lim['daily_usd']:g} USD を超える")


def _parse_output(stdout: str) -> dict:
    try:
        out = json.loads(stdout or "")
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


def cost_of(stdout: str) -> float | None:
    """--output-format json の total_cost_usd（実際にかかった額）。"""
    c = _parse_output(stdout).get("total_cost_usd")
    return round(float(c), 2) if isinstance(c, (int, float)) else None


def summarize_output(stdout: str) -> str:
    out = _parse_output(stdout)
    if not out:
        return (stdout or "").strip()
    return str(out.get("result") or out.get("subtype") or "").strip()


def run_foreground(root: Path, cid: str, rid: str, budget: float) -> int:
    check_task(root, cid, child=True)
    cmd = build_command(cid, rid, budget, plan_of(root, cid).get("review", "auto"))
    results_dir(root).mkdir(parents=True, exist_ok=True)
    state_path(root, rid).parent.mkdir(parents=True, exist_ok=True)
    state_path(root, rid).write_text(json.dumps({"start": round(time.time(), 1), "pid": os.getpid(), "budget": budget}), encoding="utf-8")
    env = dict(os.environ, AI_AGENCY_UNATTENDED="1", AI_AGENCY_TASK=cid, AI_AGENCY_RUN=rid, PYTHONUTF8="1")
    cost, subtype = None, ""
    with open(state_path(root, rid, "log"), "w", encoding="utf-8") as log:
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] start {rid}\n")
        log.flush()
        try:
            r = subprocess.run(cmd, cwd=root, env=env, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace")
            code = r.returncode
            log.write(summarize_output(r.stdout) + "\n")
            cost = cost_of(r.stdout)
            subtype = str(_parse_output(r.stdout).get("subtype") or "")
        except OSError as e:
            log.write(f"起動できない: {e}\n")
            code = 127
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] exit {code}" + (f" cost {cost:.2f} USD" if cost is not None else "") + "\n")
    st = json.loads(state_path(root, rid).read_text(encoding="utf-8"))
    st.update(end=round(time.time(), 1), exit=code)
    state_path(root, rid).write_text(json.dumps(st), encoding="utf-8")
    if latest_run(root, cid).get("結果") == "停止":
        return code   # 途中で止められた（--stop が記録済み）
    has_result = (results_dir(root) / f"{rid}.md").exists()
    result = "完了" if code == 0 and has_result else ("上限で打ち切り" if "budget" in subtype else "失敗")
    work.run_end(root, rid, result, cost)
    if result == "上限で打ち切り" and not has_result:   # 報告が無いと次の実行も人も手がかりが無いので、作業記録に残す
        work.note(root, cid, f"{rid} は上限 {budget:g} USD で打ち切られ、報告が残っていない"
                  f"（費用 {cost if cost is not None else '不明'} USD）。上限を上げるか、作業を分けてやり直す", info=True)
    return code


def launch(root: Path, cid: str, budget: float | None = None, review: str | None = None) -> dict:
    """新しい作業セッションを裏で起動して、すぐ戻る。review を渡すと進め方（レビュー）も更新する。"""
    row = check_task(root, cid)
    if review is not None:
        check_review(root, row, review)
    work.ensure_task(root, cid)   # 作業依頼が無ければ台帳から最小のものを作る
    rv = review.lstrip("@") if review is not None else plan_of(root, cid).get("review", "auto")
    budget = budget if budget is not None else default_budget(root, rv)
    check_capacity(root, budget)
    exe_ok = build_command(cid, "x", budget)   # claude が無ければここで止める
    del exe_ok
    if review is not None and plan_of(root, cid).get("review") != rv:
        set_plan(root, cid, rv, "ボードで指定")
    rid = work.run_start(root, cid)
    args = [sys.executable, str(root / "tools" / "run_task.py"), cid, "--wait", "--child", rid, "--budget", f"{budget:g}",
            "--root", str(root)]
    kw: dict = {"cwd": root, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        p = subprocess.Popen(args, **kw)
    except OSError as e:
        work.run_end(root, rid, "失敗", None)
        raise ValueError(f"起動できない: {e}") from e
    state_path(root, rid).parent.mkdir(parents=True, exist_ok=True)
    state_path(root, rid).write_text(json.dumps({"start": round(time.time(), 1), "pid": p.pid, "budget": budget}), encoding="utf-8")
    return {"id": cid, "run": rid, "agent": row.get("相手"), "budget": budget, "review": rv}


def stop(root: Path, cid: str, reason: str = "") -> str:
    """実行中のセッションを止める（プロセスごと）。実行履歴は「停止」。"""
    run = latest_run(root, cid)
    if run.get("結果") != "実行中":
        raise ValueError(f"{cid} は実行中ではない")
    rid = run["実行ID"]
    try:
        pid = json.loads(state_path(root, rid).read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError):
        pid = None
    if pid:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    work.run_end(root, rid, "停止", None)
    if reason:
        work.note(root, cid, f"{rid} を止めた：{reason}", info=True)
    return rid


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="任せたタスクを独立した作業セッションで進める")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--wait", action="store_true", help="終わるまで待つ")
    ap.add_argument("--dry-run", action="store_true", help="コマンドを表示するだけ")
    ap.add_argument("--status", metavar="ID", help="最新の実行と進め方を JSON で表示")
    ap.add_argument("--stop", metavar="ID", help="実行中のセッションを止める")
    ap.add_argument("--applied", metavar="ID", help="最新の報告の候補を記憶に反映し終えた印を付ける")
    ap.add_argument("--plan", metavar="ID", help="進め方（レビュー）を作業依頼に記録する。--review と --reason を添える")
    ap.add_argument("--review", default=None, help="auto / none / レビュアーの handle")
    ap.add_argument("--reason", default="", help="進め方・停止の理由（1行）")
    ap.add_argument("--budget", type=float, default=None, help="1回の上限（USD）")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--child", metavar="RUN", help=argparse.SUPPRESS)
    a = ap.parse_args()
    root = a.root.resolve()
    try:
        if a.status:
            print(json.dumps(read_status(root, a.status), ensure_ascii=False))
            return
        if a.stop:
            print(f"■ {stop(root, a.stop, a.reason)} を止めた")
            return
        if a.plan:
            plan = set_plan(root, a.plan, a.review or "auto", a.reason)
            who = {"auto": "秘書が難易度で判断", "none": "なし"}.get(plan["review"], "@" + plan["review"])
            print(f"📝 {a.plan} の進め方：レビュー {who}" + (f"（{plan['reason']}）" if plan["reason"] else ""))
            return
        if a.applied:
            run = latest_run(root, a.applied)
            if not run or not (results_dir(root) / f"{run.get('実行ID')}.md").exists():
                sys.exit(f"✗ {a.applied} の報告が無い")
            work.mark_applied(root, run["実行ID"])
            return
        if not a.id:
            ap.error("ID（C-007 など）が必要")
        if a.budget is not None:
            check_budget(a.budget)
        if a.dry_run:
            check_task(root, a.id)
            b = a.budget if a.budget is not None else default_budget(root, plan_of(root, a.id).get("review", "auto"))
            print(" ".join(build_command(a.id, "C-xxx-Rn", b, plan_of(root, a.id).get("review", "auto"))))
            return
        if a.child:
            sys.exit(run_foreground(root, a.id, a.child, a.budget or default_budget(root)))
        if a.wait:
            info = launch_wait(root, a.id, a.budget, a.review)
            sys.exit(info)
        info = launch(root, a.id, a.budget, a.review)
        print(f"▶ {info['run']} を {info['agent']} の作業セッションで起動した（レビュー {info['review']}・上限 {info['budget']:g} USD）。"
              f"報告は memory/work/results/{info['run']}.md")
    except (ValueError, work.MemError) as e:
        sys.exit(f"✗ {e}")


def launch_wait(root: Path, cid: str, budget: float | None, review: str | None) -> int:
    """前景で実行する（定期実行やテスト用）。"""
    row = check_task(root, cid)
    if review is not None:
        set_plan(root, cid, review, "指定")
    rv = plan_of(root, cid).get("review", "auto")
    budget = budget if budget is not None else default_budget(root, rv)
    check_capacity(root, budget)
    build_command(cid, "x", budget)
    del row
    rid = work.run_start(root, cid)
    return run_foreground(root, cid, rid, budget)


if __name__ == "__main__":
    main()
