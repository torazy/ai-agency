#!/usr/bin/env python3
"""
run_task.py ── 作業役の起動。任せたタスク（約束台帳の「出した指示」で相手が @handle）を、**タスクごとに独立した新しい
Claude Code のセッション**（無人）で進める。会話全体ではなく、作業記録 memory/work/tasks/{C-ID}.md の「作業依頼」を渡す。

  python tools/run_task.py C-007              # 裏で起動してすぐ戻る（ボードの「着手させる」・割り振り役と同じ）
  python tools/run_task.py C-007 --wait       # 終わるまで待つ
  python tools/run_task.py C-007 --dry-run    # 実行するコマンドだけ表示
  python tools/run_task.py --status C-007     # 最新の実行・進め方（JSON）
  python tools/run_task.py --stop C-007       # 実行中のセッションを止める（新しい条件でやり直すとき）
  python tools/run_task.py --stop C-007-R1    # 古い実行が「実行中」のまま残っているとき、実行ID で個別に閉じる
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
    try:
        if run.get("結果") == "実行中" and reap(root, rid):
            run = latest_run(root, cid)   # プロセスが無くなっていた：終了不明として閉じた
    except work.MemError:
        pass   # ロックが取れない（ほかが書いている）：今回は後始末しない。一覧の表示は止めない
    result = run.get("結果", "")
    state = {"実行中": "running", "完了": "done", "停止": "stopped", "終了不明": "unknown"}.get(result, "failed")
    try:
        cost = float(run.get("費用")) if run.get("費用") else None
    except ValueError:
        cost = None
    st.update({"run": rid, "state": state, "result": result, "verdict": run.get("判定", ""), "applied": run.get("反映", ""),
               "start": extra.get("start"), "end": extra.get("end"), "cost_usd": cost, "used": run.get("使った版", ""),
               "has_result": (results_dir(root) / f"{rid}.md").exists()})
    # 終わった実行の成果が今の条件に合っているか（ボードは承認ボタンを押す前に見せる。承認時にも確かめ直す）
    st["stale"] = work.stale_refs(root, rid) if state != "running" and st["has_result"] else []
    if state == "running" and not extra.get("pid") and not extra.get("child"):
        try:
            started = time.mktime(time.strptime(run.get("開始", ""), "%Y-%m-%d %H:%M"))
        except ValueError:
            started = 0
        if time.time() - started >= NO_STATE_GRACE_SEC:
            st["unverifiable"] = f"実行の記録（.state）が無く、生きているか確かめられない。止めるなら run_task.py --stop {rid}"
    return st


def _proc_started(pid: int):
    """pid のプロセスが生きていれば開始時刻（UNIX 秒。分からなければ None）、いなければ False。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None if ctypes.get_last_error() == 5 else False   # 5 = 権限が無い（別のユーザーのプロセス）
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # 259 = STILL_ACTIVE
                return False
            t = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(h, *[ctypes.byref(x) for x in t]):
                return None
            return ((t[0].dwHighDateTime << 32) | t[0].dwLowDateTime) / 1e7 - 11644473600
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError:
        return False
    try:
        out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
        days, _, hms = out.rpartition("-")
        secs = 0
        for part in hms.split(":"):
            secs = secs * 60 + int(part)
        return time.time() - secs - int(days or 0) * 86400
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _ours(pid, since: float) -> bool:
    """記録した pid が、今もこの実行のプロセスか（PC の再起動後は別のプロセスに番号が使い回される）。"""
    started = _proc_started(pid)
    if started is False:
        return False
    return started is None or not since or started <= since + REUSE_SLACK_SEC


REUSE_SLACK_SEC = 300      # 記録した開始時刻より後に作られたプロセスは別物（時計の補正の揺れは見込む）
NO_STATE_GRACE_SEC = 600   # 起動の記録（.state）が書かれる前の実行は、この間は閉じない


def _host() -> str:
    import socket
    return socket.gethostname()


def reap(root: Path, only: str | None = None) -> list[str]:
    """「実行中」のまま、実行のプロセスがもう無いものを「終了不明」として閉じ、同時実行の枠を戻す。
    PC の再起動・プロセスの異常終了で終了の記録が途切れると、枠を占め続けて自律運転が止まるため（外部レビュー #8）。
    時間だけでは閉じない（本当に長く動いているものと二重に走らせない）。"""
    closed = []
    for r in work.running_runs(root):
        rid = r.get("実行ID", "")
        if only and rid != only:
            continue
        sp = state_path(root, rid)
        try:
            extra = json.loads(sp.read_text(encoding="utf-8"))
        except FileNotFoundError:
            extra = None
        except (OSError, ValueError):
            continue   # 書きかけ・読めない：生きているかもしれないので閉じない
        if extra and extra.get("host") and extra["host"] != _host():
            continue   # 別の PC で起動した実行（memory/ を同期している）：ここではプロセスを確かめられない
        pids = [p for p in ((extra or {}).get("pid"), (extra or {}).get("child")) if p]
        if not pids:
            # 起動の記録（pid）が無い：起動の途中か、.state が消された（生きているか確かめようがない）。
            # どちらも自動では閉じない。時間がたったものは read_status が「確かめられない」と出し、人が --stop 実行ID で閉じる
            continue
        elif any(_ours(p, extra.get("start", 0)) for p in pids):
            continue
        if not work.run_end(root, rid, "終了不明", None, expect=("実行中",)):
            continue   # 調べている間に実行役が終わりを書いた
        work.note(root, r["task"], f"{rid} は終了の記録が無いまま、実行のプロセスが無くなっていた（PC の再起動・異常終了など）。"
                  "報告があれば確認役が見る。無ければやり直す", info=True)
        closed.append(rid)
    return closed


def check_task(root: Path, cid: str, child: bool = False, anyway: bool = False) -> dict:
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
    if anyway and os.environ.get("AI_AGENCY_UNATTENDED"):
        raise ValueError("着手条件を外す（--anyway）のは人の判断。無人の実行では使わない（人に確認する）")
    if not child and not anyway:
        unmet = work.unmet_conditions(root, cid)
        if unmet:   # 前工程・待ちがそろうまで起動しない（外部レビュー #10。人が判断したときだけ --anyway）
            raise ValueError(f"{cid} の着手条件がそろっていない：{'／'.join(unmet)}。人が判断して始めるなら --anyway（ボードは確認して着手）")
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
    # 今回の上限は指示にも書く（作業依頼の「上限」は割り振り役の既定。ボードで今回だけ別の額にすることがある）
    return [exe, "-p", f"/run-task {cid} --run {rid} --review {review} --budget {budget:g}", "--allowedTools", *allowed_tools(),
            "--max-budget-usd", f"{budget:g}", "--no-session-persistence", "--output-format", "json"]


def default_budget(root: Path, review: str = "auto", cid: str | None = None) -> float:
    """1回の上限。タスクが分かれば work.task_budget（環境変数 → 作業依頼の上限 → settings.md）。
    無ければ環境変数 MAX_BUDGET_USD、settings.md（レビューありは作成・レビュー・直しの3人分）。"""
    if cid:
        return work.task_budget(root, cid, review)
    if work.env_run_budget() is not None:
        return work.env_run_budget()   # 範囲（1〜20）に揃える。work.task_budget（起動待ちの見込み）と同じ決め方
    lim = work.limits(root)
    return lim["run_usd"] if review == "none" else lim["run_usd_review"]


MIN_BUDGET_USD = work.RUN_MIN_USD   # 作業セッションは起動だけで 0.30〜0.54 USD 使った（実測）。上限 0.5 では起動だけで打ち切られ報告も残らない


def check_budget(budget: float) -> None:
    if not MIN_BUDGET_USD <= budget <= work.RUN_MAX_USD:
        raise ValueError(f"1回の上限は {MIN_BUDGET_USD:g}〜{work.RUN_MAX_USD:g} USD（作業セッションは起動だけで 0.3〜0.55 USD 使い、それより少ない上限では報告も残らない）")


def check_capacity(root: Path, budget: float) -> None:
    """起動の前の見込み（ボードの表示・早めの断り用）。最後の判定と記録は start_run（ロックの中）で行う。"""
    check_budget(budget)
    reap(root)
    lim = work.limits(root)
    running = work.running_runs(root)
    if len(running) >= lim["max_running"]:
        raise ValueError(f"同時実行の上限 {lim['max_running']} に達している（実行中：{', '.join(r['実行ID'] for r in running)}）")
    spent, res = work.spent_today(root), work.reserved(root)
    if spent + res + budget > lim["daily_usd"]:
        raise ValueError(f"今日の費用 {spent:g} USD＋実行中の予約 {res:g} USD＋今回の上限 {budget:g} USD が"
                         f"1日の上限 {lim['daily_usd']:g} USD を超える")


def start_run(root: Path, cid: str, budget: float, anyway: bool = False) -> str:
    try:
        return work.start_run_within_limits(root, cid, budget, anyway)
    except work.MemError as e:
        raise ValueError(str(e)) from e


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


def _state_update(root: Path, rid: str, **fields) -> None:
    p = state_path(root, rid)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.update(fields, host=_host())
    work.mem.write_text_atomic(p, json.dumps(st))   # 書きかけを読ませない（後始末が生きている実行を閉じないように）


def run_foreground(root: Path, cid: str, rid: str, budget: float) -> int:
    check_task(root, cid, child=True)
    cmd = build_command(cid, rid, budget, plan_of(root, cid).get("review", "auto"))
    results_dir(root).mkdir(parents=True, exist_ok=True)
    _state_update(root, rid, start=round(time.time(), 1), pid=os.getpid(), budget=budget)
    env = dict(os.environ, AI_AGENCY_UNATTENDED="1", AI_AGENCY_TASK=cid, AI_AGENCY_RUN=rid, PYTHONUTF8="1")
    cost, subtype = None, ""
    with open(state_path(root, rid, "log"), "w", encoding="utf-8") as log:
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] start {rid}\n")
        log.flush()
        try:
            pr = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", errors="replace")
            _state_update(root, rid, child=pr.pid)   # claude 本体の pid（実行役が落ちても生きているかを確かめる）
            stdout, _ = pr.communicate()
            code = pr.returncode
            log.write(summarize_output(stdout) + "\n")
            cost = cost_of(stdout)
            subtype = str(_parse_output(stdout).get("subtype") or "")
        except OSError as e:
            log.write(f"起動できない: {e}\n")
            code = 127
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] exit {code}" + (f" cost {cost:.2f} USD" if cost is not None else "") + "\n")
    _state_update(root, rid, end=round(time.time(), 1), exit=code)
    has_result = (results_dir(root) / f"{rid}.md").exists()
    result = "完了" if code == 0 and has_result else ("上限で打ち切り" if "budget" in subtype else "失敗")
    # 停止が記録済みなら上書きしない。後始末が誤って終了不明にしていたら、こちらの方が確かなので書き直す
    if not work.run_end(root, rid, result, cost, expect=("実行中", "終了不明")):
        return code
    if result == "上限で打ち切り" and not has_result:   # 報告が無いと次の実行も人も手がかりが無いので、作業記録に残す
        work.note(root, cid, f"{rid} は上限 {budget:g} USD で打ち切られ、報告が残っていない"
                  f"（費用 {cost if cost is not None else '不明'} USD）。上限を上げるか、作業を分けてやり直す", info=True)
    return code


def launch(root: Path, cid: str, budget: float | None = None, review: str | None = None, anyway: bool = False) -> dict:
    """新しい作業セッションを裏で起動して、すぐ戻る。review を渡すと進め方（レビュー）も更新する。"""
    row = check_task(root, cid, anyway=anyway)
    if review is not None:
        check_review(root, row, review)
    work.ensure_task(root, cid)   # 作業依頼が無ければ台帳から最小のものを作る
    rv = review.lstrip("@") if review is not None else plan_of(root, cid).get("review", "auto")
    budget = budget if budget is not None else default_budget(root, rv, cid)
    check_capacity(root, budget)
    exe_ok = build_command(cid, "x", budget)   # claude が無ければここで止める
    del exe_ok
    if review is not None and plan_of(root, cid).get("review") != rv:
        set_plan(root, cid, rv, "ボードで指定")
    rid = start_run(root, cid, budget, anyway)
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
        work.run_end(root, rid, "失敗", 0)   # 起動できなかった：費用はかかっていない
        raise ValueError(f"起動できない: {e}") from e
    _state_update(root, rid, start=round(time.time(), 1), pid=p.pid, budget=budget)
    return {"id": cid, "run": rid, "agent": row.get("相手"), "budget": budget, "review": rv}


def stop(root: Path, ident: str, reason: str = "") -> str:
    """実行中のセッションを止める（プロセスごと）。実行履歴は「停止」。ident は C-ID（そのタスクで実行中の最新）か
    実行ID（古い実行が「実行中」のまま残っているときに個別に閉じる）。"""
    m = work.RUN_RE.fullmatch(ident or "")
    cid = m.group(1) if m else ident
    running = [r for r in work.read_task(root, cid).get("runs", []) if r.get("結果") == "実行中"]
    run = next((r for r in running if r.get("実行ID") == ident), None) if m else (running[-1] if running else None)
    if not run:
        raise ValueError(f"{ident} は実行中ではない")
    rid = run["実行ID"]
    try:
        extra = json.loads(state_path(root, rid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        extra = {}
    for pid in (extra.get("pid"), extra.get("child")):
        if not pid or (extra.get("host") and extra["host"] != _host()) or not _ours(pid, extra.get("start", 0)):
            continue   # もういない・番号が使い回されている・別の PC の実行：殺さない
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    work.run_end(root, rid, "停止", None, expect=("実行中",))
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
    ap.add_argument("--anyway", action="store_true", help="着手条件（前工程・待ち）がそろっていなくても始める（人が判断したとき）")
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
            b = a.budget if a.budget is not None else default_budget(root, plan_of(root, a.id).get("review", "auto"), a.id)
            print(" ".join(build_command(a.id, "C-xxx-Rn", b, plan_of(root, a.id).get("review", "auto"))))
            return
        if a.child:
            sys.exit(run_foreground(root, a.id, a.child, a.budget or default_budget(root, cid=a.id)))
        if a.wait:
            info = launch_wait(root, a.id, a.budget, a.review, a.anyway)
            sys.exit(info)
        info = launch(root, a.id, a.budget, a.review, a.anyway)
        print(f"▶ {info['run']} を {info['agent']} の作業セッションで起動した（レビュー {info['review']}・上限 {info['budget']:g} USD）。"
              f"報告は memory/work/results/{info['run']}.md")
    except (ValueError, work.MemError) as e:
        sys.exit(f"✗ {e}")


def launch_wait(root: Path, cid: str, budget: float | None, review: str | None, anyway: bool = False) -> int:
    """前景で実行する（定期実行やテスト用）。"""
    row = check_task(root, cid, anyway=anyway)
    if review is not None:
        set_plan(root, cid, review, "指定")
    rv = plan_of(root, cid).get("review", "auto")
    budget = budget if budget is not None else default_budget(root, rv, cid)
    check_capacity(root, budget)
    build_command(cid, "x", budget)
    del row
    rid = start_run(root, cid, budget, anyway)
    return run_foreground(root, cid, rid, budget)


if __name__ == "__main__":
    main()
