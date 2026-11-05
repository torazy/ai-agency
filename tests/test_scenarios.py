"""時間をまたぐシナリオ：作成→変更→待機→再開→確認→さらに変更→承認。

単体の正常系だけでは、変更・待機・承認の順序で壊れる箇所を拾えなかった（2026-11-05 外部レビュー #2〜#4）。
ここでは操作の順序そのものを入れ替えて確かめる。"""

import os
import time
import unittest

from helpers import TempRepo, d

STATUS_V1 = "# 状況\n\n> 最終更新: 2026-11-01\n\n## 見積\n- 未着\n"
STATUS_V2 = "# 状況\n\n> 最終更新: 2026-11-04\n\n## 見積\n- 届いた 120万\n"


class Base(TempRepo):
    def setUp(self):
        super().setUp()
        self.write(".claude/agents/pm.md", "---\nname: pm\n---\n")
        self.write("memory/projects/x/status.md", STATUS_V1)
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "費用比較", "--due", d(10))
        self.rt = self.load("tools/run_task.py", "run_task_scn")
        self.w = self.rt.work
        self.w.mem.ok = lambda m: self.w.mem.LAST_OK.append(m)
        self.srv = self.load("tools/board/server.py", "board_scn")
        self.srv.mem.ok = lambda m: self.srv.mem.LAST_OK.append(m)

    def order(self, cid="C-001"):
        return self.w.read_task(self.root, cid)["order"]

    def notes(self, cid="C-001"):
        return self.w.read_task(self.root, cid)["notes"]

    def report(self, rid, minutes_ago=2):
        """報告を書く。途中の変更は報告の時刻と分単位で比べるので、既定では2分前に書いたことにする。"""
        self.write(f"memory/work/results/{rid}.md", f"# {rid}\n\n## 結論\n- 3案\n")
        t = time.time() - minutes_ago * 60
        os.utime(self.root / f"memory/work/results/{rid}.md", (t, t))

    def finished_run(self, cid="C-001", report=True):
        rid = self.w.run_start(self.root, cid)
        self.w.run_end(self.root, rid, "完了", 0.5)
        if report:
            self.report(rid)
        return rid

    def state(self, cid="C-001"):
        return next(r for r in self.w.mem.commitment_rows(self.root) if r["ID"] == cid)["状態"]


class LedgerToOrderTest(Base):
    """#2 台帳の内容・期限を変えたら、作業セッションが読む作業依頼にも伝わる。"""

    def test_board_content_and_due_reach_the_order(self):
        self.w.ensure_task(self.root, "C-001")
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/content", {"id": "C-001", "content": "5社の比較"})[0], 200)
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/due", {"id": "C-001", "due": d(2)})[0], 200)
        self.assertEqual((self.order()["目的"], self.order()["期限"]), ("5社の比較", d(2)))
        self.assertTrue(self.w.read_task(self.root, "C-001") and
                        self.read("memory/work/tasks/C-001.md").startswith("# C-001 5社の比較"))
        joined = "\n".join(self.notes())
        self.assertIn("台帳の内容が変わった：費用比較 → 5社の比較", joined)       # 実行中のセッションと確認役に伝わる
        self.assertIn(f"台帳の期限が変わった：{d(10)} → {d(2)}", joined)

    def test_refined_purpose_is_kept_and_flagged(self):
        self.w.order_set(self.root, "C-001", {"目的": "費用比較（初期費用と月額、5年総額）"})   # 秘書が書き足した
        self.mem("commitment", "set-content", "C-001", "費用と運用負担の比較")
        self.assertEqual(self.order()["目的"], "費用比較（初期費用と月額、5年総額）")           # 書き足しは消さない
        self.assertIn("見直す", self.notes()[-1])

    def test_no_order_no_file(self):
        self.mem("commitment", "set-content", "C-001", "別の内容")                             # 作業依頼が無ければ作らない
        self.assertFalse((self.root / "memory/work/tasks/C-001.md").exists())
        self.mem("commitment", "set-due", "C-001", d(10))                                       # 同じ期限なら何も残さない
        self.w.ensure_task(self.root, "C-001")
        self.mem("commitment", "set-due", "C-001", d(10))
        self.assertEqual(self.notes(), [])


class StaleResultTest(Base):
    """#3 作業依頼と途中の変更も、成果の鮮度に含める。"""

    def test_order_changed_after_start_is_stale(self):
        rid = self.w.run_start(self.root, "C-001")
        self.w.order_set(self.root, "C-001", {"完了条件": "5年総額と移行費を含める"})
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.assertEqual(self.w.accept(self.root, rid, "accept"), "古い条件")

    def test_content_change_during_run_is_stale(self):
        rid = self.w.run_start(self.root, "C-001")
        self.mem("commitment", "set-content", "C-001", "5社の費用比較")                         # 実行中に依頼が変わる
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.report(rid, 0)
        self.assertEqual(self.w.accept(self.root, rid, "accept"), "古い条件")

    def test_note_before_report_is_read_note_after_report_is_not(self):
        rid = self.w.run_start(self.root, "C-001")
        self.w.note(self.root, "C-001", "税込で")                                              # 報告の前：手順6で読まれる
        p = self.root / "memory/work/tasks/C-001.md"                                             # 変更は5分前のこととする
        old = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 300))
        p.write_text("\n".join(f"- {old} 税込で" if ln.endswith(" 税込で") else ln
                               for ln in p.read_text(encoding="utf-8").split("\n")), encoding="utf-8")
        self.report(rid, 2)                                                                      # 報告は2分前
        self.assertEqual(self.w.stale_refs(self.root, rid), [])
        self.report(rid, 0)                                                                      # 同じ分の変更は読めていない側に倒す
        self.w.note(self.root, "C-001", "単位は千円")
        self.assertEqual(self.w.stale_refs(self.root, rid), ["途中の変更（報告のあとに 1 件）"])
        self.report(rid, 2)
        self.w.note(self.root, "C-001", "対象を5社に増やす")                                   # 報告のあと・終了の記録の前
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.assertEqual(self.w.stale_refs(self.root, rid), ["途中の変更（報告のあとに 2 件）"])
        self.assertEqual(self.w.accept(self.root, rid, "accept"), "古い条件")

    def test_run_without_end_record_still_checks_notes(self):
        rid = self.w.run_start(self.root, "C-001")                                              # 終了の記録が来ない（PC の再起動）
        self.report(rid)
        self.w.note(self.root, "C-001", "移行費も入れる")
        self.assertEqual(self.w.stale_refs(self.root, rid), ["途中の変更（報告のあとに 1 件）"])

    def test_due_earlier_after_report_is_stale_later_is_not(self):
        rid = self.finished_run()
        self.w.accept(self.root, rid, "accept")
        self.mem("commitment", "set-due", "C-001", d(20))                                       # 延長：成果物は古くならない
        self.assertEqual(self.w.stale_refs(self.root, rid), [])
        self.assertIn("［記録］台帳の期限が変わった", self.notes()[-1])
        self.mem("commitment", "set-due", "C-001", d(3))                                        # 前倒し：伝えるべき変更
        self.assertEqual(self.w.stale_refs(self.root, rid), ["途中の変更（報告のあとに 1 件）"])

    def test_info_notes_do_not_make_results_stale(self):
        rid = self.finished_run()
        self.w.note(self.root, "C-001", "部長：報告のままで可", info=True)                       # 人の回答（成果物は変えない）
        r = self.run_py("tools/work.py", "note", "C-001", "記録だけ", "--info")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.w.accept(self.root, rid, "accept"), "受け入れ")

    def test_heading_word_in_content_does_not_misplace_note(self):
        self.w.order_set(self.root, "C-001", {"目的": "費用比較（3社・税込）"})
        self.mem("commitment", "set-content", "C-001", "途中の変更を反映して5社で比較")
        text = self.read("memory/work/tasks/C-001.md")
        self.assertTrue(text.startswith("# C-001 費用比較"))                                    # 目的を置き換えないならタイトルも変えない
        self.assertEqual(len(self.notes()), 1)                                                   # 見出しの下に入り、数えられる
        self.assertLess(text.index("## 途中の変更"), text.index("台帳の内容が変わった"))

    def test_plan_change_is_not_a_requirement_change(self):
        rid = self.w.run_start(self.root, "C-001")
        self.w.order_set(self.root, "C-001", {"レビュー": "qa", "上限": "3 USD"})               # 進め方・上限は成果物を変えない
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.assertEqual(self.w.accept(self.root, rid, "accept"), "受け入れ")

    def test_old_records_without_order_version_still_work(self):
        rid = self.finished_run()
        p = self.root / "memory/work/tasks/C-001.md"
        text = p.read_text(encoding="utf-8")
        used = self.w.run_row(self.root, rid)["使った版"]
        p.write_text(text.replace(used, "参照なし"), encoding="utf-8")                          # 以前の形の記録
        self.w.order_set(self.root, "C-001", {"完了条件": "別"})
        self.assertEqual(self.w.accept(self.root, rid, "accept"), "受け入れ")                    # 比べる材料が無いものは比べない


class ApproveRechecksTest(Base):
    """#4 確認役の受け入れのあとで条件が変わったら、人の承認でも完了にしない。"""

    def test_accepted_then_premise_changes_then_approve(self):
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "前提", "見積")
        rid = self.finished_run()
        self.assertEqual(self.w.accept(self.root, rid, "accept"), "受け入れ")                  # 確認役
        self.write("memory/projects/x/status.md", STATUS_V2)                                     # その後で前提が変わる
        self.assertEqual(self.rt.read_status(self.root, "C-001")["stale"], ["memory/projects/x/status.md#見積"])  # 押す前に見える
        code, res = self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})
        self.assertEqual(code, 400, res)
        self.assertIn("古い条件", res["error"])
        self.assertEqual(self.state(), "open")
        self.assertTrue(self.w.run_row(self.root, rid)["判定"].startswith("古い条件"))           # 判定も書き換えて残す

    def test_accepted_then_order_changes_then_approve(self):
        rid = self.finished_run()
        self.w.accept(self.root, rid, "accept")
        self.srv.do_post(self.root, "/api/commitment/content", {"id": "C-001", "content": "5社の比較"})
        self.assertEqual(self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 400)
        self.assertEqual(self.state(), "open")

    def test_state_button_and_cli_done_also_recheck(self):
        rid = self.finished_run()
        self.w.accept(self.root, rid, "accept")
        self.srv.do_post(self.root, "/api/commitment/content", {"id": "C-001", "content": "5社の比較"})
        code, res = self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-001", "state": "done"})
        self.assertEqual(code, 400, res)                                                          # 状態ボタン・かんばん
        r = self.mem("commitment", "set-state", "C-001", "done", check=False)
        self.assertNotEqual(r.returncode, 0)                                                      # CLI
        self.assertIn("--force", r.stdout + r.stderr)
        self.assertEqual(self.state(), "open")
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-001", "state": "waiting"})[0], 200)
        self.mem("commitment", "set-state", "C-001", "done", "--force")                          # 人が影響なしと判断
        self.assertEqual(self.state(), "done")

    def test_accepted_and_unchanged_is_approved(self):
        rid = self.finished_run()
        self.w.accept(self.root, rid, "accept")
        self.assertEqual(self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 200)
        self.assertEqual(self.state(), "done")


class FullScenarioTest(Base):
    """作成→変更→待機→再開→確認→さらに変更→承認 を1本で通す。"""

    def test_create_change_wait_resume_confirm_change_approve(self):
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "前提", "見積")   # 作成
        self.mem("commitment", "set-content", "C-001", "5社の費用比較")                         # 起動前に変更
        r1 = self.w.run_start(self.root, "C-001")                                               # 起動（新しい依頼を読む）
        self.assertEqual(self.order()["目的"], "5社の費用比較")
        self.mem("commitment", "set-content", "C-001", "5社の費用と運用負担の比較")              # 実行中にさらに変更
        self.w.run_end(self.root, r1, "完了", 0.5)
        self.report(r1, 0)
        self.assertEqual(self.w.accept(self.root, r1, "accept"), "古い条件")                   # 確認役：古い
        r2 = self.finished_run()                                                                          # 再開（やり直し）
        self.assertEqual(self.w.accept(self.root, r2, "accept"), "受け入れ")                   # 確認役：OK
        self.write("memory/projects/x/status.md", STATUS_V2)                                     # 承認の前に前提が変わる
        self.assertEqual(self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 400)
        self.assertEqual(self.state(), "open")
        r3 = self.finished_run()                                                                          # 新しい前提でやり直し
        self.assertEqual(self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 200)
        self.assertEqual(self.state(), "done")
        self.assertTrue(self.w.run_row(self.root, r3)["判定"].startswith("受け入れ"))


if __name__ == "__main__":
    unittest.main()
