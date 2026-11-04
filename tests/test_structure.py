"""配布物の構造（maint-check-structure の機械化）。リポジトリ本体を読むが、memory/ の中身には依存しない。"""

import json
import py_compile
import re
import unittest
from pathlib import Path

from helpers import FIXTURE_MEMORY, REPO


def frontmatter(p: Path) -> str:
    s = p.read_text(encoding="utf-8")
    return s.split("---", 2)[1] if s.startswith("---") else ""


class StructureTest(unittest.TestCase):
    def test_skills_frontmatter(self):
        for f in sorted((REPO / ".claude" / "skills").glob("*/SKILL.md")):
            if f.parent.name.startswith("maint-"):
                continue
            fm = frontmatter(f)
            m = re.search(r"^name:\s*(\S+)", fm, re.M)
            self.assertTrue(m and m.group(1) == f.parent.name, f"{f}: name がフォルダ名と違う")
            self.assertIn("description:", fm, f)

    def test_agents_and_their_skills(self):
        for f in sorted((REPO / ".claude" / "agents").glob("*.md")):
            fm = frontmatter(f)
            for key in ("name:", "description:", "icon:"):
                self.assertIn(key, fm, f"{f}: {key} が無い")
            for sk in re.findall(r"^\s+-\s+(\S+)", fm, re.M):
                self.assertTrue((REPO / ".claude" / "skills" / sk / "SKILL.md").exists(), f"{f}: skill {sk} が無い")

    def test_commands_listed(self):
        claude, readme = (REPO / "CLAUDE.md").read_text(encoding="utf-8"), (REPO / "README.md").read_text(encoding="utf-8")
        for f in sorted((REPO / ".claude" / "commands").glob("*.md")):
            s = f.read_text(encoding="utf-8")
            self.assertIn("description:", frontmatter(f), f)
            self.assertIn("$ARGUMENTS", s, f)
            self.assertIn(f"/{f.stem}", claude, f"CLAUDE.md に /{f.stem} が無い")
            self.assertIn(f"/{f.stem}", readme, f"README.md に /{f.stem} が無い")

    def test_claude_md_stays_small(self):
        n = len((REPO / "CLAUDE.md").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(n, 220, f"CLAUDE.md が {n} 行（毎ターン読み込まれる L0。手順は skills へ）")

    def test_python_compiles_and_json_parses(self):
        for f in list((REPO / "tools").rglob("*.py")) + list((REPO / "tests").glob("*.py")):
            if "__pycache__" not in f.parts:
                py_compile.compile(str(f), doraise=True)
        for f in (REPO / "tools/hooks/operation-log.json", REPO / ".claude/settings.json", FIXTURE_MEMORY / "guardrails.json"):
            json.loads(f.read_text(encoding="utf-8"))

    def test_no_non_ascii_in_strftime(self):
        """Windows の Python 3.11 は strftime の書式に非 ASCII があると英語ロケールで落ちる（CI で検出した不具合の再発防止）。"""
        for f in (REPO / "tools").rglob("*.py"):
            for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                m = re.search(r"strftime\(([^)]*)\)", ln)
                if m:
                    self.assertTrue(all(ord(c) < 128 for c in m.group(1)), f"{f}:{i} strftime の書式に非 ASCII")

    def test_hooks_and_board_start_through_py_sh(self):
        """Mac は python3 だけ、Windows は python だけのことが多い。フックとボードは tools/py.sh 経由で起動する。"""
        st = json.loads((REPO / ".claude/settings.json").read_text(encoding="utf-8"))
        for arr in st.get("hooks", {}).values():
            for h in arr:
                for hh in h.get("hooks", []):
                    self.assertTrue(hh.get("command", "").startswith("sh tools/py.sh "), hh.get("command"))
        lj = json.loads((REPO / ".claude/launch.json").read_text(encoding="utf-8"))
        for c in lj.get("configurations", []):
            self.assertNotEqual(c.get("runtimeExecutable"), "python", c)

    def test_hooks_point_to_existing_scripts(self):
        st = json.loads((REPO / ".claude/settings.json").read_text(encoding="utf-8"))
        for arr in st.get("hooks", {}).values():
            for h in arr:
                for hh in h.get("hooks", []):
                    m = re.search(r"tools/hooks/[\w-]+\.py", hh.get("command", ""))
                    if m:
                        self.assertTrue((REPO / m.group(0)).exists(), m.group(0))

    def test_schedule_scripts_portable(self):
        ps1 = (REPO / "tools/schedule/morning-brief.ps1").read_bytes()
        self.assertTrue(all(b < 128 for b in ps1), "ps1 は ASCII のみ（PowerShell 5.1 が BOM 無し UTF-8 を誤読する）")
        attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh   text eol=lf", attrs)

    def test_no_maintenance_files_in_product(self):
        """保守の物（台帳・手順・メンテモードの層）は別リポジトリ ai-agency-maintenance に置き、本体には持ち込まない。"""
        self.assertFalse((REPO / "tools" / "dev").exists(), "tools/dev は廃止（保守は ai-agency-maintenance から）")
        self.assertEqual([p.name for p in (REPO / ".claude" / "skills").glob("maint-*")], [])

    def test_fixture_matches_template(self):
        """本体（雛形のまま）でだけ確かめる：tests/fixtures/memory が memory/ の雛形と一致しているか。"""
        dash = REPO / "memory" / "dashboard.md"
        pristine = dash.exists() and "**名前**: （未設定）" in dash.read_text(encoding="utf-8") \
            and not [p for p in (REPO / "memory" / "people").glob("*.md") if p.name != "_template.md"]
        if not pristine:
            self.skipTest("インスタンス（記憶あり）では比べない")
        for f in FIXTURE_MEMORY.rglob("*"):
            if f.is_file():
                t = REPO / "memory" / f.relative_to(FIXTURE_MEMORY)
                self.assertTrue(t.exists(), f"雛形に {t} が無い")
                self.assertEqual(f.read_text(encoding="utf-8"), t.read_text(encoding="utf-8"),
                                 f"tests/fixtures/memory/{f.relative_to(FIXTURE_MEMORY)} が雛形とずれている（コピーし直す）")
        for t in (REPO / "memory").rglob("*"):
            if t.is_file() and "task-log" not in t.parts and "briefs" not in t.parts and t.name != "sync-state.json":
                self.assertTrue((FIXTURE_MEMORY / t.relative_to(REPO / "memory")).exists(), f"fixture に {t.name} が無い")


if __name__ == "__main__":
    unittest.main()
