"""tools/hooks/（ガードレール・操作ログ・起動時の注入）の振る舞い。"""

import json
import unittest

from helpers import TempRepo, d


def ev(tool, command=None, file_path=None, mode="default", hook="PostToolUse"):
    inp = {"command": command} if command is not None else {"file_path": file_path}
    return json.dumps({"tool_name": tool, "tool_input": inp, "hook_event_name": hook,
                       "permission_mode": mode, "session_id": "abcdef1234"})


class GuardrailTest(TempRepo):
    def guard(self, command):
        return self.run_py("tools/hooks/guardrail.py", stdin=ev("Bash", command, hook="PreToolUse"))

    def test_blocks_destructive(self):
        self.assertEqual(self.guard("rm -rf / --no-preserve-root").returncode, 2)
        self.assertEqual(self.guard("git push --force origin main").returncode, 2)

    def test_allows_normal(self):
        self.assertEqual(self.guard("python tools/mem.py inbox list").returncode, 0)
        self.assertEqual(self.guard("git status").returncode, 0)


class OperationLoggerTest(TempRepo):
    def log_lines(self):
        logs = sorted((self.root / "memory" / "task-log").glob("*.md"))
        return [ln for f in logs for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("**操作**")]

    def logger(self, event, env=None):
        r = self.run_py("tools/hooks/operation-logger.py", stdin=event, env=env)
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout!r}\nstderr={r.stderr}")
        self.assertEqual(json.loads(r.stdout), {"suppressOutput": True})

    def test_categories_and_status(self):
        self.logger(ev("Bash", "python tools/mem.py inbox add x && python tools/mem.py commitment add --kind task --content y"))
        self.logger(ev("Bash", "git commit -q -F - <<'EOF'\nmsg mentions git push\nEOF"))
        self.logger(ev("Bash", "python tools/mem.py person add-alias a b", mode="auto"))
        self.logger(ev("Bash", "python tools/mem.py inbox add z"), env={"AI_AGENCY_UNATTENDED": "1"})
        lines = self.log_lines()
        cats = [ln.split("[", 1)[1].split("]", 1)[0] for ln in lines]
        self.assertEqual(cats, ["mem:inbox", "mem:commitment", "git-commit", "mem:person", "mem:inbox"])
        self.assertIn("自動許可", lines[0])          # 確認画面の印が無い通常モード＝許可ルールで通ったもの
        self.assertIn("自動実行[auto]", lines[3])
        self.assertIn("自動実行[unattended]", lines[4])

    def test_skips_reads_help_and_generated(self):
        self.logger(ev("Bash", "python tools/recall.py find 比較表"))
        self.logger(ev("Bash", "python tools/mem.py inbox add --help"))
        self.logger(ev("Write", file_path=str(self.root / "memory" / "briefs" / f"{d(0)}.md")))
        self.logger(ev("Write", file_path=str(self.root / "README.md")))
        self.assertEqual(self.log_lines(), [])

    def test_template_records_nothing(self):
        # 本体（テンプレート）を直接開いても、記録系のフックは雛形の memory/ に書かない
        (self.root / ".ai-agency-template").write_text("", encoding="utf-8")
        for hook, e in (("operation-logger", ev("Edit", file_path=str(self.root / "memory" / "situation.md"))),
                        ("agent-activity", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Agent",
                                                       "tool_input": {"subagent_type": "qa", "description": "x"}})),
                        ("permission-marker", ev("Bash", "python tools/mem.py inbox add x"))):
            self.assertEqual(self.run_py(f"tools/hooks/{hook}.py", stdin=e).returncode, 0, hook)
        self.assertEqual(self.log_lines(), [])
        self.assertFalse((self.root / "memory/task-log/agent-runs.jsonl").exists())

    def test_records_memory_writes(self):
        self.logger(ev("Edit", file_path=str(self.root / "memory" / "situation.md")))
        self.logger(ev("Bash", "python -c \"open('memory/x.md','w').write('a')\""))
        cats = [ln.split("[", 1)[1].split("]", 1)[0] for ln in self.log_lines()]
        self.assertEqual(cats, ["memory-write", "memory-write(sh)"])


class SessionStartTest(TempRepo):
    def start(self):
        r = self.run_py("tools/hooks/session-start.py")
        self.assertEqual(r.returncode, 0)
        return r.stdout

    def test_template_marker_suppresses(self):
        (self.root / ".ai-agency-template").write_text("", encoding="utf-8")
        out = self.start()
        self.assertIn("本体（テンプレート）", out)
        self.assertNotIn("/setup ワークフロー", out)

    def test_uninitialized_suggests_setup(self):
        self.assertIn("未初期化", self.start())

    def test_counts_brief_review_inbox_overdue(self):
        self.seed_people()
        self.mem("inbox", "add", "未分類のもの")
        self.mem("commitment", "add", "--kind", "task", "--content", "遅れているタスク", "--due", d(-3))
        self.write(f"memory/briefs/{d(0)}.md",
                   "# ブリーフ\n\n## 🔴 今日中に対応\n- 該当なし\n\n## 🟡 今日見ておくべき\n- **P:x**\n  - A\n  - B\n")
        self.write("memory/briefs/review.md", f"# 記憶の点検 {d(0)}\n\n要対応 3 件（秘書が直してよい 2・ユーザーに確認 1）。\n")
        out = self.start()
        self.assertIn("人物 3 名", out)
        self.assertIn("未分類（秘書の作業中トレイ）が 1 件", out)
        self.assertIn("過去の未完了行が 1 行", out)
        self.assertIn("今日中 0 件・見ておく 2 件", out)
        self.assertIn("要対応 3 件（秘書が直してよい 2・ユーザーに確認 1）", out)


if __name__ == "__main__":
    unittest.main()
