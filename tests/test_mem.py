"""tools/mem.py（書き込み）の振る舞い。表が崩れない・ID が振られる・確認ケースで止まる、を守る。"""

import json
import unittest

from helpers import TempRepo, d


class InboxTest(TempRepo):
    def test_add_resolve_and_dedupe(self):
        r = self.mem("inbox", "add", "来週の定例は水曜", "--source", "会話")
        self.assertIn("I-001", r.stdout)
        r = self.mem("inbox", "add", "来週の定例は  水曜", "--source", "会話")  # 空白違いの同文
        self.assertIn("追加しない", r.stdout)
        self.assertEqual(self.read("memory/inbox.md").count("- [ ] I-"), 1)
        self.mem("inbox", "resolve", "I-001", "--to", "memory/situation.md")
        text = self.read("memory/inbox.md")
        self.assertIn("- [x] I-001", text)
        self.assertIn("→ 記録先: memory/situation.md", text)
        self.assertNotIn("- [ ] I-001", text)

    def test_resolve_unknown_fails(self):
        r = self.mem("inbox", "resolve", "I-999", "--to", "x", check=False)
        self.assertNotEqual(r.returncode, 0)


class CommitmentTest(TempRepo):
    def test_add_list_and_state(self):
        self.mem("commitment", "add", "--kind", "received", "--content", "比較表の提出", "--who", "架空部長",
                 "--due", d(7), "--rel", "P:kintai-saas")
        self.mem("commitment", "add", "--kind", "task", "--content", "経費精算", "--due", d(2))
        self.mem("commitment", "add", "--kind", "given", "--content", "レビュー依頼", "--who", "鈴木さん",
                 "--due", d(5), "--follow", d(4))
        rows = json.loads(self.mem("commitment", "list", "--json").stdout)
        self.assertEqual([r["ID"] for r in rows], ["C-002", "C-001", "C-003"])  # 表の順（自分→受けた→出した）
        self.assertEqual({r["kind"] for r in rows}, {"task", "received", "given"})
        self.mem("commitment", "set-state", "C-001", "dropped")
        self.mem("commitment", "set-due", "C-002", d(3))
        rows = {r["ID"]: r for r in json.loads(self.mem("commitment", "list", "--json").stdout)}
        self.assertEqual(rows["C-001"]["状態"], "dropped")
        self.assertEqual(rows["C-002"]["期限"], d(3))
        self.assertEqual(json.loads(self.mem("commitment", "list", "--rel", "P:kintai-saas", "--json").stdout)[0]["ID"], "C-001")
        self.mem("check")  # 列数が崩れていない

    def test_given_requires_follow_and_received_requires_who(self):
        self.assertNotEqual(self.mem("commitment", "add", "--kind", "given", "--content", "x", "--who", "y", check=False).returncode, 0)
        self.assertNotEqual(self.mem("commitment", "add", "--kind", "received", "--content", "x", check=False).returncode, 0)
        self.assertNotEqual(self.mem("commitment", "set-state", "C-001", "finished", check=False).returncode, 0)


class PersonTest(TempRepo):
    def test_new_alias_icon_observe(self):
        self.mem("person", "new", "tanaka-kenji-abc-corp-cfo", "--name", "田中 健司", "--title", "CFO",
                 "--email", "tanaka@example.invalid", "--alias", "田中CFO", "--icon", "crown")
        p = "memory/people/tanaka-kenji-abc-corp-cfo.md"
        self.assertIn("- **呼び名**: 田中CFO", self.read(p))
        self.mem("person", "add-alias", "tanaka-kenji-abc-corp-cfo", "健司さん")
        self.mem("person", "add-alias", "tanaka-kenji-abc-corp-cfo", "健司さん")  # 2回目は何もしない
        self.assertIn("- **呼び名**: 田中CFO、健司さん", self.read(p))
        self.mem("person", "set-icon", "tanaka-kenji-abc-corp-cfo", "🌸")
        self.assertIn("- **アイコン**: 🌸", self.read(p))
        self.mem("person", "observe", "tanaka-kenji-abc-corp-cfo", "--situation", "予算会議", "--judgment", "根拠を求めた")
        self.assertIn("根拠を求めた", self.read(p))
        self.mem("check")

    def test_identifier_rules(self):
        self.assertNotEqual(self.mem("person", "new", "tanaka", "--name", "田中", check=False).returncode, 0)
        self.mem("person", "new", "tanaka-kenji-abc-corp", "--name", "田中 健司")
        self.assertNotEqual(self.mem("person", "new", "tanaka-kenji-abc-corp", "--name", "田中 健司", check=False).returncode, 0)


class DecisionAndSyncTest(TempRepo):
    def test_decision_proposed_then_decided(self):
        self.mem("decision", "add", "--project", "kintai-saas", "--title", "クラウド型を本命にする", "--content", "c",
                 "--reason", "r", "--prediction", "p", "--verify-date", d(30))
        path = "memory/projects/kintai-saas/decisions.md"
        self.assertIn("- **状態**: 提案（未承認）", self.read(path))
        self.assertIn("- **ID**: D-001", self.read(path))
        self.assertNotEqual(self.mem("decision", "set-status", "kintai-saas", "クラウド型", "decided", check=False).returncode, 0)  # 部分一致では動かない
        self.mem("decision", "set-status", "kintai-saas", "D-001", "decided")
        self.assertIn("- **状態**: 決定", self.read(path))

    def test_sync_mark_and_show(self):
        self.assertIn("取り込み記録なし", self.mem("sync", "show").stdout)
        self.mem("sync", "mark", "gmail", "--at", "2026-11-04T09:00")
        self.assertEqual(json.loads(self.mem("sync", "show", "--json").stdout), {"gmail": "2026-11-04T09:00"})
        self.assertNotEqual(self.mem("sync", "mark", "Gmail!", check=False).returncode, 0)

    def test_migrate_is_idempotent(self):
        self.mem("migrate")
        before = self.read("memory/commitments.md")
        self.mem("migrate")
        self.assertEqual(before, self.read("memory/commitments.md"))


if __name__ == "__main__":
    unittest.main()
