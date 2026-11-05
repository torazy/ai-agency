"""時間をまたぐシナリオ：作成→変更→待機→再開→確認→さらに変更→承認。

単体の正常系だけでは、変更・待機・承認の順序で壊れる箇所を拾えなかった（2026-11-05 外部レビュー #2〜#4）。
ここでは操作の順序そのものを入れ替えて確かめる。"""

import json
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


def _dead_pid() -> int:
    import subprocess
    import sys
    return int(subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True).stdout)


class WaitingLaunchTest(Base):
    """#5 起動待ち（作業依頼まで書いたが起動していない・差し戻しのあと次が無い）を、次の定期の見直しが拾う。"""

    def setUp(self):
        super().setUp()
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "別件", "--due", d(10))   # C-002
        self.w.ensure_work(self.root)
        p = self.root / "memory/work/settings.md"
        p.write_text(p.read_text(encoding="utf-8").replace("| 同時実行の上限 | 2 |", "| 同時実行の上限 | 1 |"), encoding="utf-8")
        self.rt.shutil.which = lambda name: "claude"

    def waiting(self):
        return [w["task"] for w in self.w.launch_waiting(self.root)]

    def test_refused_by_capacity_then_picked_up(self):
        r2 = self.w.run_start(self.root, "C-002")
        self.rt.state_path(self.root, r2).parent.mkdir(parents=True, exist_ok=True)
        self.rt.state_path(self.root, r2).write_text(json.dumps({"start": time.time(), "pid": os.getpid()}), encoding="utf-8")
        self.w.order_set(self.root, "C-001", {"目的": "費用比較"})                                 # 採用して作業依頼まで書いた
        with self.assertRaisesRegex(ValueError, "同時実行"):
            self.rt.launch(self.root, "C-001")                                                    # 上限で断られた
        self.assertEqual(self.waiting(), ["C-001"])
        self.assertFalse(self.w.pending(self.root)["work"])                                       # 起動できない間は Claude を起こさない
        self.w.run_end(self.root, r2, "完了", 0.5)
        self.report(r2)
        self.w.accept(self.root, r2, "accept")
        pd = self.w.pending(self.root)
        self.assertEqual((pd["waiting"], pd["work"]), (["C-001"], True))                         # 枠が空いた次の回に拾う

    def test_returned_then_deferred_then_new_note(self):
        rid = self.finished_run()
        self.w.accept(self.root, rid, "return", "根拠不足")
        self.assertEqual(self.waiting(), ["C-001"])
        r = self.run_py("tools/work.py", "defer", "C-001", "部長の判断待ち")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.waiting(), [])                                                      # 見送り：理由を残して外す
        self.w.note(self.root, "C-001", "部長：進めてよい")
        self.assertEqual(self.waiting(), ["C-001"])                                               # 新しい変更で戻る
        self.w.run_start(self.root, "C-001")
        self.assertEqual(self.waiting(), [])

    def test_wait_refs_and_closed_tasks_are_not_waiting(self):
        self.w.ensure_task(self.root, "C-001")
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "待ち", "見積")   # 変化の提案で再開する
        self.w.ensure_task(self.root, "C-002")
        self.mem("commitment", "set-state", "C-002", "done")
        self.assertEqual(self.waiting(), [])


class ReapTest(Base):
    """#8 終了の記録が無いまま実行のプロセスが無くなったもの（PC の再起動など）を閉じ、同時実行の枠を戻す。"""

    def setUp(self):
        super().setUp()
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "別件", "--due", d(10))   # C-002

    def running(self, cid, **state):
        rid = self.w.run_start(self.root, cid)
        if state:
            self.rt.state_path(self.root, rid).parent.mkdir(parents=True, exist_ok=True)
            self.rt.state_path(self.root, rid).write_text(json.dumps(state), encoding="utf-8")
        return rid

    def test_dead_process_frees_the_slot(self):
        rid = self.running("C-002", start=time.time() - 60, pid=_dead_pid())
        self.assertEqual(len(self.w.running_runs(self.root)), 1)
        self.rt.check_capacity(self.root, 1.0)                                                    # 起動前に後始末する
        self.assertEqual(self.w.running_runs(self.root), [])
        self.assertEqual(self.w.run_row(self.root, rid)["結果"], "終了不明")
        self.assertEqual([r["実行ID"] for r in self.w.unjudged_runs(self.root)], [rid])           # 確認役が見る（復旧が要る仕事）
        self.assertIn("［記録］", self.notes("C-002")[-1])

    def test_live_and_starting_runs_are_kept(self):
        self.running("C-001", start=time.time() - 10, pid=os.getpid())                           # 生きている
        self.running("C-002")                                                                      # 起動の途中（記録がまだ無い）
        self.assertEqual(self.rt.reap(self.root), [])

    def test_reused_pid_is_not_ours(self):
        rid = self.running("C-001", start=time.time() - 10 * 86400, pid=os.getpid())             # 番号が別のプロセスに使い回された
        self.assertEqual(self.rt.reap(self.root), [rid])

    def test_stop_by_run_id_and_never_kill_reused_pid(self):
        from unittest import mock
        old = self.running("C-001", start=time.time() - 10 * 86400, pid=os.getpid())              # 孤児（番号は使い回し）
        self.w.read_task(self.root, "C-001")
        new = self.w.run_start(self.root, "C-001")                                                # やり直しが動いている
        with mock.patch.object(self.rt.subprocess, "run") as run, mock.patch.object(self.rt.os, "killpg", create=True) as kp:
            self.assertEqual(self.rt.stop(self.root, old, "孤児を閉じる"), old)
            run.assert_not_called()                                                                # 使い回された番号は殺さない
            kp.assert_not_called()
        self.assertEqual(self.w.run_row(self.root, old)["結果"], "停止")
        self.assertEqual(self.w.run_row(self.root, new)["結果"], "実行中")
        with self.assertRaises(ValueError):
            self.rt.stop(self.root, old)                                                           # もう実行中ではない


class FlipFlopTest(Base):
    """#6 値が元に戻ってまた変わったら、新しい出来事として提案する（同じ出来事は二度提案しない）。"""

    def test_100_200_100_200(self):
        v = {1: "# 状況\n\n## 見積\n- 100 万円\n", 2: "# 状況\n\n## 見積\n- 200 万円\n"}
        self.write("memory/projects/x/status.md", v[1])
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "前提", "見積")
        made = []
        for step in (2, 1, 2):
            self.write("memory/projects/x/status.md", v[step])
            sids = self.w.propose_from_changes(self.root)
            self.assertEqual(self.w.propose_from_changes(self.root), [])                         # 判定前は同じ出来事：二度提案しない
            made += sids
            self.w.ref_touch(self.root, "C-001")
            self.w.decide(self.root, sids[0], "reject", "", "影響なし")
        self.assertEqual(len(made), 3)


def _age_start(test, cid, rid, minutes):
    import re
    p = test.root / f"memory/work/tasks/{cid}.md"
    old = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - minutes * 60))
    txt = re.sub(rf"(\| {re.escape(rid)} \| )\d{{4}}-\d\d-\d\d \d\d:\d\d", rf"\g<1>{old}", p.read_text(encoding="utf-8"))
    p.write_text(txt, encoding="utf-8")


class ReapRaceTest(Base):
    """#8 の後始末が、終わりかけ・書きかけ・確かめられない実行を誤って閉じない（R-021 の独立レビュー）。"""

    def test_does_not_overwrite_a_run_that_ended_meanwhile(self):
        from unittest import mock
        rid = self.w.run_start(self.root, "C-001")
        self.rt._state_update(self.root, rid, start=time.time() - 30, pid=os.getpid())
        w, root = self.w, self.root

        def finish_then_dead(pid, since):                                                          # 調べている間に実行役が終わった
            if w.run_row(root, rid)["結果"] == "実行中":
                w.run_end(root, rid, "完了", 1.5)
            return False
        with mock.patch.object(self.rt, "_ours", side_effect=finish_then_dead):
            self.assertEqual(self.rt.reap(self.root), [])
        row = self.w.run_row(self.root, rid)
        self.assertEqual((row["結果"], row["費用"]), ("完了", "1.50"))                           # 完了と費用を消さない

    def test_runner_end_overrides_a_wrong_unknown_but_not_a_stop(self):
        rid = self.w.run_start(self.root, "C-001")
        self.w.run_end(self.root, rid, "終了不明", None, expect=("実行中",))
        self.assertTrue(self.w.run_end(self.root, rid, "完了", 1.0, expect=("実行中", "終了不明")))
        r2 = self.w.run_start(self.root, "C-001")
        self.w.run_end(self.root, r2, "停止", None, expect=("実行中",))
        self.assertFalse(self.w.run_end(self.root, r2, "完了", 1.0, expect=("実行中", "終了不明")))
        self.assertEqual(self.w.run_row(self.root, r2)["結果"], "停止")

    def test_torn_or_foreign_or_missing_state_is_not_reaped(self):
        rid = self.w.run_start(self.root, "C-001")
        _age_start(self, "C-001", rid, 15)
        self.rt.state_path(self.root, rid).parent.mkdir(parents=True, exist_ok=True)
        self.rt.state_path(self.root, rid).write_text("", encoding="utf-8")                      # 書きかけ
        self.assertEqual(self.rt.reap(self.root), [])
        self.rt.state_path(self.root, rid).write_text(json.dumps(
            {"start": time.time() - 900, "pid": _dead_pid(), "host": "another-pc"}), encoding="utf-8")   # 別の PC の実行
        self.assertEqual(self.rt.reap(self.root), [])
        self.rt.state_path(self.root, rid).unlink()                                               # .state が消された
        self.assertEqual(self.rt.reap(self.root), [])
        st = self.rt.read_status(self.root, "C-001")
        self.assertEqual(st["state"], "running")
        self.assertIn(f"--stop {rid}", st["unverifiable"])                                        # 人に見せる
        self.rt.stop(self.root, rid)                                                              # 実行ID で閉じられる
        self.assertEqual(self.w.run_row(self.root, rid)["結果"], "停止")


class WakeOnlyWhenStartableTest(Base):
    """#5 起動待ちでも、実際に起動できないなら定期の見直しを起こさない（空振りの費用を使わない）。"""

    def setUp(self):
        super().setUp()
        self.w.ensure_work(self.root)
        self.rt.shutil.which = lambda name: "claude"

    def test_budget_nearly_spent(self):
        self.w.order_set(self.root, "C-001", {"目的": "費用比較"})
        sd = self.w.state_dir(self.root)
        sd.mkdir(parents=True, exist_ok=True)
        daily = self.w.limits(self.root)["daily_usd"]
        (sd / "costs.jsonl").write_text(json.dumps({"date": self.w.mem.today(), "cost": daily - 0.5}) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.rt.launch(self.root, "C-001")                                                    # 残り 0.5 では起動できない
        self.assertEqual([x["task"] for x in self.w.launch_waiting(self.root)], ["C-001"])
        self.assertFalse(self.w.pending(self.root)["work"])

    def test_missing_agent_is_listed_with_reason_but_does_not_wake(self):
        self.write(".claude/agents/ghost.md", "---\nname: ghost\n---\n")
        self.mem("commitment", "add", "--kind", "given", "--who", "@ghost", "--content", "幽霊", "--due", d(10))   # C-002
        self.w.order_set(self.root, "C-002", {"目的": "幽霊"})
        (self.root / ".claude/agents/ghost.md").unlink()                                          # 担当を消した・改名した
        lw = {x["task"]: x for x in self.w.launch_waiting(self.root)}
        self.assertFalse(lw["C-002"]["startable"])
        self.assertIn("いない", lw["C-002"]["blocked"])
        self.assertFalse(self.w.pending(self.root)["work"])

    def test_resolved_wait_and_finished_predecessor_are_waiting(self):
        self.w.order_set(self.root, "C-001", {"目的": "費用比較"})
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "待ち", "見積")
        self.assertEqual(self.w.launch_waiting(self.root), [])                                    # まだ待っている
        self.write("memory/projects/x/status.md", STATUS_V2)
        sids = self.w.propose_from_changes(self.root)
        self.w.decide(self.root, sids[0], "adopt", "C-001", "見積が届いた")                       # 再開を採用
        self.w.ref_touch(self.root, "C-001")
        self.assertEqual([x["task"] for x in self.w.launch_waiting(self.root)], ["C-001"])       # 起動を断られても拾う
        self.mem("commitment", "add", "--kind", "task", "--content", "前工程", "--due", d(5))       # C-002
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "後工程", "--due", d(9))   # C-003
        self.w.ref_add(self.root, "C-003", "C-002", "後工程", "前工程")
        self.assertNotIn("C-003", [x["task"] for x in self.w.launch_waiting(self.root)])
        self.mem("commitment", "set-state", "C-002", "done")
        self.assertIn("C-003", [x["task"] for x in self.w.launch_waiting(self.root)])


class ChangeEventKeyTest(Base):
    """#6 判定しても版を進めていない間は同じ出来事。場所に「／」があっても数え違えない。"""

    def setUp(self):
        super().setUp()
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "前提", "見積")
        self.write("memory/projects/x/status.md", STATUS_V2)

    def test_board_reject_or_hold_without_touch_is_not_reproposed(self):
        s1 = self.w.propose_from_changes(self.root)
        self.assertEqual(self.srv.do_post(self.root, "/api/proposal/decide",
                                          {"sid": s1[0], "decision": "reject", "reason": "影響なし"})[0], 200)
        self.assertEqual(self.w.propose_from_changes(self.root), [])
        self.assertEqual(self.w.pending(self.root)["proposed"], [])

    def test_flipflop_with_slash_in_heading(self):
        place = "memory/projects/y/status.md#見積／予算"
        v = {1: "# 状況\n\n## 見積／予算\n- 100 万円\n", 2: "# 状況\n\n## 見積／予算\n- 200 万円\n"}
        self.write("memory/projects/y/status.md", v[1])
        self.w.ref_add(self.root, "C-001", place, "前提", "予算")
        self.w.ref_touch(self.root, "C-001")
        made = []
        for step in (2, 1, 2):
            self.write("memory/projects/y/status.md", v[step])
            sids = self.w.propose_from_changes(self.root)
            made += sids
            self.w.ref_touch(self.root, "C-001")
            for sid in sids:
                self.w.decide(self.root, sid, "reject", "", "影響なし")
        self.assertEqual(len(made), 3)                                                            # 予算の往復3回がそれぞれ提案になる


if __name__ == "__main__":
    unittest.main()
