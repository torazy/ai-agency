#!/usr/bin/env python3
"""
recall.py ── memory/ を「探す・読む・点検する」読み取り専用 CLI（書き込みは tools/mem.py）。

秘書が記憶を引くときの段階的開示（CLAUDE.md §6）を道具にしたもの：
  L1 どこにあるか   find / who / map    … 見出しつきの短い一覧だけを返す（件数に上限）
  L2 中身           show                … 当たりをつけたファイルの目次、または1節だけ
  L3 全文・過去分   Read / find --archive / 外部の原本（座標から）

使い方（リポジトリルートで）：
  python tools/recall.py find 比較表 部長 [--type decision,meeting] [--project kintai-saas] [--since 2026-10-01] [--archive] [--limit 12] [--json]
  python tools/recall.py who 部長            # 名前・呼び名・メール → 人物ファイル（0件=新規、2件以上=曖昧）
  python tools/recall.py map [--project kintai-saas | --type people]
  python tools/recall.py show memory/projects/kintai-saas/decisions.md [--section クラウド]
  python tools/recall.py review [--out memory/briefs/review-2026-11.md] [--json]   # 中身の点検（古さ・食い違い・重複・切れた参照・取り出し漏れ）

検索は表記を正規化（全角半角・大小）し、人物は氏名・姓・名・読み・呼び名・メール・「姓＋役職」「姓＋さん」、
案件はスラッグ・名称・別名に展開して探す。単位は「見出しの節」。複数の語は同じ節に揃って出るものを優先する。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from pathlib import Path

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
FIELD_RE = r"^\s*-\s*\*\*{label}\*\*:\s*(.*)$"
EXCLUDE_DIRS = {"task-log", "briefs"}
GENERIC = {"提出", "報告", "対応", "確認", "検討", "作成", "依頼", "共有", "準備", "資料", "会議", "連絡", "調整", "勤怠"}
TYPE_ORDER = ["person", "project", "status", "decision", "commitment", "issue", "meeting", "context",
              "incident", "insight", "memo", "inbox", "situation", "profile", "team", "deliverable", "other"]


# ---------------------------------------------------------------- basics
def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").lower()


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def field(md: str, label: str) -> str:
    m = re.search(FIELD_RE.format(label=re.escape(label)), md, re.M)
    v = m.group(1).strip() if m else ""
    return "" if re.fullmatch(r"（.*）|-|—|", v) else v


def to_date(s: str) -> date | None:
    m = DATE_RE.search(s or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def classify(relpath: str) -> str:
    parts = relpath.split("/")
    name = parts[-1]
    if parts[0] == "deliverables":
        return "deliverable"
    if len(parts) >= 2 and parts[1] == "people":
        return "person"
    if len(parts) >= 3 and parts[1] == "projects":
        if name == "overview.md":
            return "project"
        for key, t in (("status.md", "status"), ("decisions.md", "decision"), ("issues.md", "issue")):
            if name.startswith(key.split(".")[0]):
                return t
        for d, t in (("meetings", "meeting"), ("context", "context"), ("incidents", "incident")):
            if d in parts:
                return t
    if len(parts) >= 2 and parts[1] == "memos":
        return "memo"
    return {"commitments.md": "commitment", "inbox.md": "inbox", "insights.md": "insight",
            "situation.md": "situation", "dashboard.md": "profile", "user-profile.md": "profile"}.get(
        name, "team" if "team" in parts else "other")


def project_of(relpath: str) -> str:
    m = re.match(r"memory/projects/([^/]+)/", relpath)
    return m.group(1) if m else ""


@dataclass
class Doc:
    path: Path
    relpath: str
    type: str
    project: str
    archived: bool
    text: str
    lines: list[str]
    date: date | None


def iter_docs(root: Path, archive: bool = False) -> list[Doc]:
    out = []
    bases = [root / "memory"] + ([root / "deliverables"] if (root / "deliverables").exists() else [])
    for base in bases:
        for p in sorted(base.rglob("*.md")):
            r = rel(root, p)
            parts = set(r.split("/"))
            if parts & EXCLUDE_DIRS or p.name.startswith("_template"):
                continue
            is_arch = "archive" in parts or p.name.startswith("_archive")
            if is_arch and not archive:
                continue
            text = read(p)
            d = to_date(p.name)
            if not d:
                m = re.search(r"\*\*最終更新\*\*:\s*(\d{4}-\d{2}-\d{2})", text)
                d = to_date(m.group(1)) if m else None
            if not d:
                try:
                    d = datetime.fromtimestamp(p.stat().st_mtime).date()
                except OSError:
                    d = None
            out.append(Doc(p, r, classify(r), project_of(r), is_arch, text, text.splitlines(), d))
    return out


# ---------------------------------------------------------------- entities（名前の展開）
@dataclass
class Entity:
    kind: str           # person / project
    key: str            # 人物の識別子 / 案件スラッグ
    label: str          # 表示名
    path: str
    aliases: set[str] = dc_field(default_factory=set)
    info: str = ""


def split_aliases(s: str) -> list[str]:
    return [a.strip() for a in re.split(r"[、,，/／]", s or "") if a.strip() and not a.strip().startswith("（")]


def load_entities(root: Path) -> list[Entity]:
    ents = []
    pdir = root / "memory" / "people"
    for p in sorted(pdir.glob("*.md")) if pdir.exists() else []:
        if p.name.startswith("_"):
            continue
        md = read(p)
        name = field(md, "氏名") or p.stem
        e = Entity("person", p.stem, name, rel(root, p))
        e.aliases.add(p.stem)
        parts = re.split(r"[\s　]+", name.strip())
        e.aliases.add(name)
        e.aliases.add("".join(parts))
        title = field(md, "役職")
        if len(parts) >= 2:
            sei, mei = parts[0], parts[-1]
            # 姓だけでも引ける（同姓が複数なら resolve が「曖昧」を返す）
            e.aliases.update({sei, sei + "さん", mei + "さん", sei + mei})
            if title and len(title) <= 6:
                e.aliases.add(sei + title)
        kana = field(md, "読み仮名")
        if kana:
            e.aliases.add(kana)
            e.aliases.add(kana.replace(" ", "").replace("　", ""))
        for lab in ("メールアドレス", "Slackアカウント名", "Backlogユーザー名", "GitHubアカウント"):
            v = field(md, lab)
            if v and "未登録" not in v:
                e.aliases.add(v)
        for a in split_aliases(field(md, "呼び名")):
            e.aliases.add(a)
        lv = re.search(r"\*\*モデル成熟度\*\*:\s*(Lv\d)", md)
        e.info = " ・ ".join(x for x in (field(md, "所属組織"), title, lv.group(1) if lv else "Lv0") if x)
        e.aliases = {a for a in e.aliases if len(norm(a)) >= 2}
        ents.append(e)
    prj = root / "memory" / "projects"
    for d in sorted(x for x in prj.iterdir() if x.is_dir() and not x.name.startswith("_")) if prj.exists() else []:
        ov = read(d / "overview.md")
        m = re.search(r"^#\s+(.*)$", ov, re.M)
        title = re.sub(r"（[^）]*）$", "", m.group(1)).strip() if m else d.name
        e = Entity("project", d.name, title, rel(root, d / "overview.md"))
        e.aliases.update({d.name, title, "P:" + d.name})
        for a in split_aliases(field(ov, "別名")):
            e.aliases.add(a)
        e.info = " ・ ".join(x for x in (field(ov, "状態"), "期限 " + field(ov, "期限") if field(ov, "期限") else "") if x)
        e.aliases = {a for a in e.aliases if len(norm(a)) >= 2}
        ents.append(e)
    return ents


def resolve(term: str, ents: list[Entity]) -> list[tuple[Entity, str]]:
    """完全一致（正規化後）を優先。無ければ『姓＋役職』の役職だけ等、別名に後方一致する1件に限って採る。"""
    t = norm(term)
    exact = [(e, a) for e in ents for a in e.aliases if norm(a) == t]
    if exact:
        seen, out = set(), []
        for e, a in exact:
            if e.key not in seen:
                seen.add(e.key)
                out.append((e, a))
        return out
    if len(t) >= 2:
        tail = [(e, a) for e in ents if e.kind == "person" for a in e.aliases if norm(a).endswith(t) and len(norm(a)) > len(t)]
        keys = {e.key for e, _ in tail}
        if len(keys) == 1:
            return [tail[0]]
        if len(keys) > 1:
            seen, out = set(), []
            for e, a in tail:
                if e.key not in seen:
                    seen.add(e.key)
                    out.append((e, a))
            return out
    return []


# ---------------------------------------------------------------- sections
@dataclass
class Section:
    doc: Doc
    start: int          # 0-based 見出し行（文書先頭の見出し無し部分は 0）
    end: int
    trail: str          # 見出しパス
    date: date | None


def sections_of(doc: Doc) -> list[Section]:
    secs, stack, cur_start = [], [], 0
    heads = []
    for i, ln in enumerate(doc.lines):
        m = HEADING_RE.match(ln)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    if not heads or heads[0][0] != 0:
        heads.insert(0, (0, 0, ""))
    for idx, (i, lvl, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(doc.lines)
        while stack and stack[-1][0] >= lvl and lvl > 0:
            stack.pop()
        if lvl > 0:
            stack.append((lvl, title))
        trail = " › ".join(t for l, t in stack if l >= 2) or (stack[0][1] if stack else "")
        secs.append(Section(doc, i, end, trail, to_date(title) or doc.date))
    return secs


# ---------------------------------------------------------------- find
def term_variants(terms: list[str], ents: list[Entity]) -> tuple[list[list[str]], list[str]]:
    variants, notes = [], []
    for t in terms:
        hits = resolve(t, ents)
        if len(hits) == 1:
            e, _ = hits[0]
            vs = sorted({norm(a) for a in e.aliases} | {norm(t)}, key=len, reverse=True)
            notes.append(f"{t} → {e.label}（{e.key}）")
        else:
            vs = [norm(t)]
            if len(hits) > 1:
                notes.append(f"{t} は曖昧: " + " / ".join(f"{e.label}（{e.key}）" for e, _ in hits) + " ── 語として検索")
        variants.append(vs)
    return variants, notes


def find_hits(root: Path, terms: list[str], types: set[str] | None = None, project: str | None = None,
              since: date | None = None, archive: bool = False, limit: int = 12) -> dict:
    """検索の本体（CLI・ボード共通）。節単位でスコア順、上位 limit 件を返す。"""
    ents = load_entities(root)
    variants, notes = term_variants(terms, ents)
    today = date.today()
    results = []
    for doc in iter_docs(root, archive=archive):
        if types and doc.type not in types:
            continue
        if project and doc.project != project and f"p:{project.lower()}" not in norm(doc.text):
            continue
        for sec in sections_of(doc):
            if since and sec.date and sec.date < since:
                continue
            body = doc.lines[sec.start:sec.end]
            nbody = [norm(x) for x in body]
            if project and doc.project != project and not any(f"p:{project.lower()}" in x for x in nbody):
                continue
            per_term, matched_lines = [], set()
            for vs in variants:
                c = 0
                for k, x in enumerate(nbody):
                    if any(v in x for v in vs):
                        c += 1
                        matched_lines.add(k)
                per_term.append(c)
            if not all(per_term):
                continue
            # 全部の語が同じ行に揃う行（表なら同じ約束・同じ判断事例）
            together = [k for k, x in enumerate(nbody) if all(any(v in x for v in vs) for vs in variants)]
            # 当たった行がすべて表の行なのに同じ行に揃わない ＝ 別々の約束・事例に語が散っているだけ
            in_table_only = all(body[k].lstrip().startswith("|") for k in matched_lines)
            ntrail = norm(sec.trail)
            heading_hits = sum(1 for vs in variants if any(v in ntrail for v in vs))
            if len(variants) > 1 and in_table_only and not together and not heading_hits:
                continue
            score = sum(min(c, 5) for c in per_term)
            score += 3 * heading_hits
            if len(variants) > 1 and together:
                score += 5
                matched_lines = set(together)
            if sec.date:
                age = (today - sec.date).days
                score += 3 if age <= 30 else 2 if age <= 90 else 1 if age <= 365 else 0
            score += {"decision": 2, "commitment": 2, "person": 1, "status": 1}.get(doc.type, 0)
            if doc.archived:
                score -= 2
            snippet_idx = sorted(k for k in matched_lines if not HEADING_RE.match(body[k]))[:2] or sorted(matched_lines)[:1]
            results.append((score, sec, [(sec.start + k + 1, body[k].strip()) for k in snippet_idx]))
    results.sort(key=lambda r: (-r[0], -(r[1].date.toordinal() if r[1].date else 0)))
    return {"resolved": notes, "total": len(results),
            "hits": [{"path": s.doc.relpath, "line": s.start + 1, "type": s.doc.type, "project": s.doc.project,
                      "date": s.date.isoformat() if s.date else "", "heading": s.trail, "archived": s.doc.archived,
                      "score": sc, "snippets": [{"line": n, "text": t} for n, t in snip]}
                     for sc, s, snip in results[:limit]]}


def do_find(root: Path, a) -> int:
    r = find_hits(root, a.terms, set(a.type.split(",")) if a.type else None, a.project,
                  to_date(a.since) if a.since else None, a.archive, a.limit)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["total"] else 1
    for n in r["resolved"]:
        print(f"解決: {n}")
    if not r["total"]:
        print("該当なし。" + ("" if a.archive else " 過去分も探すなら --archive。") + " 見つからない場合は insights.md に「🔍検索ミス」を記録")
        return 1
    print(f"{r['total']} 節が該当（上位 {len(r['hits'])}）")
    for h in r["hits"]:
        tag = h["type"] + ("・過去分" if h["archived"] else "")
        print(f"- {h['path']}:{h['line']}  [{tag} {h['date']}]  {h['heading'] or '（冒頭）'}")
        for sn in h["snippets"]:
            print(f"    {sn['line']}: {clip(sn['text'], 110)}")
    if r["total"] > len(r["hits"]):
        print(f"ほか {r['total'] - len(r['hits'])} 節。--type / --project / --since で絞るか --limit を上げる")
    return 0




def clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------- who
def do_who(root: Path, a) -> int:
    ents = [e for e in load_entities(root) if e.kind == "person"]
    hits = resolve(a.name, ents)
    if a.json:
        print(json.dumps([{"ident": e.key, "name": e.label, "matched": al, "info": e.info, "path": e.path} for e, al in hits],
                         ensure_ascii=False, indent=1))
    elif not hits:
        print(f"「{a.name}」に当たる人物ファイルは無い → 新規人物（確認ケース）。既存の人なら呼び名を足す:"
              f" python tools/mem.py person add-alias {{識別子}} \"{a.name}\"")
    elif len(hits) == 1:
        e, al = hits[0]
        print(f"{e.key}  {e.label}（{e.info}）  ← 「{al}」で一致  {e.path}")
    else:
        print(f"「{a.name}」は {len(hits)} 人に当たる（曖昧＝確認ケース）:")
        for e, al in hits:
            print(f"- {e.key}  {e.label}（{e.info}）  ← 「{al}」")
    return 0 if len(hits) == 1 else (3 if not hits else 2)


# ---------------------------------------------------------------- map（目次）
def first_line(doc: Doc) -> str:
    for ln in doc.lines:
        s = ln.strip()
        if s and not s.startswith(("#", ">", "|", "---")):
            return clip(re.sub(r"^\s*-\s*", "", s), 70)
    return ""


def do_map(root: Path, a) -> int:
    docs = iter_docs(root, archive=a.archive)
    if a.type == "people":
        for e in [e for e in load_entities(root) if e.kind == "person"]:
            kana = re.compile(r"^[ぁ-ゖー\s　]+$")
            al = sorted((x for x in e.aliases if x != e.key and "@" not in x and not kana.match(x)), key=len, reverse=True)[:5]
            print(f"- {e.key}  {e.label}（{e.info}）  呼び名: {'、'.join(al)}")
        return 0
    if a.project:
        pd = [d for d in docs if d.project == a.project]
        if not pd:
            print(f"案件 {a.project} は無い")
            return 1
        for d in sorted(pd, key=lambda d: (TYPE_ORDER.index(d.type) if d.type in TYPE_ORDER else 99, d.relpath)):
            extra = ""
            if d.type == "decision":
                heads = [(m.group(1), m.group(2)) for m in re.finditer(r"^## (\d{4}-\d{2}-\d{2}): (.*)$", d.text, re.M)]
                states = re.findall(r"^- \*\*状態\*\*: (\S+)", d.text, re.M)
                extra = "  " + "／".join(f"{t[:18]}[{(states[i] if i < len(states) else '?')[:4]}]" for i, (_, t) in enumerate(heads[-4:]))
            print(f"- {d.relpath}  [{d.type} {d.date or ''} {len(d.lines)}行]  {first_line(d)}{extra}")
        return 0
    # 全体の目次：領域ごとの件数と最新日、案件ごとの内訳
    by_type: dict[str, list[Doc]] = {}
    for d in docs:
        by_type.setdefault(d.type, []).append(d)
    print("領域          件数  最新")
    for t in TYPE_ORDER:
        if t in by_type:
            latest = max((d.date for d in by_type[t] if d.date), default=None)
            print(f"{t:12s} {len(by_type[t]):5d}  {str(latest or '')}")
    projects = sorted({d.project for d in docs if d.project})
    if projects:
        print("\n案件          ファイル  最新        状態")
        ents = {e.key: e for e in load_entities(root) if e.kind == "project"}
        for p in projects:
            pd = [d for d in docs if d.project == p]
            latest = max((d.date for d in pd if d.date), default=None)
            print(f"{p:14s} {len(pd):6d}  {str(latest or ''):10}  {ents[p].info if p in ents else ''}")
    print("\n詳しく: map --project {slug} ／ map --type people ／ find {語}")
    return 0


# ---------------------------------------------------------------- show（節だけ読む）
def do_show(root: Path, a) -> int:
    p = (root / a.path) if not Path(a.path).is_absolute() else Path(a.path)
    if not p.exists():
        print(f"無い: {a.path}")
        return 1
    r = rel(root, p)
    doc = Doc(p, r, classify(r), project_of(r), "archive" in r, read(p), read(p).splitlines(), None)
    secs = sections_of(doc)
    if a.section:
        q = norm(a.section)
        hit = [s for s in secs if q in norm(s.trail) or (s.start < len(doc.lines) and q in norm(doc.lines[s.start]))]
        if not hit:
            print(f"見出しに「{a.section}」を含む節が無い。目次:")
            a.section = None
        else:
            for s in hit[: a.max_sections]:
                for i in range(s.start, min(s.end, s.start + a.max_lines)):
                    print(f"{i + 1:5d}  {doc.lines[i]}")
                if s.end - s.start > a.max_lines:
                    print(f"      …（{s.end - s.start - a.max_lines} 行省略。Read の offset={s.start + a.max_lines + 1} で続き）")
            if len(hit) > a.max_sections:
                print(f"ほか {len(hit) - a.max_sections} 節が一致")
            return 0
    if len(doc.lines) <= a.max_lines:
        for i, ln in enumerate(doc.lines):
            print(f"{i + 1:5d}  {ln}")
        return 0
    print(f"{r}（{len(doc.lines)} 行）の目次。--section で節だけ読む")
    for s in secs:
        if s.start < len(doc.lines) and HEADING_RE.match(doc.lines[s.start]):
            print(f"{s.start + 1:5d}  {doc.lines[s.start]}  （{s.end - s.start}行）")
    return 0


# ---------------------------------------------------------------- review（中身の点検）
@dataclass
class Finding:
    area: str
    target: str
    detail: str
    action: str
    auto: bool          # True=秘書が直してよい（事後報告）、False=ユーザーに1問


def md_rows(md: str, heading: str) -> list[dict]:
    lines = md.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#") and heading in ln:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("|"):
                if lines[j].startswith("#"):
                    return []
                j += 1
            if j + 1 >= len(lines):
                return []
            hdr = [c.strip() for c in lines[j].strip().strip("|").split("|")]
            rows = []
            for ln2 in lines[j + 2:]:
                if not ln2.startswith("|"):
                    break
                cells = [c.strip() for c in ln2.strip().strip("|").split("|")]
                if cells and "（未" not in cells[0]:
                    rows.append(dict(zip(hdr, cells)))
            return rows
    return []


def commitments(root: Path) -> list[dict]:
    md = read(root / "memory" / "commitments.md")
    out = []
    for kind, head in (("task", "自分のタスク"), ("received", "受けた指示"), ("given", "出した指示")):
        for r in md_rows(md, head):
            r["kind"] = kind
            out.append(r)
    return out


TOKEN_RE = re.compile(r"[一-龥ァ-ヶーA-Za-z0-9]{2,}")


def tokens(s: str) -> set[str]:
    return {t for t in TOKEN_RE.findall(norm(re.sub(r"（.*?）|\(.*?\)", " ", s or ""))) if not re.fullmatch(r"\d+", t)}


def match_score(row_toks: set[str], line: str, line_toks: set[str]) -> float:
    """約束の語がその行にどれだけ出るか。一般語は 0.5、固有の語は 1。部分一致（比較表 ⊂ ベンダー比較表）も数える。"""
    nl = norm(line)
    sc = 0.0
    for t in row_toks:
        hit = t in nl or (len(t) >= 3 and any(len(lt) >= 3 and (lt in t or t in lt) for lt in line_toks))
        if hit:
            sc += 0.5 if t in GENERIC or len(t) <= 2 else 1.0
    return sc


def md_dates(line: str, ref: date) -> list[date]:
    """行の日付。「旧 11/14」「元の 11/14」「11/14 から変更」のような変更前の日付は除く。"""
    out = []
    pats = [(m.start(), m.end(), to_date(m.group(0))) for m in DATE_RE.finditer(line)]
    for m in re.finditer(r"(?<![\d-])(\d{1,2})/(\d{1,2})(?![\d/])", line):
        try:
            pats.append((m.start(), m.end(), date(ref.year, int(m.group(1)), int(m.group(2)))))
        except ValueError:
            pass
    for s, e, d in pats:
        before, after = line[max(0, s - 4):s], line[e:e + 6]
        if d and not re.search(r"旧|元の?|変更前", before) and not re.match(r"\s*(から|より)変更", after):
            out.append(d)
    return out


def review_findings(root: Path) -> list[Finding]:
    """中身の点検の本体（CLI・ボード共通）。"""
    today = date.today()
    F: list[Finding] = []
    docs = iter_docs(root)
    mem = root / "memory"
    ents = load_entities(root)
    people = [e for e in ents if e.kind == "person"]
    projects = {e.key: e for e in ents if e.kind == "project"}
    dash = read(mem / "dashboard.md")

    # 1. 量
    for d in docs:
        lim = 100 if d.relpath == "memory/dashboard.md" else 200
        if len(d.lines) > lim and d.type != "deliverable":
            F.append(Finding("量", d.relpath, f"{len(d.lines)} 行（目安 {lim}）", "古い部分を archive へ退避（memory-maintenance）", True))

    # 2. 古さ
    for key, e in projects.items():
        state = field(read(root / e.path), "状態")
        if re.search(r"完了|凍結|中止", state):
            if key in dash:
                F.append(Finding("古さ", e.path, f"状態「{state}」なのに dashboard の進行中に残っている", "dashboard から外し _archive.md へ1行", True))
            continue
        st = next((d for d in docs if d.relpath == f"memory/projects/{key}/status.md"), None)
        if st and st.date and (today - st.date).days > 30:
            F.append(Finding("古さ", st.relpath, f"最終更新から {(today - st.date).days} 日", f"「{e.label}」は今も進行中か、状況を更新するか（完了なら archive）", False))
    sit = next((d for d in docs if d.relpath == "memory/situation.md"), None)
    if sit and sit.date and (today - sit.date).days > 14:
        F.append(Finding("古さ", sit.relpath, f"最終更新から {(today - sit.date).days} 日", "台帳・各案件の状況から situation を更新する", True))
    for e in people:
        md = read(root / e.path)
        ds = [d for d in (to_date(x) for x in re.findall(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|", md)) if d]
        last = max(ds) if ds else None
        if last and (today - last).days > 120 and (e.label in dash or e.key in dash):
            F.append(Finding("古さ", e.path, f"最後の記録から {(today - last).days} 日なのに dashboard の重要な人物に残っている",
                             f"{e.label} さんとは今も関わるか（外すなら dashboard から削除、ファイルは残す）", False))
        obs = md_rows(md, "判断事例ログ")
        lv = re.search(r"\*\*モデル成熟度\*\*:\s*Lv(\d)", md)
        if len(obs) >= 5 and lv and int(lv.group(1)) < 2:
            F.append(Finding("育成", e.path, f"判断事例 {len(obs)} 件で Lv{lv.group(1)}", "成熟度を Lv2 に上げる（/simulate が実用になる）", True))
    for d in docs:
        if d.type == "context" and d.date and (today - d.date).days > 180:
            F.append(Finding("古さ", d.relpath, f"{(today - d.date).days} 日更新なし", "内容が今も正しいか確認（古ければ archive）", False))

    # 3. 食い違い（台帳の期限 と situation / status の記述）
    rows = commitments(root)
    open_rows = [r for r in rows if r.get("状態") not in ("done", "dropped")]
    texts = [d for d in docs if d.relpath == "memory/situation.md" or d.type == "status"]
    dated = [r for r in open_rows if to_date(r.get("期限", ""))]
    row_toks = {id(r): tokens((r.get("タスク") or r.get("内容") or "") + " " + (r.get("依頼者") or r.get("相手") or "")) for r in dated}
    for d in texts:
        for i, ln in enumerate(d.lines):
            if not (DATE_RE.search(ln) or re.search(r"\d{1,2}/\d{1,2}", ln)):
                continue
            ids = set(re.findall(r"C-\d{3,}", ln))
            if ids:   # ID が書いてある行は、その約束とだけ比べる
                cands = [r for r in dated if r.get("ID") in ids]
            else:     # 書いていなければ、いちばん語が重なる約束を1つだけ選ぶ（同点なら判定しない）
                lt = tokens(ln)
                scored = sorted(((match_score(row_toks[id(r)], ln, lt), r) for r in dated), key=lambda x: -x[0])
                if not scored or scored[0][0] < 1.5 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
                    continue
                cands = [scored[0][1]]
            for r in cands:
                due = to_date(r.get("期限", ""))
                ds = md_dates(ln, due)
                if ds and due not in ds and all(abs((x - due).days) <= 60 for x in ds):
                    content = r.get("タスク") or r.get("内容") or ""
                    F.append(Finding("食い違い", f"{d.relpath}:{i + 1}",
                                     f"{r.get('ID')}「{clip(content, 30)}」の期限は台帳で {due}、ここでは {'・'.join(x.isoformat() for x in ds)}",
                                     "台帳（期限の正典）に合わせて記述を直す。台帳が古いなら set-due", True))

    # 4. 重複
    by_mail: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}
    for e in people:
        md = read(root / e.path)
        mail = field(md, "メールアドレス")
        if mail and "@" in mail:
            by_mail.setdefault(norm(mail), []).append(e.path)
        by_name.setdefault(norm(e.label.replace(" ", "").replace("　", "")), []).append(e.path)
    reported = set()
    for k, v in list(by_mail.items()) + list(by_name.items()):
        if len(v) > 1 and tuple(sorted(v)) not in reported:
            reported.add(tuple(sorted(v)))
            F.append(Finding("重複", " / ".join(v), f"同じ{'メール' if '@' in k else '氏名'}「{k}」の人物ファイルが {len(v)} つ",
                             "同一人物ならまとめる（別人なら識別子と所属を見直す）", False))
    ids = [r.get("ID", "") for r in rows if re.fullmatch(r"C-\d{3,}", r.get("ID", ""))]
    for cid in sorted({i for i in ids if ids.count(i) > 1}):
        F.append(Finding("重複", "memory/commitments.md", f"台帳に ID {cid} が {ids.count(cid)} 行ある（状態更新がどちらに効くか曖昧）",
                         "後から足した行に新しい ID を振り直す（mem.py commitment add で作り直し、古い行は dropped）", True))
    for d in docs:
        if d.type == "decision":
            titles = [norm(m.group(1)) for m in re.finditer(r"^## \d{4}-\d{2}-\d{2}: (.*)$", d.text, re.M)]
            for t in {t for t in titles if titles.count(t) > 1}:
                F.append(Finding("重複", d.relpath, f"同じ題の決定が複数「{clip(t, 30)}」", "1つにまとめる（経緯は本文へ）", True))

    # 5. 切れた参照・解決できない名前
    for r in rows:
        for slug in re.findall(r"P:([A-Za-z0-9_-]+)", r.get("関連", "")):
            if slug not in projects:
                F.append(Finding("参照", "memory/commitments.md", f"{r.get('ID', '?')} の関連 P:{slug} という案件が無い", "正しい案件に直す（または案件を登録）", True))
        who = re.sub(r"（.*?）", "", r.get("依頼者") or r.get("相手") or "").strip()
        if who and who not in ("-", "自分", "—") and not resolve(who, people):
            F.append(Finding("参照", "memory/commitments.md", f"{r.get('ID', '?')} の相手「{who}」が人物ファイルに解決できない",
                             f"既存の人なら呼び名を足す（mem.py person add-alias）。新しい人なら登録するか確認", False))
    seen_ref = set()
    for d in docs:
        body = "\n".join(ln for ln in d.lines if not ln.lstrip().startswith(">"))  # 引用行は説明文なので見ない
        for m in re.finditer(r"`(memory/[^`{}\s]+?\.md)`", body):
            ref = m.group(1)
            if ref not in seen_ref and not (root / ref).exists() and "YYYY" not in ref and "{" not in ref:
                seen_ref.add(ref)
                F.append(Finding("参照", d.relpath, f"存在しないファイル `{ref}` を参照", "パスを直すか参照を外す", True))

    # 6. 取り出し漏れ（メモ・会議に書かれたまま）
    # 生メモ・会議メモはそれ自体が正規の置き場。問題は「約束・決定が書かれているのに台帳・decisions に紐づいていない」ものだけ
    action_re = re.compile(r"アクション|TODO|ToDo|宿題|決定事項|決まったこと|までに|期限")
    linked_re = re.compile(r"C-\d{3}|decisions\.md|→ 記録先|📁")
    for d in docs:
        if d.type in ("meeting", "memo") and d.date and (today - d.date).days > 1 \
                and action_re.search(d.text) and not linked_re.search(d.text):
            kind = "会議メモ" if d.type == "meeting" else "生メモ"
            F.append(Finding("取り出し漏れ", d.relpath, f"{kind}に約束・決定らしき記載があるが、台帳や decisions への紐づけが無い",
                             "約束は台帳へ、決定は decisions へ取り出し、元のメモに「→ 記録先: …」を書く（取り出すものが無ければその旨を1行）", True))
    inbox = read(mem / "inbox.md")
    for m in re.finditer(r"^- \[ \] (I-\d+) (\d{4}-\d{2}-\d{2})", inbox, re.M):
        dd = to_date(m.group(2))
        if dd and (today - dd).days > 2:
            F.append(Finding("取り出し漏れ", "memory/inbox.md", f"{m.group(1)} が {(today - dd).days} 日未分類", "秘書が仕分けて配置する（確認ケースなら1問で聞く）", True))

    # 7. ライフサイクル
    for d in docs:
        if d.type == "decision":
            for m in re.finditer(r"^## (\d{4}-\d{2}-\d{2}): (.*)\n- \*\*状態\*\*: 提案", d.text, re.M):
                dd = to_date(m.group(1))
                if dd and (today - dd).days > 30:
                    F.append(Finding("期限切れ", d.relpath, f"提案「{clip(m.group(2), 30)}」が {(today - dd).days} 日未承認", "承認するか取り下げるか", False))
    closed = [r for r in rows if r.get("状態") in ("done", "dropped")]
    if len(closed) > 30:
        F.append(Finding("量", "memory/commitments.md", f"完了・取り下げが {len(closed)} 行", "月次アーカイブへ（memory-maintenance）", True))
    for r in open_rows:
        due = to_date(r.get("期限", ""))
        if due and (today - due).days > 14:
            F.append(Finding("期限切れ", "memory/commitments.md", f"{r.get('ID')} 「{clip(r.get('タスク') or r.get('内容') or '', 30)}」が期限から {(today - due).days} 日",
                             "済んでいれば done、不要なら dropped、続くなら期限を引き直す", False))
    for d in docs:
        if d.type == "deliverable" and d.date and (today - d.date).days > 90:
            F.append(Finding("量", d.relpath, f"{(today - d.date).days} 日前の成果物", "提出済みなら deliverables/archive/ へ", True))

    return F


def do_review(root: Path, a) -> int:
    today = date.today()
    F = review_findings(root)
    auto = [f for f in F if f.auto]
    ask = [f for f in F if not f.auto]
    if a.area:
        F = [f for f in F if f.area == a.area]
        auto = [f for f in F if f.auto]
        ask = [f for f in F if not f.auto]
    if a.json:
        print(json.dumps([f.__dict__ for f in F], ensure_ascii=False, indent=1))
        return 0
    # 段階的開示：件数の表 → 区分ごとに上位 per 件 → 残りは --area {区分} で全件
    per = 10 ** 6 if (a.all or a.area) else 5
    areas = sorted({f.area for f in F}, key=lambda x: ["食い違い", "参照", "重複", "取り出し漏れ", "期限切れ", "古さ", "育成", "量"].index(x)
                   if x in ["食い違い", "参照", "重複", "取り出し漏れ", "期限切れ", "古さ", "育成", "量"] else 99)
    out = [f"# 記憶の点検 {today.isoformat()}", "",
           f"要対応 {len(F)} 件（秘書が直してよい {len(auto)}・ユーザーに確認 {len(ask)}）。直し方は memory-review スキル。", "",
           "| 区分 | 秘書が直す | 確認 |", "|---|---|---|"]
    out += [f"| {ar} | {sum(1 for f in auto if f.area == ar)} | {sum(1 for f in ask if f.area == ar)} |" for ar in areas]
    out.append("")
    for title, group in (("秘書が直してよい（直したら事後報告）", auto), ("ユーザーに確認（まとめて1問）", ask)):
        out.append(f"## {title}")
        if not group:
            out.append("- なし")
        for ar in areas:
            g = [f for f in group if f.area == ar]
            for f in g[:per]:
                out.append(f"- [{f.area}] `{f.target}` ── {f.detail} → {f.action}")
            if len(g) > per:
                out.append(f"- [{ar}] ほか {len(g) - per} 件（`python tools/recall.py review --area {ar}` で全件）")
        out.append("")
    text = "\n".join(out)
    if a.out:
        op = root / a.out
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(text + "\n", encoding="utf-8")
        print(f"点検結果を {a.out} に保存（要対応 {len(F)} 件）")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------- main
def main(argv=None) -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="recall.py", description="memory/ を探す・読む・点検する（読み取り専用）")
    ap.add_argument("--root", type=Path, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("find", help="語で探す（人物・案件の別名を展開、節単位、上限付き）")
    s.add_argument("terms", nargs="+")
    s.add_argument("--type", help="person,project,status,decision,commitment,issue,meeting,context,incident,insight,memo,inbox,situation,deliverable")
    s.add_argument("--project"); s.add_argument("--since"); s.add_argument("--archive", action="store_true")
    s.add_argument("--limit", type=int, default=12); s.add_argument("--json", action="store_true")
    s = sub.add_parser("who", help="名前・呼び名・メールから人物ファイルを引く")
    s.add_argument("name"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("map", help="目次（全体／案件／人物）")
    s.add_argument("--project"); s.add_argument("--type", choices=["people"]); s.add_argument("--archive", action="store_true")
    s = sub.add_parser("show", help="ファイルの目次、または1節だけ")
    s.add_argument("path"); s.add_argument("--section")
    s.add_argument("--max-lines", type=int, default=60); s.add_argument("--max-sections", type=int, default=3)
    s = sub.add_parser("review", help="中身の点検（古さ・食い違い・重複・切れた参照・取り出し漏れ・期限切れ）")
    s.add_argument("--out"); s.add_argument("--json", action="store_true")
    s.add_argument("--area", help="区分で絞って全件（食い違い・参照・重複・取り出し漏れ・期限切れ・古さ・育成・量）")
    s.add_argument("--all", action="store_true", help="区分ごとの上限（5件）を外す")
    a = ap.parse_args(argv)
    root = (a.root or Path(__file__).resolve().parent.parent).resolve()
    if not (root / "memory").is_dir():
        sys.exit(f"{root} に memory/ が無い（--root を確認）")
    code = {"find": do_find, "who": do_who, "map": do_map, "show": do_show, "review": do_review}[a.cmd](root, a)
    sys.exit(code)


if __name__ == "__main__":
    main()
