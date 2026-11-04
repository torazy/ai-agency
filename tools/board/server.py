#!/usr/bin/env python3
"""
board/server.py ── memory/ を「見る・触る」ためのローカルボード（標準ライブラリのみ）。

- 読み取り：memory/ の markdown を解析して JSON で返す（dashboard / situation / inbox / 約束台帳 / 案件 / 人物 / 決定 / 操作ログ）
- 書き込み：tools/mem.py と**同じ関数**を呼ぶ（markdown が唯一の正典のまま。二重ストアを作らない）
- 127.0.0.1 にだけバインドする。認証は無い（ローカル専用）

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


def api_commitments(root: Path) -> dict:
    rows = mem.commitment_rows(root) if (root / "memory" / "commitments.md").exists() else []
    out = []
    for r in rows:
        d = dict(r)
        d["content"] = r.get("タスク") or r.get("内容") or ""
        d["who"] = r.get("依頼者") or r.get("相手") or ""
        d["due_in"] = days_until(r.get("期限", ""))
        d["follow_in"] = days_until(r.get("フォロー日", "")) if "フォロー日" in r else None
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
    except mem.MemError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "message": mem.LAST_OK[-1] if mem.LAST_OK else ""}


def do_post(root: Path, path: str, body: dict) -> tuple[int, dict]:
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
            res = run_mem(mem.commitment_set_state, root, g("id", ""), g("state", ""))
        elif path == "/api/commitment/due":
            res = run_mem(mem.commitment_set_due, root, g("id", ""), g("due", ""))
        elif path == "/api/commitment/follow":
            res = run_mem(mem.commitment_set_follow, root, g("id", ""), g("follow", ""))
        elif path == "/api/person/observe":
            ns = Namespace(ident=g("ident"), situation=g("situation"), judgment=g("judgment"), reason=g("reason"))
            res = run_mem(mem.person_observe, root, ns)
        elif path == "/api/person/icon":
            res = run_mem(mem.person_set_icon, root, g("ident", ""), g("icon", ""))
        elif path == "/api/person/note":
            ns = Namespace(ident=g("ident"), situation=g("situation"), insight=g("insight"))
            res = run_mem(mem.person_note, root, ns)
        elif path == "/api/decision/status":
            res = run_mem(mem.decision_set_status, root, g("project", ""), g("title", ""), g("status", ""))
        else:
            return 404, {"ok": False, "error": "unknown endpoint"}
    except Exception as e:  # noqa: BLE001 ── ボードは落とさず理由を返す
        return 500, {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return (200 if res.get("ok") else 400), res


GETS = {
    "/api/summary": api_summary, "/api/situation": api_situation, "/api/inbox": api_inbox,
    "/api/commitments": api_commitments, "/api/projects": api_projects, "/api/people": api_people,
    "/api/decisions": api_decisions, "/api/oplog": api_oplog, "/api/agents": api_agents, "/api/brief": api_brief,
    "/api/review": api_review,
}


class Handler(BaseHTTPRequestHandler):
    root: Path = HERE.parent.parent

    def _send(self, code: int, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
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
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"[board] http://127.0.0.1:{a.port}/  (memory: {root / 'memory'})  Ctrl+C で停止", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
