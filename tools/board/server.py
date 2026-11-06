#!/usr/bin/env python3
"""
board/server.py ── memory/ を「見る・触る」ためのローカルボード（標準ライブラリのみ）。

- 読み取り：memory/ の markdown を解析して JSON で返す（dashboard / situation / inbox / 約束台帳 / 案件 / 人物 / 決定 / 操作ログ）
- 書き込み：tools/mem.py と**同じ関数**を呼ぶ（markdown が唯一の正典のまま。二重ストアを作らない）
- 127.0.0.1 にだけバインドする。認証は無い（ローカル専用）
- ブラウザで開いている別のサイトから読み書きされないよう、Host がこの端末の名前であること（DNS リバインディング対策）、
  書き込みは Content-Type が application/json で、Origin があれば自分自身であること（CSRF 対策）を確かめる

起動：python tools/board/server.py [--port 8765] [--root <リポジトリルート>]
Claude Code デスクトップでは .claude/launch.json の "board" で preview_start できる。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from argparse import Namespace
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent


def load_mem():
    spec = importlib.util.spec_from_file_location("mem", HERE.parent / "mem.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


mem = load_mem()


def load_recall():
    spec = importlib.util.spec_from_file_location("recall", HERE.parent / "recall.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules["recall"] = mod  # dataclass が自モジュールを引けるように
    spec.loader.exec_module(mod)
    return mod


recall = load_recall()


def load_run_task():
    spec = importlib.util.spec_from_file_location("run_task", HERE.parent / "run_task.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


run_task = load_run_task()
work = run_task.work


def api_knowledge(root: Path) -> dict:
    rmd = text(root / "memory" / "knowledge" / "rules.md")
    rules = recall.md_rows(rmd, "## ルール") if rmd else []
    for r in rules:
        cd = recall.to_date(r.get("確認日", ""))
        r["confirmed_days"] = (date.today() - cd).days if cd else None
    return {"terms": recall.glossary(root), "rules": rules}


def api_review(root: Path) -> dict:
    fs = recall.review_findings(root)
    return {"findings": [f.__dict__ for f in fs], "auto": sum(f.auto for f in fs), "ask": sum(not f.auto for f in fs)}


def api_search(root: Path, qs: dict) -> dict:
    q = (qs.get("q") or [""])[0].strip()
    if not q:
        return {"resolved": [], "total": 0, "hits": []}
    types = set((qs.get("type") or [""])[0].split(",")) - {""} or None
    return recall.find_hits(root, q.split(), types, (qs.get("project") or [None])[0] or None,
                            None, (qs.get("archive") or [""])[0] == "1", 20)


# ---------------------------------------------------------------- readers
def text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def field(md: str, label: str) -> str:
    m = re.search(rf"^\s*-\s*\*\*{re.escape(label)}\*\*:\s*(.*)$", md, re.M)
    return m.group(1).strip() if m else ""


def table_after(md: str, heading_substr: str) -> list[dict]:
    lines = md.splitlines()
    sec = mem.find_section(lines, heading_substr)
    if sec < 0:
        return []
    h, e = mem.find_table(lines, sec)
    if h < 0:
        return []
    hdr = mem.cells(lines[h])
    rows = []
    for ln in lines[h + 2:e]:
        if mem.PLACEHOLDER_RE.match(ln):
            continue
        rows.append(dict(zip(hdr, mem.cells(ln))))
    return rows


def days_until(s: str) -> int | None:
    m = re.search(r"\d{4}-\d{2}-\d{2}", s or "")
    if not m:
        return None
    try:
        return (datetime.strptime(m.group(0), "%Y-%m-%d").date() - date.today()).days
    except ValueError:
        return None


def frontmatter(md: str) -> dict:
    """--- で囲まれた先頭ブロックを最小限に読む（name / description / icon / color と skills のリスト）。"""
    m = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*\n", md, re.S)
    out: dict = {}
    if not m:
        return out
    key = None
    for ln in m.group(1).splitlines():
        if re.match(r"^\s+-\s+", ln) and key:
            out.setdefault(key, []).append(ln.split("-", 1)[1].strip())
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", ln)
        if kv:
            key, val = kv.group(1), kv.group(2).strip().strip("'\"")
            out[key] = val if val else []
    return out


CHIEF = {"handle": "chief", "name": "chief", "role": "第一秘書（司会・統合）", "icon": "crown", "color": "",
         "description": "ユーザーと話す唯一の顔。専門家を召集し、記憶へ書くのは @chief だけ。", "skills": []}


def api_agents(root: Path) -> dict:
    out = [dict(CHIEF)]
    adir = root / ".claude" / "agents"
    if adir.exists():
        for f in sorted(adir.glob("*.md")):
            md = text(f)
            fm = frontmatter(md)
            body = md.split("---", 2)[-1] if md.startswith("---") else md
            role = re.search(r"あなたはAI秘書チームの(.+?)（@", body)
            out.append({
                "handle": fm.get("name") or f.stem, "name": fm.get("name") or f.stem,
                "role": role.group(1) if role else "",
                "description": fm.get("description", "") if isinstance(fm.get("description"), str) else "",
                "icon": fm.get("icon", "") if isinstance(fm.get("icon"), str) else "",
                "color": fm.get("color", "") if isinstance(fm.get("color"), str) else "",
                "skills": fm.get("skills", []) if isinstance(fm.get("skills"), list) else [],
                "path": f"{f.parent.parent.name}/{f.parent.name}/{f.name}",
            })
    return {"agents": out}


def api_summary(root: Path) -> dict:
    dash = text(root / "memory" / "dashboard.md")
    sit = text(root / "memory" / "situation.md")
    m = re.search(r"\*\*最終更新\*\*:\s*(\S+)", sit)
    return {
        "name": field(dash, "名前"),
        "nickname": field(dash, "呼び方"),
        "role": field(dash, "役職・担当領域"),
        "projects": table_after(dash, "進行中プロジェクト一覧"),
        "people": table_after(dash, "重要な人物"),
        "situation_updated": m.group(1) if m else "",
        "today": date.today().isoformat(),
        "is_template": (root / ".ai-agency-template").exists(),
    }


def api_brief(root: Path) -> dict:
    bdir = root / "memory" / "briefs"
    files = sorted(bdir.glob("????-??-??.md")) if bdir.exists() else []
    if not files:
        return {"date": "", "markdown": "", "age": None}
    f = files[-1]
    return {"date": f.stem, "markdown": text(f), "age": -(days_until(f.stem) or 0)}


def api_situation(root: Path) -> dict:
    return {"markdown": text(root / "memory" / "situation.md")}


def api_inbox(root: Path) -> dict:
    md = text(root / "memory" / "inbox.md")
    pending, resolved = [], []
    pat = re.compile(r"^- \[( |x)\] (I-\d+) (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) 「(.*?)」（出所: (.*?)）(?: → 記録先: (.*?)（(\d{4}-\d{2}-\d{2})）)?\s*$")
    for ln in md.splitlines():
        m = pat.match(ln)
        if not m:
            continue
        item = {"id": m.group(2), "at": m.group(3), "text": m.group(4), "source": m.group(5)}
        if m.group(1) == "x":
            item.update({"to": m.group(6) or "", "resolved_at": m.group(7) or ""})
            resolved.append(item)
        else:
            pending.append(item)
    return {"pending": pending, "resolved": resolved[:30]}


RUN_STALE_SEC = {"running": 2 * 3600, "background": 6 * 3600}


def agent_runs(root: Path) -> list[dict]:
    """agent-activity.py の記録（memory/task-log/agent-runs.jsonl）を、起動1回＝1件にまとめる。"""
    log = root / "memory" / "task-log" / "agent-runs.jsonl"
    try:
        lines = log.read_text(encoding="utf-8").splitlines()[-3000:]
    except OSError:
        return []
    runs, by_tuid = [], {}
    # 別々のフックのプロセスが書くので、記録は届く順が入れ替わる・重複することがある（外部レビュー #11）：
    # 終了（SubagentStop）が起動の応答（launched）より先に届いたら、aid で覚えておき、後から来た launched で閉じる
    early_stops: dict = {}   # (会話, aid) → 終了の記録
    closed_aids: set = set()   # 閉じた (会話, aid)。同じ終了が2回届いても別の実行を閉じない
    now = datetime.now().timestamp()
    for ln in lines:
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        ev = e.get("ev")
        if ev == "start":
            r = {"tuid": e.get("tuid"), "sid": e.get("sid", ""), "agent": e.get("agent", ""), "desc": e.get("desc", ""),
                 "tasks": e.get("tasks", []), "bg": e.get("bg", False), "start": e.get("ts"), "end": None,
                 "state": "running", "unattended": e.get("unattended", False)}
            runs.append(r)
            if r["tuid"]:
                by_tuid[r["tuid"]] = r
        elif ev in ("end", "launched", "fail") and e.get("tuid") in by_tuid:
            r = by_tuid[e["tuid"]]
            if ev in ("end", "fail") and r["state"] == "running":
                # 前景の終了だけが自分の stop を取り除く。背景に回った実行（launched のあと）に遅れて届いた end は、
                # 本物の SubagentStop を「重複」として捨てさせない（R-044 の独立レビュー）
                _drop_own_stop(early_stops, closed_aids, r, e)
            if ev == "end" and r["state"] == "running":
                r.update(state="done", end=e.get("ts"))
            elif ev == "launched" and r["state"] == "running":   # 重複した応答で、閉じた実行を「動いている」に戻さない
                r.update(state="background", aid=e.get("aid", ""), bg=True)   # 前景で呼んで背景に回ったもの（moved）も背景として結ぶ
                st = early_stops.pop((r["sid"], r["aid"]), None) if r["aid"] else None
                if st:   # 終了のほうが先に届いていた
                    r.update(state="done", end=st.get("ts"))
                    closed_aids.add((r["sid"], r["aid"]))
            elif ev == "fail":
                r.update(state="failed", end=e.get("ts"))
        elif ev == "stop":  # バックグラウンドで動いていたものの終了：起動の応答の agentId で突き合わせる
            key = (e.get("sid", ""), e.get("aid", ""))
            if e.get("aid") and key in closed_aids:
                continue   # 同じ終了の重複
            alive = [r for r in runs if r["sid"] == e.get("sid") and r["state"] == "background"]
            hit = next((r for r in alive if e.get("aid") and r.get("aid") == e.get("aid")), None)
            if hit:
                hit.update(state="done", end=e.get("ts"))
                closed_aids.add(key)
            elif any(r["sid"] == e.get("sid") and r["agent"] == e.get("agent") and r["state"] == "failed"
                     and 0 <= (e.get("ts") or 0) - (r["end"] or 0) <= 10 for r in runs):
                continue   # 失敗した起動の後から届いた終了（その起動のもの）。近似で別の実行を閉じない
            elif e.get("aid"):
                early_stops.setdefault(key, e)   # 起動の応答がまだ：後から来る launched で閉じる（近似で別の実行を閉じない）
            else:
                _close_legacy(runs, e)
    for e in sorted(early_stops.values(), key=lambda x: x.get("ts") or 0):
        # aid の合う起動の応答が来ないまま時間がたった終了：aid の無い記録（応答に agentId が無かった・応答の記録が欠けた）に
        # 近似で当てる。届いたばかりのものは、応答がまだ書かれていないだけかもしれないので待つ（別の実行を閉じない）
        if now - (e.get("ts") or now) >= EARLY_STOP_GRACE_SEC:
            _close_legacy(runs, e)
    for r in runs:
        lim = RUN_STALE_SEC.get(r["state"])
        if lim and now - (r["start"] or 0) > lim:
            r["state"] = "unknown"  # 終了の記録が無いまま時間がたった（会話が途中で閉じた等）
        r["elapsed"] = round(((r["end"] or now) - (r["start"] or now)))
        r["start_hm"] = datetime.fromtimestamp(r["start"]).strftime("%m/%d %H:%M") if r["start"] else ""
    return runs


EARLY_STOP_GRACE_SEC = 30   # 起動の応答（launched）は起動の約0.3秒後に書かれる。これだけ待っても来なければ欠けたとみなす


def _close_legacy(runs: list[dict], e: dict) -> None:
    """aid で結べない終了：同じ会話・同じ種類で、その終了より前に起動し、aid の無いまま動いている背景の実行のうち
    最も古いものを閉じる（近似）。起動の応答が欠けて「動いている（running）」のままの背景の実行も対象。"""
    ts = e.get("ts") or 0
    cands = [r for r in runs if r["sid"] == e.get("sid") and r["agent"] == e.get("agent") and not r.get("aid")
             and r.get("bg") and r["state"] in ("background", "running") and (r["start"] or 0) <= ts]
    # 起動の応答がまだ（欠けた）背景の実行を先に：aid 付きの終了は、応答の届いていない起動のものである見込みが高い
    hit = next((r for r in cands if r["state"] == "running"), None) or next(iter(cands), None)
    if hit:
        hit.update(state="done", end=ts)


def _drop_own_stop(early_stops: dict, closed_aids: set, r: dict, e: dict) -> None:
    """前景の実行の終了（end・fail）が届いた：先に届いていたその実行の stop を、近似に回らないよう取り除く。
    aid があれば aid で、無い古い記録は同じ会話・同じ種類で起動から終了までの間の stop を1つ。"""
    if e.get("aid"):
        early_stops.pop((r["sid"], e["aid"]), None)
        closed_aids.add((r["sid"], e["aid"]))
        return
    lo, hi = r["start"] or 0, (e.get("ts") or 0) + 1
    key = next((k for k, x in early_stops.items() if k[0] == r["sid"] and x.get("agent") == r["agent"]
                and lo <= (x.get("ts") or 0) <= hi), None)
    if key:
        early_stops.pop(key)
        closed_aids.add(key)


def review_of(md: str) -> dict:
    """報告の「## レビュー」節から、レビュアーと判定を取る（independent-review の形）。"""
    m = re.search(r"^## レビュー\s*$(.*?)(?=^## |\Z)", md, re.M | re.S)
    if not m:
        return {}
    sec = m.group(1)
    who = re.search(r"レビュアー[:：]\s*(@[a-z0-9-]+|なし)", sec)
    verdict = re.search(r"判定[:：]\s*([^\n／|]+)", sec)
    rests = re.findall(r"残った指摘[^:：\n]*[:：][ \t]*(.*)", sec)
    if verdict:
        v = verdict.group(1).replace("*", "").strip()
    elif (who and who.group(1) == "なし") or re.search(r"レビュー[:：]\s*なし", sec):
        v = "なし"
    else:
        v = ""
    return {"reviewer": who.group(1) if who else "", "verdict": v,
            "left": any(not r.strip().startswith("なし") for r in rests)}   # 「残った指摘（重大）:」の下に箇条書きの形も


def api_task_result(root: Path, cid: str) -> dict:
    """任せたタスクの最新の報告（memory/work/results/{実行ID}.md）・状態・ログの末尾・作業記録（途中の変更・引き継ぎ・実行履歴）。"""
    if not re.fullmatch(r"C-\d{3,}", cid or ""):
        return {"error": "ID が不正"}
    st = run_task.read_status(root, cid)
    rid = st.get("run", "")
    log = text(run_task.state_path(root, rid, "log")).splitlines()[-15:] if rid else []
    t = work.read_task(root, cid)
    return {"id": cid, "run": rid, "text": text(run_task.results_dir(root) / f"{rid}.md") if rid else "", "status": st, "log": log,
            "notes": t.get("notes", []), "handoff": t.get("handoff", ""), "runs": t.get("runs", []), "order": t.get("order", {}),
            "refs": t.get("refs", [])}


def api_work(root: Path) -> dict:
    """チームの自動運転：上限と今日の費用、実行中、確認待ちの実行、変化、提案（未判定・要承認・最近の判定）。"""
    st = work.status(root)
    props = work.proposal_rows(root)
    last = {}
    try:
        last = json.loads((work.state_dir(root) / "last-dispatch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {**st, "proposals": props[::-1][:40], "last_dispatch": last.get("finished") or last.get("when", ""), "last_skipped": last.get("skipped", ""),
            "dispatch_log": text(root / "memory" / "briefs" / "dispatch.md")[-4000:]}


def unattended_status(root: Path) -> dict:
    """無人実行（朝のブリーフ）の最後の実行。"""
    t = text(root / "memory" / "briefs" / "_last-run.log")
    starts = re.findall(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] start", t)
    exits = re.findall(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] exit (\d+)", t)
    if not starts:
        return {}
    return {"start": starts[-1], "exit": exits[-1][1] if exits else None, "end": exits[-1][0] if exits else None,
            "running": not exits or exits[-1][0] < starts[-1]}


def api_activity(root: Path) -> dict:
    runs = agent_runs(root)
    today_s = date.today().isoformat()
    by_agent: dict = {}
    for r in runs:
        a = by_agent.setdefault(r["agent"], {"running": 0, "today": 0, "last": "", "last_desc": ""})
        if r["state"] in ("running", "background"):
            a["running"] += 1
        if r["start"] and datetime.fromtimestamp(r["start"]).date().isoformat() == today_s:
            a["today"] += 1
        a["last"], a["last_desc"] = r["start_hm"], r["desc"]
    running = [r for r in runs if r["state"] in ("running", "background")]
    recent = [r for r in runs if r["state"] not in ("running", "background")][-30:][::-1]
    return {"running": running[::-1], "recent": recent, "by_agent": by_agent, "unattended": unattended_status(root)}


def api_commitments(root: Path) -> dict:
    rows = mem.commitment_rows(root) if (root / "memory" / "commitments.md").exists() else []
    runs = agent_runs(root)
    try:
        changes_all = work.changes(root)
    except Exception:  # noqa: BLE001 ── 変化の検知に失敗してもタスク一覧は出す
        changes_all = []
    try:
        nmap = work.needs_map(root)   # 不足の約束 → 止めている仕事（逆引き。正本は仕事の側の参照）
    except Exception:  # noqa: BLE001
        nmap = {}
    states = {r.get("ID"): r.get("状態") for r in rows}
    by_id = {r.get("ID"): r for r in rows}
    out = []
    for r in rows:
        d = dict(r)
        d["content"] = r.get("タスク") or r.get("内容") or ""
        d["who"] = r.get("依頼者") or r.get("相手") or ""
        d["due_in"] = days_until(r.get("期限", ""))
        d["follow_in"] = days_until(r.get("フォロー日", "")) if "フォロー日" in r else None
        # 誰がやるか：自分（自分のタスク・受けた指示）／人（人に出した指示）／エージェント（@handle に任せた）
        if r["kind"] == "given":
            d["assignee"] = "agent" if d["who"].startswith("@") else "person"
        else:
            d["assignee"] = "self"
        mine = [x for x in runs if r.get("ID") in x["tasks"]]
        d["runs"] = len(mine)
        # 作業セッション（tools/run_task.py）の最新の実行・報告・関連情報の変化
        task = run_task.read_status(root, r.get("ID", "")) if d["assignee"] == "agent" else {}
        d["task_run"] = task
        d["has_result"] = bool(task.get("has_result"))
        d["plan"] = task.get("plan") or {}
        if d["assignee"] == "agent":
            lim = work.limits(root)
            d["budget"] = {"order": work.order_budget(root, r.get("ID", "")), "env": work.env_run_budget(),
                           "none": lim["run_usd"], "review": lim["run_usd_review"]}
        d["review"] = review_of(text(run_task.results_dir(root) / f"{task.get('run')}.md")) if d["has_result"] else {}
        d["changes"] = sorted({c["kind"] for c in changes_all if c["task"] == r.get("ID")})
        open_ = r.get("状態") not in mem.CLOSED
        # 仕事を止めている不足（そろっていないもの）と、この約束が不足として止めている仕事（外部レビュー #12）
        d["needs"] = work.open_needs(root, r.get("ID", "")) if open_ and work.task_path(root, r.get("ID", "")).exists() else []
        d["blocks"] = [c for c in nmap.get(r.get("ID", ""), []) if states.get(c) not in mem.CLOSED] if open_ else []
        try:
            h = work.human_need(root, r.get("ID", ""), by_id, nmap) if r.get("ID") in nmap else None
            if h:   # 人・相手の不足：答える・無しで進める・下書き・送った（段階3）
                rec = work.read_need_rec(root, r.get("ID", ""))
                d["need"] = {"kind": h["kind"], "who": h["who"], "blocks": h["blocks"],
                             "answer": rec["answers"][-1] if rec["answers"] else None,
                             "unanswered": not open_ and not rec["answers"],   # 答えの記録が無いまま閉じた（答えを残せる）
                             "draft": rec["drafts"][-1] if rec["drafts"] else None, "drafts": len(rec["drafts"])}
            for n in d["needs"]:
                rec = work.read_need_rec(root, n["id"])
                sent_ = [x for x in rec["drafts"] if x.get("送った")]
                n["sent"] = sent_[-1]["送った"][:10] if sent_ else ""   # 催促の版（未送信）ができても、送ってある日は消さない
                n["drafted"] = bool(rec["drafts"])
            if d["assignee"] == "agent" and open_ and work.task_path(root, r.get("ID", "")).exists():
                d["answered"] = [{"id": x["id"], "kind": x["answer"].get("kind"), "at": (x["answer"].get("at") or "")[:10]}
                                 for x in work.needs_view(root, r.get("ID", "")) if x.get("answer") and x["answer"].get("at")]
        except work.MemError:
            pass
        if task.get("run"):
            # 作業セッション（run_task）の実行があるタスクは、その状態が正本。中で動いたサブエージェント（無人の記録）は
            # 止めた・落ちたあとも「稼働中」「終わった」で残り、停止・終了不明を隠していた（R-024 の独立レビュー）。
            # 人の会話から任せた分（無人でない記録）だけを見る
            mine = [x for x in mine if not x.get("unattended")]
        if task.get("state") == "running" or any(x["state"] in ("running", "background") for x in mine):
            d["agent_state"] = "running"
        elif open_ and d["needs"]:
            d["agent_state"] = "needs"   # 不足がそろうまで、承認も起動もしない（報告があっても「確認待ち」より先に出す）
        elif open_ and task.get("state") == "failed":
            d["agent_state"] = "failed"
        elif open_ and d["has_result"]:
            d["agent_state"] = "review"   # エージェントは終わった。人が報告を確かめて完了にする
        elif open_ and task.get("state") in ("unknown", "stopped"):
            d["agent_state"] = task["state"]   # 終了不明（プロセスが消えた・費用は予約額で数えた）／人が止めた
        elif open_ and mine and mine[-1]["state"] == "done":
            d["agent_state"] = "review"   # 人の会話から任せたサブエージェントが終わった
        elif open_ and mine and mine[-1]["state"] == "failed":
            d["agent_state"] = "failed"
        else:
            d["agent_state"] = ""
        out.append(d)
    return {"rows": out}


def api_projects(root: Path) -> dict:
    out = []
    pdir = root / "memory" / "projects"
    rels, totals = {}, {}
    for r in (mem.commitment_rows(root) if (root / "memory" / "commitments.md").exists() else []):
        for slug in set(re.findall(r"P:([A-Za-z0-9_-]+)", r.get("関連", ""))):
            totals[slug] = totals.get(slug, 0) + 1
            if r.get("状態") not in mem.CLOSED:
                rels[slug] = rels.get(slug, 0) + 1
    if pdir.exists():
        for d in sorted(p for p in pdir.iterdir() if p.is_dir() and not p.name.startswith("_")):
            ov = text(d / "overview.md")
            st = text(d / "status.md")
            dec = text(d / "decisions.md")
            out.append({
                "slug": d.name,
                "title": (re.search(r"^#\s+(.*)$", ov, re.M) or [None, d.name])[1],
                "purpose": field(ov, "目的"), "state": field(ov, "状態"),
                "deadline": field(ov, "期限"), "deadline_in": days_until(field(ov, "期限")),
                "start": field(ov, "開始日"), "start_in": days_until(field(ov, "開始日")),
                "priority": field(ov, "優先度"),
                "members": table_after(ov, "関係者"),
                "coordinates": table_after(ov, "サービス座標"),
                "status_excerpt": "\n".join(st.splitlines()[:25]),
                "decisions_total": len(re.findall(r"^## \d{4}-\d{2}-\d{2}:", dec, re.M)),
                "decisions_proposed": len(re.findall(r"^- \*\*状態\*\*: 提案", dec, re.M)),
                "issues_open": len(re.findall(r"^- \*\*状態\*\*: open", text(d / "issues.md"), re.M)),
                "open_commitments": rels.get(d.name, 0),
                "total_commitments": totals.get(d.name, 0),
            })
    return {"projects": out}


def api_people(root: Path) -> dict:
    out = []
    pdir = root / "memory" / "people"
    if pdir.exists():
        for f in sorted(pdir.glob("*.md")):
            if f.name == "_template.md":
                continue
            md = text(f)
            lines = md.splitlines()
            obs = table_after(md, "判断事例ログ")
            preds = table_after(md, "反応予測と検証")
            lv = re.search(r"\*\*モデル成熟度\*\*:\s*(Lv\d)", md)
            out.append({
                "ident": f.stem,
                "name": field(md, "氏名"), "title": field(md, "役職"), "org": field(md, "所属組織"),
                "relation": field(md, "関係の種別"),
                "icon": re.sub(r"^（.*）$", "", field(md, "アイコン")),
                "maturity": lv.group(1) if lv else "Lv0",
                "observations": len(obs),
                "last_observation": obs[-1].get("日付", "") if obs else "",
                "pending_predictions": sum(1 for p in preds if "未検証" in p.get("実際の反応", "")),
                "approach": field(md, "効果的なアプローチ"),
            })
    return {"people": out}


def api_decisions(root: Path) -> dict:
    out = []
    pdir = root / "memory" / "projects"
    if pdir.exists():
        for d in sorted(pdir.glob("*/decisions.md")):
            md = text(d)
            blocks = re.split(r"^(?=## \d{4}-\d{2}-\d{2}:)", md, flags=re.M)
            for b in blocks:
                m = re.match(r"## (\d{4}-\d{2}-\d{2}): (.*)", b)
                if not m:
                    continue
                out.append({
                    "project": d.parent.name, "date": m.group(1), "title": m.group(2).strip(),
                    "id": field(b, "ID"),
                    "status": field(b, "状態") or "（状態行なし）",
                    "content": field(b, "決定内容"), "prediction": field(b, "予測（期待した結果）"),
                    "verify_date": field(b, "検証日"), "verify_in": days_until(field(b, "検証日")),
                    "result": field(b, "検証結果"),
                })
    out.sort(key=lambda x: x["date"], reverse=True)
    return {"decisions": out}


def api_oplog(root: Path) -> dict:
    files = sorted((root / "memory" / "task-log").glob("*.md")) if (root / "memory" / "task-log").exists() else []
    lines = []
    for f in files[-2:]:
        lines += [ln for ln in text(f).splitlines() if ln.startswith("**操作**:")]
    pat = re.compile(r"^\*\*操作\*\*: \[(.+?)\] (.*?) ── (\S+?)（(\d{2}:\d{2}) ?(\w*)）\s*$")
    items = []
    for ln in lines[-60:][::-1]:
        m = pat.match(ln)
        items.append({"category": m.group(1), "target": m.group(2), "status": m.group(3), "time": m.group(4), "sid": m.group(5)}
                     if m else {"category": "", "target": ln.replace("**操作**: ", ""), "status": "", "time": "", "sid": ""})
    return {"lines": lines[-40:][::-1], "items": items}


# ---------------------------------------------------------------- writers（mem.py と同じ関数）
def run_mem(fn, *args, **kwargs) -> dict:
    mem.LAST_OK.clear()
    try:
        fn(*args, **kwargs)
    except (mem.MemError, work.MemError) as e:   # work は mem の別の写しを読むので、例外の型も別
        return {"ok": False, "error": str(e)}
    return {"ok": True, "message": mem.LAST_OK[-1] if mem.LAST_OK else ""}


# 画面からの入力の検証。mem.py も検証するが、ここでは「形として受け付けてよいか」を先に見て 400 で返す
# （型違い・必須欠落・長すぎ・不正な ID や日付で 500 や想定外の書き込みにしない）
FIELD_RULES = {
    "text": (r"(?s).{1,1000}", "1〜1000字"), "source": (r".{1,80}", "1〜80字"), "to": (r".{1,300}", "1〜300字"),
    "content": (r".{1,300}", "1〜300字"), "who": (r".{1,80}", "1〜80字"), "rel": (r".{1,120}", "1〜120字"),
    "situation": (r".{1,300}", "1〜300字"), "judgment": (r".{1,300}", "1〜300字"), "reason": (r".{1,300}", "1〜300字"),
    "insight": (r".{1,300}", "1〜300字"),
    "answer": (r"(?s).{1,1000}", "1〜1000字"), "without": (r"0|1", "0 / 1"), "via": (r".{1,40}", "1〜40字"),
    "address": (r".{1,200}", "1〜200字"), "subject": (r".{1,200}", "1〜200字"), "body": (r"(?s).{1,4000}", "1〜4000字"),
    "version": (r"\d{1,4}", "下書きの版（数）"),
    "id": (r"(C|I|D)-\d{3,}", "C-001 / I-001 / D-001 の形"),
    "kind": (r"task|received|given", "task / received / given"),
    "state": (r"open|in-progress|waiting|done|dropped", "open / in-progress / waiting / done / dropped"),
    "status": (r"proposed|decided|verified", "proposed / decided / verified"),
    "due": (r"\d{4}-\d{2}-\d{2}", "YYYY-MM-DD"), "follow": (r"\d{4}-\d{2}-\d{2}", "YYYY-MM-DD"),
    "ident": (r"[a-z0-9]+(-[a-z0-9]+)+", "英小文字・数字・ハイフンの識別子"),
    "project": (r"[A-Za-z0-9_-]{1,60}", "案件のスラッグ"), "icon": (r"[^|\n]{1,24}", "1〜24字"),
    "title": (r".{1,200}", "1〜200字"),
    "term": (r".{1,60}", "1〜60字"), "meaning": (r".{1,300}", "1〜300字"), "aliases": (r".{1,120}", "1〜120字"),
    "scope": (r".{1,80}", "1〜80字"), "effective": (r"\d{4}-\d{2}-\d{2}", "YYYY-MM-DD"),
    "kind_term": (r"略語|社内用語|コードネーム|あだ名|業界用語", "略語 / 社内用語 / コードネーム / あだ名 / 業界用語"),
    "rid": (r"R-\d{3,}", "R-001 の形"), "sid": (r"S-\d{3,}", "S-001 の形"),
    "decision": (r"adopt|hold|reject|merge|ask", "adopt / hold / reject / merge / ask"), "task": (r"C-\d{3,}", "C-001 の形"),
    "budget": (r"\d{1,2}(\.\d{1,2})?", "USD の数値"),
    "review": (r"auto|none|@?[a-z0-9][a-z0-9-]*", "auto / none / レビュアーの handle"), "reason": (r".{1,200}", "1〜200字"),
}
REQUIRED = {
    "/api/inbox/add": ["text"], "/api/inbox/resolve": ["id", "to"],
    "/api/commitment/add": ["kind", "content"], "/api/commitment/state": ["id", "state"],
    "/api/commitment/due": ["id", "due"], "/api/commitment/follow": ["id", "follow"],
    "/api/commitment/content": ["id", "content"],
    "/api/person/observe": ["ident", "situation", "judgment"], "/api/person/icon": ["ident", "icon"],
    "/api/person/note": ["ident", "situation", "insight"], "/api/decision/status": ["project", "status"],
    "/api/term/add": ["term", "meaning"], "/api/rule/add": ["content", "source"], "/api/rule/confirm": ["rid"],
    "/api/task/run": ["id"], "/api/task/plan": ["id", "review"], "/api/task/stop": ["id"], "/api/task/approve": ["id"],
    "/api/proposal/decide": ["sid", "decision", "reason"], "/api/proposal/adopt": ["sid", "who"],
    "/api/need/answer": ["id", "answer"], "/api/need/draft": ["id", "body"], "/api/need/sent": ["id", "version"],
}


def validate(path: str, body) -> str | None:
    if not isinstance(body, dict):
        return "本文は JSON オブジェクトで送る"
    for k in REQUIRED.get(path, []):
        if body.get(k) in (None, ""):
            return f"{k} が必要"
    for k, v in body.items():
        if v in (None, ""):
            continue
        if not isinstance(v, str):
            return f"{k} は文字列で送る"
        rule = FIELD_RULES.get(k)
        if rule and not re.fullmatch(rule[0], v.strip()):
            return f"{k} が不正（{rule[1]}）"
    if path == "/api/decision/status" and not (body.get("id") or body.get("title")):
        return "決定の id（推奨）か title が必要"
    return None


def do_post(root: Path, path: str, body: dict) -> tuple[int, dict]:
    if path not in REQUIRED:
        return 404, {"ok": False, "error": "unknown endpoint"}
    err = validate(path, body)
    if err:
        return 400, {"ok": False, "error": err}
    g = body.get
    try:
        if path == "/api/inbox/add":
            res = run_mem(mem.inbox_add, root, g("text", ""), g("source", "ボード"))
        elif path == "/api/inbox/resolve":
            res = run_mem(mem.inbox_resolve, root, g("id", ""), g("to", ""))
        elif path == "/api/commitment/add":
            ns = Namespace(kind=g("kind"), content=g("content"), who=g("who"), due=g("due"),
                           follow=g("follow"), rel=g("rel"), state=g("state"))
            res = run_mem(mem.commitment_add, root, ns)
        elif path == "/api/commitment/state":
            if g("state") == "done":
                # 状態ボタン・かんばんで done にする経路も、任せたタスクの報告が古くないかを承認と同じく確かめる。
                # 確かめてから書くまでを1つのロックで（work.complete_task）
                try:
                    msg = work.complete_task(root, g("id", ""))
                except work.MemError as e:
                    why = str(e)
                    return 400, {"ok": False, "error": why + ("（やり直させる）" if "古い条件" in why else "")}
                res = {"ok": True, "message": msg}
            else:
                try:
                    msg = work.set_task_state(root, g("id", ""), g("state", ""))
                except work.MemError as e:
                    return 400, {"ok": False, "error": str(e)}
                res = {"ok": True, "message": msg}
        elif path == "/api/commitment/due":
            res = run_mem(mem.commitment_set_due, root, g("id", ""), g("due", ""))
        elif path == "/api/commitment/follow":
            res = run_mem(mem.commitment_set_follow, root, g("id", ""), g("follow", ""))
        elif path == "/api/task/run":
            try:
                budget = float(g("budget")) if g("budget") else None
                if budget is not None:
                    run_task.check_budget(budget)
                info = run_task.launch(root, g("id", ""), budget, g("review") or None, g("anyway") == "1")
            except (ValueError, work.MemError) as e:
                return 400, {"ok": False, "error": str(e)}
            res = {"ok": True, "message": f"{info['run']} を {info['agent']} の作業セッションで起動（上限 {info['budget']:g} USD）。終わると報告が届く"}
        elif path == "/api/task/stop":
            try:
                rid = run_task.stop(root, g("id", ""), g("reason", "") or "ボードで停止")
            except (ValueError, work.MemError) as e:
                return 400, {"ok": False, "error": str(e)}
            res = {"ok": True, "message": f"{rid} を止めた"}
        elif path == "/api/task/approve":
            # 人の承認：最新の実行を受け入れ（古い条件なら断る）、約束を完了に。
            # read_status は終わったプロセスの後始末（終了不明で閉じる）のため。判定はロックの中で読み直す（work.complete_task）
            run_task.read_status(root, g("id", ""))
            try:
                msg = work.complete_task(root, g("id", ""), approve=True)
            except work.MemError as e:
                return 400, {"ok": False, "error": str(e)}
            res = {"ok": True, "message": msg}
        elif path in ("/api/need/answer", "/api/need/draft", "/api/need/sent"):
            # 人・相手の不足（段階3）：答える・無しで進める・連絡の下書き・送った。答えの正本は不足の記録（memory/work/needs/）
            try:
                if path == "/api/need/answer":
                    msg = work.answer(root, g("id", ""), g("answer", ""), g("without") == "1", "人（ボード）")
                elif path == "/api/need/draft":
                    msg = work.draft(root, g("id", ""), g("via") or "未定", g("subject") or "", g("body", ""), g("address") or "", "人（ボード）",
                                     int(g("version")) if g("version") else None)
                else:
                    msg = work.sent(root, g("id", ""), g("follow") or None, int(g("version")) if g("version") else None)
            except work.MemError as e:
                return 400, {"ok": False, "error": str(e)}
            res = {"ok": True, "message": msg}
        elif path == "/api/proposal/decide":
            res = run_mem(work.decide, root, g("sid", ""), g("decision", ""), g("task", ""), g("reason", ""))
        elif path == "/api/proposal/adopt":
            # 人が提案を採用：担当（@handle）に任せる約束を足し、提案を採用に
            try:
                cid, made = work.adopt(root, g("sid", ""), g("who", ""), g("content", ""), g("due"), g("rel"))
            except work.MemError as e:
                return 400, {"ok": False, "error": str(e)}
            res = {"ok": True, "id": cid,
                   "message": f"{g('sid')} を採用し {cid} を足した" if made else f"{g('sid')} はもう {cid} として採用済み（新しくは作らない）"}
        elif path == "/api/task/plan":
            try:
                plan = run_task.set_plan(root, g("id", ""), g("review", ""), g("reason", ""))
            except ValueError as e:
                return 400, {"ok": False, "error": str(e)}
            who = {"auto": "秘書が判断", "none": "なし"}.get(plan["review"], "@" + plan["review"])
            res = {"ok": True, "message": f"{g('id')} の進め方：レビュー {who}"}
        elif path == "/api/commitment/content":
            res = run_mem(mem.commitment_set_content, root, g("id", ""), g("content", ""))
        elif path == "/api/person/observe":
            ns = Namespace(ident=g("ident"), situation=g("situation"), judgment=g("judgment"), reason=g("reason"))
            res = run_mem(mem.person_observe, root, ns)
        elif path == "/api/person/icon":
            res = run_mem(mem.person_set_icon, root, g("ident", ""), g("icon", ""))
        elif path == "/api/person/note":
            ns = Namespace(ident=g("ident"), situation=g("situation"), insight=g("insight"))
            res = run_mem(mem.person_note, root, ns)
        elif path == "/api/term/add":
            ns = Namespace(term=g("term"), meaning=g("meaning"), aliases=g("aliases"), kind=g("kind_term"),
                           scope=g("scope"), source=g("source") or "ボード")
            res = run_mem(mem.term_add, root, ns)
        elif path == "/api/rule/add":
            ns = Namespace(content=g("content"), source=g("source"), scope=g("scope"), effective=g("effective"))
            res = run_mem(mem.rule_add, root, ns)
        elif path == "/api/rule/confirm":
            res = run_mem(mem.rule_confirm, root, g("rid", ""))
        elif path == "/api/decision/status":
            res = run_mem(mem.decision_set_status, root, g("project", ""), g("id") or g("title", ""), g("status", ""))
        else:
            return 404, {"ok": False, "error": "unknown endpoint"}
    except Exception as e:  # noqa: BLE001 ── ボードは落とさず理由を返す
        return 500, {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return (200 if res.get("ok") else 400), res


GETS = {
    "/api/summary": api_summary, "/api/situation": api_situation, "/api/inbox": api_inbox,
    "/api/commitments": api_commitments, "/api/projects": api_projects, "/api/people": api_people,
    "/api/decisions": api_decisions, "/api/oplog": api_oplog, "/api/agents": api_agents, "/api/brief": api_brief,
    "/api/review": api_review, "/api/knowledge": api_knowledge, "/api/activity": api_activity, "/api/work": api_work,
}


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def request_allowed(method: str, headers, port: int) -> str:
    """ブラウザ経由の他サイトからの読み書きを断る。問題があれば理由を、無ければ空文字を返す。"""
    host = (headers.get("Host") or "").strip().lower()
    name, _, hport = host.rpartition(":") if not host.endswith("]") else (host, "", "")
    if name not in LOCAL_HOSTS or (hport and hport != str(port)):
        return f"Host「{host}」からは受け付けない"
    if method == "POST":
        if not (headers.get("Content-Type") or "").lower().startswith("application/json"):
            return "書き込みは application/json だけ"
        origin = (headers.get("Origin") or "").strip().lower()
        if origin and origin not in {f"http://{h}:{port}" for h in LOCAL_HOSTS}:
            return f"Origin「{origin}」からの書き込みは受け付けない"
    return ""


class Handler(BaseHTTPRequestHandler):
    root: Path = HERE.parent.parent
    port: int = 8765

    def _guard(self, method: str) -> bool:
        why = request_allowed(method, self.headers, self.port)
        if why:
            self._send(403, {"ok": False, "error": why})
            return False
        return True

    def _send(self, code: int, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if not self._guard("GET"):
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/task/result":
            return self._send(200, api_task_result(self.root, parse_qs(u.query).get("id", [""])[0]))
        if u.path == "/api/search":
            try:
                return self._send(200, api_search(self.root, parse_qs(u.query)))
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        fn = GETS.get(u.path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            return self._send(200, fn(self.root))
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):  # noqa: N802
        if not self._guard("POST"):
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._send(400, {"ok": False, "error": "invalid JSON"})
        code, payload = do_post(self.root, urlparse(self.path).path, body)
        return self._send(code, payload)

    def log_message(self, fmt, *args):  # 静かに（POST だけ出す）
        if args and str(args[0]).startswith("POST"):
            sys.stderr.write("[board] " + (fmt % args) + "\n")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows の cp932 コンソールでも絵文字・日本語ログで落ちないように
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", type=Path, default=None)
    a = ap.parse_args()
    root = (a.root or HERE.parent.parent).resolve()
    if not (root / "memory").is_dir():
        sys.exit(f"{root} に memory/ が無い（--root を確認）")
    Handler.root = root
    Handler.port = a.port
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"[board] http://127.0.0.1:{a.port}/  (memory: {root / 'memory'})  Ctrl+C で停止", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
