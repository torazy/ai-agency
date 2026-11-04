"""チームの仕事の記録（tools/work.py）：作業依頼・参照と版・変化の検知・提案と判定・実行履歴・受け入れ。"""

import json
import unittest

from helpers import TempRepo, d

STATUS_V1 = "# 状況\n\n> 最終更新: 2026-11-01\n\n## 見積\n- 未着\n\n## 他\n- a\n"
STATUS_V2 = "# 状況\n\n> 最終更新: 2026-11-04\n\n## 見積\n- 届いた 120万\n\n## 他\n- a\n"


class WorkTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.write(".claude/agents/pm.md", "---\nname: pm\n---\n")
        self.write("memory/projects/x/status.md", STATUS_V1)
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "費用比較", "--due", d(10))
        self.mem("commitment", "add", "--kind", "task", "--content", "前工程", "--due", d(3))
        self.w = self.load("tools/work.py", "work_t")
        self.w.mem.ok = lambda m: None

    def work(self, *args, check=True, env=None):
        return self.run_py("tools/work.py", *args, check=check, env=env)

    def test_order_and_refs_record_versions(self):
        self.work("order", "C-001", "--purpose", "費用比較を作る", "--done-when", "3社の5年総額がある")
        self.work("ref", "add", "C-001", "memory/projects/x/status.md#見積", "--use", "待ち", "--label", "見積")
        self.work("ref", "add", "C-001", "C-002", "--use", "後工程")
        t = self.w.read_task(self.root, "C-001")
        self.assertEqual(t["order"]["目的"], "費用比較を作る")
        self.assertEqual(t["order"]["完了条件"], "3社の5年総額がある")
        self.assertEqual(t["order"]["期限"], d(10))                                     # 台帳から
        self.assertEqual([r["使い方"] for r in t["refs"]], ["待ち", "後工程"])
        self.assertEqual(t["refs"][1]["評価した版"], "open")
        self.assertEqual(len(t["refs"][0]["評価した版"]), 8)
        self.assertNotEqual(self.work("ref", "add", "C-001", "C-002", "--use", "後工程", check=False).returncode, 0)  # 重複
        self.assertNotEqual(self.work("ref", "add", "C-001", "x", "--use", "後工程", check=False).returncode, 0)      # 後工程は C-ID
        self.assertNotEqual(self.work("ref", "add", "C-001", "x", "--use", "なんとなく", check=False).returncode, 0)

    def test_changes_without_task_text_change_and_no_duplicates(self):
        self.work("ref", "add", "C-001", "memory/projects/x/status.md#見積", "--use", "待ち", "--label", "見積")
        self.work("ref", "add", "C-001", "memory/projects/x/status.md#他", "--use", "前提", "--label", "他")
        self.work("ref", "add", "C-001", "C-002", "--use", "後工程")
        self.assertEqual(self.w.changes(self.root), [])
        self.write("memory/projects/x/status.md", STATUS_V2)                           # 見積の節だけ変わる（最終更新は無視）
        self.mem("commitment", "set-state", "C-002", "done")
        kinds = sorted(c["kind"] for c in self.w.changes(self.root))
        self.assertEqual(kinds, ["再開", "着手"])                                       # 「他」の節は変わっていない
        first = json.loads(self.work("changes", "--json", "--propose").stdout)
        self.assertEqual(len(first["proposed"]), 2)
        again = json.loads(self.work("changes", "--json", "--propose").stdout)
        self.assertEqual(again["proposed"], [])                                         # 同じ変化からは二度提案しない
        self.work("ref", "touch", "C-001")                                              # 影響を判断した
        self.assertEqual(self.w.changes(self.root), [])

    def test_done_task_becomes_review_candidate_when_premise_changes(self):
        self.work("ref", "add", "C-001", "memory/projects/x/status.md#見積", "--use", "前提")
        self.mem("commitment", "set-state", "C-001", "done")
        self.write("memory/projects/x/status.md", STATUS_V2)
        self.assertEqual([c["kind"] for c in self.w.changes(self.root)], ["完了済みの見直し"])

    def test_proposals_need_fields_and_decisions_need_reason(self):
        r = self.work("propose", "--what", "会議の資料準備", "--why", "", "--evidence", "x", "--effect", "y", "--who", "@pm", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.work("propose", "--what", "会議の資料準備", "--why", "11/12 の定例", "--evidence", "カレンダー", "--effect", "当日慌てない", "--who", "@pm")
        self.assertIn("同じ", self.work("propose", "--what", "会議の資料準備", "--why", "a", "--evidence", "b", "--effect", "c", "--who", "@pm").stdout)
        self.assertNotEqual(self.work("decide", "S-001", "adopt", "--reason", "必要", check=False).returncode, 0)   # 採用は C-ID
        self.assertNotEqual(self.work("decide", "S-001", "hold", check=False).returncode, 0)                        # 理由が要る
        self.work("decide", "S-001", "adopt", "--task", "C-001", "--reason", "期限に効く・重複なし")
        self.assertEqual(self.w.proposal_rows(self.root)[0]["判定"], "採用→C-001")

    def test_runs_snapshot_staleness_and_returns(self):
        self.work("ref", "add", "C-001", "memory/projects/x/status.md#見積", "--use", "前提")
        r1 = self.w.run_start(self.root, "C-001")
        self.assertEqual(r1, "C-001-R1")
        self.assertEqual(self.w.running_runs(self.root)[0]["実行ID"], "C-001-R1")
        self.w.run_end(self.root, r1, "完了", 1.234)
        self.assertEqual(self.w.spent_today(self.root), 1.23)
        self.w.record_cost(self.root, "dispatch", 0.48)                                  # 定期の見直しの費用も数える
        self.w.record_cost(self.root, "dispatch", None)
        self.assertEqual(self.w.spent_today(self.root), 1.71)
        self.write("memory/projects/x/status.md", STATUS_V2)                            # 実行の後で前提が変わった
        self.assertEqual(self.w.accept(self.root, r1, "accept"), "古い条件")              # 最新として受け入れない
        self.assertIn("古い条件", self.w.read_task(self.root, "C-001")["runs"][0]["判定"])
        r2 = self.w.run_start(self.root, "C-001")
        self.w.run_end(self.root, r2, "完了", 0.5)
        with self.assertRaises(self.w.MemError):
            self.w.accept(self.root, r1, "accept")                                       # 新しい実行がある
        self.assertEqual(self.w.accept(self.root, r2, "return", "根拠不足"), "差し戻し")
        r3 = self.w.run_start(self.root, "C-001"); self.w.run_end(self.root, r3, "完了", 0.5)
        self.assertEqual(self.w.accept(self.root, r3, "return"), "差し戻し")
        r4 = self.w.run_start(self.root, "C-001"); self.w.run_end(self.root, r4, "完了", 0.5)
        self.assertEqual(self.w.accept(self.root, r4, "return"), "人へ確認")              # 差し戻しは2回まで
        self.assertEqual(self.w.accept(self.root, r4, "accept"), "受け入れ")
        self.work("applied", "C-001-R4")
        self.assertTrue(self.w.read_task(self.root, "C-001")["runs"][-1]["反映"].startswith("済"))

    def test_note_and_handoff(self):
        self.work("note", "C-001", "部長から新条件：5年総額")
        self.work("note", "C-001", "会議が前倒し")
        self.work("handoff", "C-001", "3社のうち2社済み。残りは C 社")
        t = self.w.read_task(self.root, "C-001")
        self.assertEqual(len(t["notes"]), 2)
        self.assertIn("部長から新条件", t["notes"][0])
        self.assertIn("C 社", t["handoff"])
        self.assertNotIn("（記録なし）", self.read("memory/work/tasks/C-001.md").split("## 途中の変更")[1].split("##")[0])

    def test_scan_follow_and_due(self):
        self.mem("commitment", "add", "--kind", "given", "--who", "山田", "--content", "見積", "--due", d(5), "--follow", d(0))
        items = {s["key"].split("|")[0] for s in self.w.scan(self.root)}
        self.assertEqual(items, {"フォロー", "期限"})                                  # C-003 のフォロー日・C-002 の期限が近い
        self.assertEqual(len(json.loads(self.work("scan", "--json", "--propose").stdout)["proposed"]), 2)
        self.assertEqual(json.loads(self.work("scan", "--json", "--propose").stdout)["proposed"], [])
        # 判定済み（要承認）になった後も、同じ起因からは二度提案しない（実地で S-004 と S-007 が重複した）
        self.work("decide", "S-001", "ask", "--reason", "人の判断")
        self.work("decide", "S-002", "ask", "--reason", "人の判断")
        self.assertEqual(json.loads(self.work("scan", "--json", "--propose").stdout)["proposed"], [])

    def test_settings_limits_status_tick(self):
        lim = self.w.limits(self.root)
        self.assertEqual((lim["max_running"], lim["daily_usd"], lim["auto_start"]), (2, 10.0, False))
        p = self.root / "memory/work/settings.md"
        p.write_text(p.read_text(encoding="utf-8").replace("| 同時実行の上限 | 2 |", "| 同時実行の上限 | 1 |")
                     .replace("| 自動で着手 | しない |", "| 自動で着手 | 委任範囲 |"), encoding="utf-8")
        lim = self.w.limits(self.root)
        self.assertEqual((lim["max_running"], lim["auto_start"]), (1, True))
        self.assertTrue(self.w.status(self.root)["can_start"])
        self.assertEqual(self.work("tick", check=False).returncode, 0)
        self.assertEqual(self.work("tick", check=False).returncode, 1)                 # 間隔が来ていない

    def test_unattended_allows_work_records_but_not_applied(self):
        env = {"AI_AGENCY_UNATTENDED": "1"}
        self.assertEqual(self.work("propose", "--what", "a", "--why", "b", "--evidence", "c", "--effect", "d", "--who", "@pm", env=env).returncode, 0)
        self.assertNotEqual(self.work("applied", "C-001-R1", check=False, env=env).returncode, 0)

    def test_migrate_creates_work_area(self):
        import shutil
        shutil.rmtree(self.root / "memory/work")
        self.assertIn("work/proposals.md", self.mem("migrate").stdout)
        self.assertTrue((self.root / "memory/work/settings.md").exists())
        self.assertTrue((self.root / "memory/work/tasks").is_dir())


if __name__ == "__main__":
    unittest.main()
