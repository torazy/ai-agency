"""メンテモード（tools/dev/）：本体の設定との分離・写し直し・状況台帳・git フックでの止め。設計は tools/dev/DESIGN.md。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def is_instance() -> bool:
    """/setup 済みのインスタンス（本体の設定を利用者が育てている）では、本体との照合をしない。"""
    p = REPO / "memory" / "dashboard.md"
    return p.exists() and "**名前**: （未設定）" not in p.read_text(encoding="utf-8")


class IsolationTest(unittest.TestCase):
    @unittest.skipIf(is_instance(), "インスタンスでは本体の settings.json を利用者が育てるので照合しない")
    def test_product_settings_are_classified_for_maint_mode(self):
        # 本体の .claude/settings.json に足したものがメンテモードに黙って効かないよう、扱いを isolation.json で決めておく
        m = load(REPO / "tools" / "dev" / "maint.py", "maint_iso")
        self.assertEqual(m.settings_problems(REPO), [])

    def test_unclassified_key_and_new_deny_are_reported(self):
        m = load(REPO / "tools" / "dev" / "maint.py", "maint_iso2")
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / ".claude").mkdir()
            (root / "tools" / "dev").mkdir(parents=True)
            shutil.copy(REPO / "tools" / "dev" / "isolation.json", root / "tools" / "dev" / "isolation.json")
            st = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
            st["env"] = {"X": "1"}
            st["permissions"]["deny"] = st["permissions"]["deny"] + ["Bash(git push*)"]
            (root / ".claude" / "settings.json").write_text(json.dumps(st), encoding="utf-8")
            probs = "\n".join(m.settings_problems(root))
            self.assertIn("「env」", probs)
            self.assertIn("Bash(git push*)", probs)


class MaintRepo(unittest.TestCase):
    """本体のメンテ関係のファイルを一時フォルダに写して試す（本物の本体フォルダは触らない）。"""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        for d in (".claude/skills", ".claude/commands", "tools/dev"):
            shutil.copytree(REPO / d, self.root / d, ignore=shutil.ignore_patterns("__pycache__", "maint-state.md",
                                                                                 "maint-archive.md", "maint-*" if d == ".claude/skills" else "__none__"))
        shutil.copy(REPO / ".claude" / "settings.json", self.root / ".claude" / "settings.json")
        shutil.copy(REPO / "tools" / "py.sh", self.root / "tools" / "py.sh")
        if (REPO / ".mcp.json").exists():
            shutil.copy(REPO / ".mcp.json", self.root / ".mcp.json")
        self.git = shutil.which("git") is not None
        if self.git:
            subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.en = load(self.root / "tools" / "dev" / "enable-maintenance.py", "en_t")
        self.m = load(self.root / "tools" / "dev" / "maint.py", "maint_t")

    def tearDown(self):
        self._t.cleanup()

    def maint(self, *args, env=None):
        import os
        e = dict(os.environ, PYTHONUTF8="1")
        e.pop("MAINT_ALLOW_UNTRACKED", None)
        e.update(env or {})
        return subprocess.run([sys.executable, str(self.root / "tools/dev/maint.py"), "--root", str(self.root), *args],
                              cwd=self.root, capture_output=True, text=True, encoding="utf-8", env=e)


class SyncTest(MaintRepo):
    def test_sync_hides_every_product_skill_and_is_idempotent(self):
        changes = self.en.sync(self.root)
        local = json.loads((self.root / ".claude/settings.local.json").read_text(encoding="utf-8"))
        self.assertTrue(local["disableAllHooks"])
        for n in self.en.product_skill_names(self.root):
            self.assertEqual(local["skillOverrides"].get(n), "off", n)
        self.assertIn("dispatch", local["skillOverrides"])                       # コマンドも止まる
        self.assertTrue((self.root / "CLAUDE.local.md").exists())
        self.assertIn("@tools/dev/memory/maint-state.md", (self.root / "CLAUDE.local.md").read_text(encoding="utf-8"))
        self.assertTrue((self.root / ".claude/skills/maint-check-structure/SKILL.md").exists())
        self.assertTrue((self.root / ".ai-agency-template").exists())
        if self.git:
            self.assertTrue((self.root / ".git/hooks/pre-commit").exists())
            self.assertTrue(any("pre-commit" in c for c in changes))
        self.assertEqual(self.en.sync(self.root), [])                             # 冪等
        self.assertEqual(self.m.overlay_problems(self.root), [])
        # 本体に後からスキルを足すと漏れとして見つかり、写し直しで止まる
        (self.root / ".claude/skills/new-skill").mkdir()
        (self.root / ".claude/skills/new-skill/SKILL.md").write_text("---\nname: new-skill\n---\n", encoding="utf-8")
        self.assertIn("new-skill", "\n".join(self.m.overlay_problems(self.root)))
        self.assertIn("new-skill", "\n".join(self.en.sync(self.root)))
        self.assertEqual(self.m.overlay_problems(self.root), [])

    def test_does_nothing_outside_maint_mode(self):
        r = self.maint("add", "x")
        self.assertIn("メンテモードではない", r.stdout)
        self.assertFalse((self.root / "tools/dev/memory/maint-state.md").exists())


class LedgerTest(MaintRepo):
    def setUp(self):
        super().setUp()
        (self.root / ".ai-agency-template").write_text("", encoding="utf-8")

    def state(self) -> str:
        return (self.root / "tools/dev/memory/maint-state.md").read_text(encoding="utf-8")

    def test_request_lifecycle_and_handoff(self):
        r = self.maint("add", "ボードに a|b を出す", "--state", "着手", "--next", "API から")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("R-001", r.stdout)
        self.assertIn("a／b", self.state())                                         # 表を壊さない
        self.assertEqual(self.maint("note", "R-001", "API できた", "--next", "画面").returncode, 0)
        self.maint("handoff", "画面の途中。index.html の 480 行目から")
        self.assertIn("480 行目", self.state())
        self.assertNotEqual(self.maint("set", "R-001", "完了").returncode, 0)        # 完了は done で
        self.assertNotEqual(self.maint("done", "R-001", "--result", "").returncode, 0)
        r = self.maint("done", "R-001", "--result", "abc1234", "--unverified", "Mac で", "--unverified", "スマホ幅")
        self.assertIn("V-001, V-002", r.stdout)
        self.maint("verify", "--ok", "V-001", "--note", "OK")
        self.assertNotIn("Mac で", self.state())
        self.assertIn("Mac で", (self.root / "tools/dev/memory/maint-archive.md").read_text(encoding="utf-8"))
        self.maint("handoff", "--clear")
        self.assertIn("## 引き継ぎ\n\n（なし）", self.state())
        rows = self.m.rows(self.m.load(self.m.state_path(self.root), ""), "要求")
        self.assertEqual((rows[0]["状態"], rows[0]["結果"]), ("完了", "abc1234"))

    def test_running_things_and_compaction(self):
        self.maint("run", "add", "見直し", "--kind", "path", "--target", str(self.root), "--stop", "消す")
        self.assertEqual(self.m.run_alive(self.root, self.m.rows(self.m.load(self.m.state_path(self.root), ""), "稼働中のもの")[0]), "あり")
        self.assertNotEqual(self.maint("run", "add", "見直し").returncode, 0)                   # 二重登録しない
        self.maint("run", "rm", "見直し")
        self.assertIn("| （なし） |", self.state().split("## 稼働中のもの")[1])
        for i in range(9):
            rid = self.m.add(self.root, f"要求{i}")
            self.m.done(self.root, rid, "ok")
        reqs = self.m.rows(self.m.load(self.m.state_path(self.root), ""), "要求")
        self.assertEqual(len(reqs), self.m.KEEP_CLOSED)                                      # 古い完了は記録庫へ
        self.assertIn("要求0", (self.root / "tools/dev/memory/maint-archive.md").read_text(encoding="utf-8"))
        self.assertEqual(self.m.add(self.root, "次"), "R-010")                               # 記録庫の ID と重ならない

    @unittest.skipUnless(shutil.which("git"), "git が要る")
    def test_pre_commit_requires_active_request_and_classified_settings(self):
        (self.root / "x.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "x.txt"], cwd=self.root, check=True)
        r = self.maint("git-hook", "pre-commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("着手中のメンテ要求が無い", r.stderr)
        self.assertEqual(self.maint("git-hook", "pre-commit", env={"MAINT_ALLOW_UNTRACKED": "1"}).returncode, 0)
        self.m.add(self.root, "済んだ要求")
        subprocess.run(["git", "add", "tools/dev/memory/maint-state.md"], cwd=self.root, check=True)
        self.assertEqual(self.maint("git-hook", "pre-commit").returncode, 0)          # 台帳の更新を含むコミットは通す
        subprocess.run(["git", "reset", "-q", "tools/dev/memory/maint-state.md"], cwd=self.root, check=True)
        self.maint("add", "x を足す", "--state", "着手")
        self.assertEqual(self.maint("git-hook", "pre-commit").returncode, 0)
        st = json.loads((self.root / ".claude/settings.json").read_text(encoding="utf-8"))
        st["permissions"]["deny"].append("Bash(git push*)")
        (self.root / ".claude/settings.json").write_text(json.dumps(st), encoding="utf-8")
        subprocess.run(["git", "add", ".claude/settings.json"], cwd=self.root, check=True)
        r = self.maint("git-hook", "pre-commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("メンテモードに効く", r.stderr)


if __name__ == "__main__":
    unittest.main()
