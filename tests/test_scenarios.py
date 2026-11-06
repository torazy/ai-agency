"""時間をまたぐシナリオ：作成→変更→待機→再開→確認→さらに変更→承認。

単体の正常系だけでは、変更・待機・承認の順序で壊れる箇所を拾えなかった（2026-11-05 外部レビュー #2〜#4）。
ここでは操作の順序そのものを入れ替えて確かめる。"""

import json
import os
import threading
import time
import unittest
from pathlib import Path

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
        new = self.w.run_start(self.root, "C-001")                                                # やり直しが動いている
        real_run, kills = self.rt.subprocess.run, []

        def run(args, *a, **k):                                                                    # 生死の確認（ps）は本物、taskkill は記録だけ
            if args and args[0] == "taskkill":
                kills.append(args)
                return None
            return real_run(args, *a, **k)
        with mock.patch.object(self.rt.subprocess, "run", side_effect=run), \
                mock.patch.object(self.rt.os, "killpg", create=True) as kp:
            self.assertEqual(self.rt.stop(self.root, old, "孤児を閉じる"), old)
        self.assertEqual(kills, [])                                                                # 使い回された番号は殺さない
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

    def test_predecessor_done_open_done_in_the_same_minute(self):
        self.mem("commitment", "add", "--kind", "task", "--content", "前工程", "--due", d(5))      # C-002
        self.w.ref_add(self.root, "C-001", "C-002", "後工程", "前工程")
        made = []
        for st in ("done", "open", "done"):
            self.mem("commitment", "set-state", "C-002", st)
            sids = self.w.propose_from_changes(self.root)
            made += sids
            self.w.ref_touch(self.root, "C-001")                                                  # 世代が進む
            for sid in sids:
                self.w.decide(self.root, sid, "reject", "", "確認した")
        self.assertEqual(len([s for s in made if "着手" in next(r["何をするか"] for r in self.w.proposal_rows(self.root)
                                                                  if r["ID"] == s)]), 2)


class BudgetReservationTest(Base):
    """#7 実行中の作業と動いている見直しの上限を予約として数え、1日の上限を超えて起動しない。"""

    def setUp(self):
        super().setUp()
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "別件", "--due", d(10))   # C-002
        self.w.ensure_work(self.root)
        p = self.root / "memory/work/settings.md"
        t = p.read_text(encoding="utf-8")
        import re
        p.write_text(re.sub(r"\| 1日の費用上限（USD） \| [^|]+ \|", "| 1日の費用上限（USD） | 3 |", t), encoding="utf-8")
        self.assertEqual(self.w.limits(self.root)["daily_usd"], 3)

    def test_running_budget_is_reserved(self):
        self.rt.start_run(self.root, "C-001", 2.5)                                                # 費用はまだ記録されていない
        self.assertEqual(self.w.reserved(self.root), 2.5)
        with self.assertRaisesRegex(ValueError, "予約"):
            self.rt.start_run(self.root, "C-002", 2.5)                                            # 2.5＋2.5 は 3 を超える

    def test_dispatch_lock_is_reserved(self):
        sd = self.w.state_dir(self.root)
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "dispatch.lock").write_text(json.dumps({"at": time.time(), "budget": 1.5}), encoding="utf-8")
        self.assertEqual(self.w.reserved(self.root), 1.5)
        with self.assertRaises(ValueError):
            self.rt.start_run(self.root, "C-001", 2.0)                                            # 見直しの中から起動しても超えない
        (sd / "dispatch.lock").write_text(str(time.time()), encoding="utf-8")                     # 以前の形のロック
        self.assertEqual(self.w.reserved(self.root), 1.5)

    def test_reservation_is_released_by_actual_cost(self):
        rid = self.rt.start_run(self.root, "C-001", 2.5)
        self.w.run_end(self.root, rid, "完了", 0.4)
        self.assertEqual((self.w.reserved(self.root), self.w.spent_today(self.root)), (0, 0.4))
        self.rt.start_run(self.root, "C-002", 2.5)                                                # 0.4＋2.5 ≤ 3

    def test_two_launches_at_once_do_not_both_pass(self):
        import threading
        results = []

        def go(cid):
            try:
                results.append(self.rt.start_run(self.root, cid, 2.0))
            except ValueError as e:
                results.append(str(e))
        th = [threading.Thread(target=go, args=(c,)) for c in ("C-001", "C-002")]
        for t in th:
            t.start()
        for t in th:
            t.join()
        self.assertEqual(len(self.w.running_runs(self.root)), 1)                                  # 確かめて記録するまでが一度
        self.assertEqual(len([x for x in results if x.startswith("C-")]), 1)

    def test_same_task_is_not_started_twice(self):
        self.rt.start_run(self.root, "C-001", 0.5)
        with self.assertRaisesRegex(ValueError, "実行中"):
            self.rt.start_run(self.root, "C-001", 0.5)


class BudgetEdgeTest(BudgetReservationTest):
    """#7 の独立レビュー（R-022）：費用が分からない実行・見直し自身の予約・確かめてからロックまでの間。"""

    def test_unknown_cost_counts_as_its_budget(self):
        rid = self.rt.start_run(self.root, "C-001", 2.5)
        self.w.run_end(self.root, rid, "終了不明", None, expect=("実行中",))                    # 落ちた：費用は分からない
        self.assertEqual(self.w.committed_today(self.root), 2.5)                                  # 0 に戻さない
        with self.assertRaises(ValueError):
            self.rt.start_run(self.root, "C-002", 1.0)

    def test_launch_failure_costs_nothing(self):
        from unittest import mock
        self.rt.shutil.which = lambda name: "claude"
        with mock.patch.object(self.rt.subprocess, "Popen", side_effect=OSError("x")):
            with self.assertRaises(ValueError):
                self.rt.launch(self.root, "C-001", 2.5)
        self.assertEqual(self.w.committed_today(self.root), 0)

    def test_pending_counts_the_dispatch_budget(self):
        self.w.ensure_task(self.root, "C-001")                                                    # 起動待ち（上限 2.5）
        self.assertTrue(self.w.pending(self.root)["work"])                                        # 見直しの上限を含めないなら起こす
        self.assertFalse(self.w.pending(self.root, 1.5)["work"])                                  # 1.5＋2.5 > 3：起こさない

    def test_dispatch_rechecks_after_writing_its_lock(self):
        import sys as _sys
        import types
        ds = self.load("tools/schedule/dispatch.py", "dispatch_scn")
        w, rt, root = self.w, self.rt, self.root
        self.w.ensure_task(self.root, "C-001")
        ds._load_work = lambda r, name="work": rt if name == "run_task" else w
        w.tick = lambda r: (rt.start_run(root, "C-001", 2.5), True)[1]                            # 確かめた後にボードから起動
        w.pending = lambda r, b=0: {"work": True, "observe": False, "undecided": ["S-001"], "unjudged": [], "waiting": []}
        ran = []
        ds.subprocess = types.SimpleNamespace(run=lambda *a, **k: ran.append(1), PIPE=-1, DEVNULL=-3)
        _sys.argv = ["dispatch.py", "--root", str(root)]
        ds.main()
        self.assertEqual(ran, [])                                                                 # 予約込みで超えるので起動しない
        self.assertFalse((w.state_dir(root) / "dispatch.lock").exists())                          # ロックは残さない


class AdoptOnceTest(Base):
    """#9 同じ提案を二度採用しても、タスクは1件。判定済みの提案は上書きしない。"""

    def setUp(self):
        super().setUp()
        self.sid = self.w.propose(self.root, "競合の料金を調べる", "定例に要る", "カレンダー", "当日慌てない", "@pm")

    def test_board_adopt_twice(self):
        c1, r1 = self.srv.do_post(self.root, "/api/proposal/adopt", {"sid": self.sid, "who": "@pm"})
        c2, r2 = self.srv.do_post(self.root, "/api/proposal/adopt", {"sid": self.sid, "who": "@pm"})
        self.assertEqual((c1, c2), (200, 200))
        self.assertEqual(r1["id"], r2["id"])
        self.assertIn("採用済み", r2["message"])
        self.assertEqual(len([r for r in self.w.mem.commitment_rows(self.root) if "競合の料金" in (r.get("内容") or "")]), 1)

    def test_concurrent_adopt(self):
        import threading
        out = []
        th = [threading.Thread(target=lambda: out.append(self.w.adopt(self.root, self.sid, "@pm"))) for _ in range(3)]
        for t in th:
            t.start()
        for t in th:
            t.join()
        self.assertEqual(len({c for c, _ in out}), 1)
        self.assertEqual(sum(1 for _, made in out if made), 1)

    def test_decided_proposal_is_not_overwritten(self):
        self.w.decide(self.root, self.sid, "reject", "", "不要")
        self.w.decide(self.root, self.sid, "reject", "", "不要")                                  # 同じ判定の二度目は何もしない
        with self.assertRaises(self.w.MemError):
            self.w.decide(self.root, self.sid, "adopt", "C-001", "やはり要る")
        self.assertEqual(self.srv.do_post(self.root, "/api/proposal/adopt", {"sid": self.sid, "who": "@pm"})[0], 400)
        s2 = self.w.propose(self.root, "別の調べもの", "x", "y", "z", "@pm")
        self.w.decide(self.root, s2, "ask", "", "人に聞く")
        self.w.decide(self.root, s2, "adopt", "C-001", "人が承認")                                # 要承認からは決められる
        s3 = self.w.propose(self.root, "三つめ", "x", "y", "z", "@pm")
        self.w.decide(self.root, s3, "hold", "", "後で")
        self.w.decide(self.root, s3, "reject", "", "やはり不要")                                  # 保留からも決め直せる
        code, res = self.srv.do_post(self.root, "/api/proposal/decide", {"sid": s3, "decision": "hold", "reason": "x"})
        self.assertEqual(code, 400, res)                                                          # 判定済みの上書きは 500 でなく 400

    def test_cli_adopt_is_idempotent(self):
        out = [self.run_py("tools/work.py", "adopt", self.sid, "--who", "@pm", "--reason", "要る") for _ in range(2)]
        self.assertEqual([o.returncode for o in out], [0, 0], out[0].stderr + out[1].stderr)
        self.assertIn("採用済み", out[1].stdout)
        last = [o.stdout.strip().splitlines()[-1].split("（")[0] for o in out]
        self.assertEqual(last[0], last[1])


class PrerequisiteTest(Base):
    """#10 前工程が done でない・待ちが届いていないタスクは、起動の入口で止める（人が判断したときだけ --anyway）。"""

    def setUp(self):
        super().setUp()
        self.mem("commitment", "add", "--kind", "task", "--content", "前工程", "--due", d(5))      # C-002
        self.w.ref_add(self.root, "C-001", "C-002", "後工程", "前工程")
        self.rt.shutil.which = lambda name: "claude"

    def test_predecessor_must_be_done(self):
        with self.assertRaisesRegex(ValueError, "着手条件"):
            self.rt.check_task(self.root, "C-001")
        code, res = self.srv.do_post(self.root, "/api/task/run", {"id": "C-001"})
        self.assertEqual(code, 400)
        self.assertIn("前工程 C-002", res["error"])
        self.rt.check_task(self.root, "C-001", anyway=True)                                       # 人が判断したら通せる
        self.mem("commitment", "set-state", "C-002", "done")
        self.rt.check_task(self.root, "C-001")

    def test_wait_must_have_arrived(self):
        self.mem("commitment", "set-state", "C-002", "done")
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "待ち", "見積")
        with self.assertRaisesRegex(ValueError, "まだ届いていない"):
            self.rt.check_task(self.root, "C-001")
        self.write("memory/projects/x/status.md", STATUS_V2)
        sid = next(s for s in self.w.propose_from_changes(self.root)
                   if "再開" in next(r["何をするか"] for r in self.w.proposal_rows(self.root) if r["ID"] == s))
        self.w.decide(self.root, sid, "hold", "", "人に確かめる")                                  # 保留しても
        self.w.decide(self.root, sid, "merge", "C-001", "届いた")                                  # あとで決め直せる（手順は merge）
        self.rt.check_task(self.root, "C-001")

    def test_anyway_is_for_people_and_dropped_predecessor_is_explained(self):
        self.mem("commitment", "set-state", "C-002", "dropped")
        with self.assertRaisesRegex(ValueError, "取り下げられた"):
            self.rt.check_task(self.root, "C-001")
        os.environ["AI_AGENCY_UNATTENDED"] = "1"
        try:
            with self.assertRaisesRegex(ValueError, "人の判断"):
                self.rt.check_task(self.root, "C-001", anyway=True)                               # 無人では外せない
        finally:
            del os.environ["AI_AGENCY_UNATTENDED"]
        self.rt.check_task(self.root, "C-001", anyway=True)

    def test_old_style_key_is_the_same_event(self):
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "前提", "見積")
        self.write("memory/projects/x/status.md", STATUS_V2)
        c = next(x for x in self.w.changes(self.root) if x["place"].endswith("#見積"))
        self.w.propose(self.root, "前の版で作った提案", "x", "y", "z", "@chief",
                       f"C-001|{c['place']}|{c['old']}→{c['new']}#0")                             # bff2cc1 の形の識別
        self.w.decide(self.root, next(r["ID"] for r in self.w.proposal_rows(self.root)), "hold", "", "後で")
        self.assertEqual(self.w.propose_from_changes(self.root), [])


class BoardShowsWhatHappenedTest(Base):
    """R-023 の実地：終了不明の実行がカードに出ない・報告ダイアログで条件の変化が見えない・下限 0.5 では起動だけで打ち切られた。"""

    def card(self, cid="C-001"):
        return next(r for r in self.srv.api_commitments(self.root)["rows"] if r["ID"] == cid)

    def test_unknown_run_is_shown_on_the_card(self):
        rid = self.w.run_start(self.root, "C-001")
        self.rt.state_path(self.root, rid).parent.mkdir(parents=True, exist_ok=True)
        self.rt.state_path(self.root, rid).write_text(json.dumps({"start": time.time() - 60, "pid": _dead_pid()}), encoding="utf-8")
        c = self.card()                                                                            # 表示の時点で後始末される
        self.assertEqual((c["agent_state"], c["task_run"]["state"]), ("unknown", "unknown"))
        self.assertEqual(self.w.run_row(self.root, rid)["結果"], "終了不明")

    def test_stopped_run_is_shown_and_a_report_still_wins(self):
        rid = self.w.run_start(self.root, "C-001")
        self.w.run_end(self.root, rid, "停止", None)
        self.assertEqual(self.card()["agent_state"], "stopped")
        rid = self.w.run_start(self.root, "C-001")
        self.w.run_end(self.root, rid, "終了不明", None)
        self.report(rid)                                                                           # 報告を書いたあとに落ちた
        self.assertEqual(self.card()["agent_state"], "review")                                     # 報告は読める（確認待ち）
        self.mem("commitment", "set-state", "C-001", "dropped")
        self.assertEqual(self.card()["agent_state"], "")                                           # 閉じた約束には出さない

    def test_report_dialog_gets_the_stale_reason_before_approve(self):
        self.w.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "前提", "見積")
        rid = self.finished_run()
        self.assertEqual(self.srv.api_task_result(self.root, "C-001")["status"]["stale"], [])
        self.write("memory/projects/x/status.md", STATUS_V2)
        res = self.srv.api_task_result(self.root, "C-001")
        self.assertEqual((res["run"], res["status"]["stale"]), (rid, ["memory/projects/x/status.md#見積"]))

    def test_budget_floor_and_small_settings(self):
        for b in (0.5, 0.99):
            with self.assertRaisesRegex(ValueError, "1〜20"):
                self.rt.check_budget(b)
        self.rt.check_budget(1.0)
        self.w.ensure_work(self.root)
        sp = self.w.work_dir(self.root) / "settings.md"
        t = sp.read_text(encoding="utf-8").replace("| 1回の上限（USD・レビューなし） | 1 |", "| 1回の上限（USD・レビューなし） | 0.5 |")
        sp.write_text(t, encoding="utf-8")
        # 設定が下限より小さくても下限で起動する。そのままだと毎回断られ、見直しが起動できない仕事のために起き続ける
        self.assertEqual(self.w.limits(self.root)["run_usd"], 1.0)
        self.assertEqual(self.rt.default_budget(self.root, "none"), 1.0)
        self.rt.check_budget(self.rt.default_budget(self.root, "none"))
        os.environ["MAX_BUDGET_USD"] = "0.5"                                                       # 朝のブリーフと共有の上書き
        try:
            self.assertEqual((self.rt.default_budget(self.root, "none"), self.w.task_budget(self.root, "C-001")), (1.0, 1.0))
        finally:
            del os.environ["MAX_BUDGET_USD"]


def _set_setting(t, label, value):
    import re
    t.w.ensure_work(t.root)
    sp = t.w.work_dir(t.root) / "settings.md"
    txt = re.sub(r"^\| " + re.escape(label) + r" \| [^|]* \|", f"| {label} | {value} |", sp.read_text(encoding="utf-8"), flags=re.M)
    sp.write_text(txt, encoding="utf-8")


class BudgetRangeAgreesTest(Base):
    """R-024 の独立レビュー：設定・環境変数の1回の上限が範囲（1〜20）の外でも、起動待ちの見込みと起動の判定が一致する。
    食い違うと、起動は毎回断られるのに定期の見直しが毎回起きる。"""

    def test_setting_above_20(self):
        _set_setting(self, "1日の費用上限（USD）", "100")
        _set_setting(self, "1回の上限（USD・レビューあり）", "30")
        self.w.ensure_task(self.root, "C-001")
        self.assertEqual([x["budget"] for x in self.w.launch_waiting(self.root)], [20.0])
        self.assertEqual(self.rt.default_budget(self.root, "auto"), 20.0)
        self.rt.check_budget(self.rt.default_budget(self.root, "auto"))

    def test_env_above_20_or_not_a_number(self):
        for v, want in (("25", 20.0), ("0.5", 1.0), ("nan", 2.5), ("inf", 2.5), ("abc", 2.5)):
            os.environ["MAX_BUDGET_USD"] = v
            try:
                b = self.rt.default_budget(self.root, "auto")
                self.assertEqual((v, b, self.w.task_budget(self.root, "C-001")), (v, want, want))
                self.rt.check_budget(b)
            finally:
                del os.environ["MAX_BUDGET_USD"]


class SubagentLogDoesNotHideRunStateTest(Base):
    """R-024 の独立レビュー：作業セッションの中のサブエージェント（無人の記録）が、止めた・落ちた実行を「確認待ち」「稼働中」に見せない。"""

    def log(self, *events):
        p = self.root / "memory/task-log/agent-runs.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def card(self, cid="C-001"):
        return next(r for r in self.srv.api_commitments(self.root)["rows"] if r["ID"] == cid)

    def start(self, tuid, ago, **kw):
        return {"ts": time.time() - ago, "sid": "s1", "ev": "start", "tuid": tuid, "agent": "qa", "desc": "C-001 のレビュー",
                "tasks": ["C-001"], **kw}

    def test_stopped_after_reviewer_finished(self):
        rid = self.w.run_start(self.root, "C-001")
        self.log(self.start("t1", 120, unattended=True), {"ts": time.time() - 60, "sid": "s1", "ev": "end", "tuid": "t1", "unattended": True})
        self.w.run_end(self.root, rid, "停止", None)
        self.assertEqual(self.card()["agent_state"], "stopped")

    def test_stopped_while_reviewer_running(self):
        rid = self.w.run_start(self.root, "C-001")
        self.log(self.start("t1", 30, unattended=True))                                           # 終了の記録はもう来ない
        self.w.run_end(self.root, rid, "停止", None)
        self.assertEqual(self.card()["agent_state"], "stopped")

    def test_attended_delegation_after_a_run_still_shows(self):
        rid = self.w.run_start(self.root, "C-001")
        self.w.run_end(self.root, rid, "停止", None)
        self.log(self.start("t2", 10))                                                             # 人の会話から改めて任せた
        self.assertEqual(self.card()["agent_state"], "running")


class NoApproveWhileRunningTest(Base):
    """R-024 の独立レビュー：報告ファイルが書かれていても、実行中は完了にしない（どの経路でも）。"""

    def test_approve_state_button_and_cli_refuse_running(self):
        rid = self.w.run_start(self.root, "C-001")
        self.rt._state_update(self.root, rid, start=round(time.time(), 1), pid=os.getpid(), budget=1.0)   # 生きている
        self.report(rid, 0)
        code, body = self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})
        self.assertEqual(code, 400, body)
        self.assertIn("実行中", body["error"])
        code, body = self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-001", "state": "done"})
        self.assertEqual(code, 400, body)
        self.assertNotIn("やり直させる", body["error"])
        with self.assertRaises(self.w.mem.MemError):
            self.w.mem.commitment_set_state_checked(self.root, "C-001", "done")
        self.assertEqual(self.state(), "open")
        self.w.run_end(self.root, rid, "完了", 0.5)                                                # 終わったら承認できる
        self.assertEqual(self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 200)


class OrderLimitIsTheBudgetTest(Base):
    """R-027：作業依頼の「上限」欄・作業セッションが読む上限・実際の --max-budget-usd・起動待ちの見込みを一つに。"""

    def limit(self, cid="C-001"):
        return self.order(cid).get("上限", "")

    def test_order_limit_is_the_default_and_the_estimate(self):
        self.w.order_set(self.root, "C-001", {"目的": "費用比較", "上限": "2.5 USD・30分", "レビュー": "none"})
        self.assertEqual(self.w.task_budget(self.root, "C-001"), 2.5)                            # レビューなしでも依頼の指定が勝つ
        self.assertEqual(self.rt.default_budget(self.root, "none", "C-001"), 2.5)
        self.assertEqual([x["budget"] for x in self.w.launch_waiting(self.root)], [2.5])
        self.w.order_set(self.root, "C-001", {"上限": "50 USD"})
        self.assertEqual(self.w.task_budget(self.root, "C-001"), 20.0)                           # 範囲に揃える
        self.w.order_set(self.root, "C-001", {"上限": "30分"})
        self.assertEqual(self.w.task_budget(self.root, "C-001"), 1.0)                            # USD が無ければ設定（none）

    def test_one_off_budget_does_not_become_the_default(self):
        self.w.order_set(self.root, "C-001", {"目的": "費用比較", "上限": "2.5 USD・30分"})
        self.rt.start_run(self.root, "C-001", 8.0)                                               # ボードで今回だけ 8
        self.assertEqual(self.limit(), "2.5 USD・30分")                                          # 作業依頼（割り振り役の指定）は変えない
        self.assertEqual(self.w.task_budget(self.root, "C-001"), 2.5)                            # 次の無人の起動・見込みは 2.5

    def test_review_change_follows_settings_when_no_limit(self):
        self.w.order_set(self.root, "C-001", {"目的": "費用比較", "レビュー": "none"})
        rid = self.rt.start_run(self.root, "C-001", 1.0)
        self.w.run_end(self.root, rid, "完了", 0.6)
        self.w.order_set(self.root, "C-001", {"レビュー": "qa（部長に出すため）"})
        self.assertEqual(self.w.task_budget(self.root, "C-001"), 2.5)                            # 3人分の既定に戻る

    def test_how_limits_are_written(self):
        for v, want in (("$3", 3.0), ("USD 3", 3.0), ("3ドル", 3.0), ("5", 5.0), ("2.5 USD・30分", 2.5),
                        ("30分", None), ("既定（work/settings.md）", None), ("", None)):
            self.w.order_set(self.root, "C-001", {"上限": v or "なし"})
            self.assertEqual((v, self.w.order_budget(self.root, "C-001")), (v, want))

    def test_the_run_is_told_its_budget(self):
        self.rt.shutil.which = lambda name: "claude"
        self.assertIn("--budget 8", self.rt.build_command("C-001", "C-001-R1", 8.0, "none")[2])

    def test_env_still_wins_and_board_gets_the_numbers(self):
        self.w.order_set(self.root, "C-001", {"目的": "費用比較", "上限": "3 USD"})
        b = next(r for r in self.srv.api_commitments(self.root)["rows"] if r["ID"] == "C-001")["budget"]
        self.assertEqual(b, {"order": 3.0, "env": None, "none": 1.0, "review": 2.5})
        os.environ["MAX_BUDGET_USD"] = "2"
        try:
            self.assertEqual(self.w.task_budget(self.root, "C-001"), 2.0)
            self.assertEqual(self.rt.default_budget(self.root, "auto", "C-001"), 2.0)
        finally:
            del os.environ["MAX_BUDGET_USD"]


class AgentEventOrderTest(Base):
    """外部レビュー #11：別々のフックが書く記録（起動・起動の応答・終了）が逆順・重複・欠落で届いても、実行を正しく結ぶ。"""

    def runs(self, rows):
        self.write("memory/task-log/agent-runs.jsonl", "".join(json.dumps(r) + chr(10) for r in rows))
        return {r["tuid"]: r for r in self.srv.agent_runs(self.root)}

    def ev(self, t, ev, **kw):
        return {"ts": self.t0 + t, "sid": "s", "ev": ev, **kw}

    def setUp(self):
        super().setUp()
        self.t0 = time.time() - 60

    def start(self, t, tuid, agent="generalist"):
        return self.ev(t, "start", tuid=tuid, agent=agent, bg=True)

    def test_normal_and_reversed_order(self):
        for rows in ([self.start(0, "t1"), self.ev(0.3, "launched", tuid="t1", aid="a1"), self.ev(10, "stop", agent="generalist", aid="a1")],
                     [self.start(0, "t1"), self.ev(0.2, "stop", agent="generalist", aid="a1"), self.ev(0.3, "launched", tuid="t1", aid="a1")]):
            r = self.runs(rows)["t1"]
            self.assertEqual(r["state"], "done")
            self.assertEqual(r["end"], rows[2 if rows[2]["ev"] == "stop" else 1]["ts"])          # 終わった時刻は終了の記録
        self.assertEqual(self.srv.api_activity(self.root)["running"], [])

    def test_two_of_the_same_kind_both_stops_first(self):
        r = self.runs([self.start(0, "t1"), self.start(1, "t2"),
                       self.ev(2, "stop", agent="generalist", aid="a2"), self.ev(3, "stop", agent="generalist", aid="a1"),
                       self.ev(4, "launched", tuid="t2", aid="a2"), self.ev(5, "launched", tuid="t1", aid="a1")])
        self.assertEqual((r["t1"]["end"], r["t2"]["end"]), (self.t0 + 3, self.t0 + 2))

    def test_duplicates_do_not_close_or_reopen_others(self):
        r = self.runs([self.start(0, "old"), self.ev(0.1, "launched", tuid="old"),                 # 応答に agentId の無かった起動
                       self.start(1, "t1"), self.ev(1.1, "launched", tuid="t1", aid="a1"),
                       self.ev(5, "stop", agent="generalist", aid="a1"), self.ev(5.1, "stop", agent="generalist", aid="a1"),
                       self.ev(6, "launched", tuid="t1", aid="a1")])
        self.assertEqual((r["t1"]["state"], r["t1"]["end"]), ("done", self.t0 + 5))
        self.assertEqual(r["old"]["state"], "background")                                           # 重複した終了で別の実行を閉じない

    def test_early_stop_does_not_close_an_older_legacy_run(self):
        r = self.runs([self.start(0, "old"), self.ev(0.1, "launched", tuid="old"),
                       self.start(1, "t1"), self.ev(2, "stop", agent="generalist", aid="a1"),
                       self.ev(2.1, "launched", tuid="t1", aid="a1")])
        self.assertEqual((r["t1"]["state"], r["old"]["state"]), ("done", "background"))

    def fg(self, t, tuid, aid=None):
        """前景の呼び出し：フックは start → stop → end の順に書く（end には aid。古い記録には無い）。"""
        end = self.ev(t + 0.3, "end", tuid=tuid, **({"aid": aid} if aid else {}))
        return [self.ev(t, "start", tuid=tuid, agent="generalist", bg=False),
                self.ev(t + 0.1, "stop", agent="generalist", aid=aid or "x" + tuid), end]

    def legacy_bg(self, t, tuid):
        return [self.start(t, tuid), self.ev(t + 0.1, "launched", tuid=tuid)]                     # 応答に agentId が無かった

    def test_foreground_stop_never_closes_a_background_run(self):
        for rows in (self.fg(0, "f1", "f1a") + self.legacy_bg(5, "L"),                             # 前景のあとに起動
                     self.legacy_bg(0, "L") + self.fg(5, "f1", "f1a"),                             # 前景の前に起動
                     self.legacy_bg(0, "L") + self.fg(5, "f1")):                                   # aid の無い古い end
            r = self.runs(rows)
            self.assertEqual((r["f1"]["state"], r["L"]["state"]), ("done", "background"), [x["ev"] for x in rows])

    def test_stop_after_a_failed_launch_belongs_to_it(self):
        r = self.runs(self.legacy_bg(0, "L") + [self.start(1, "t1"), self.ev(1.2, "fail", tuid="t1"),
                                                self.ev(1.5, "stop", agent="generalist", aid="a1")])
        self.assertEqual((r["t1"]["state"], r["L"]["state"]), ("failed", "background"))

    def test_fresh_stop_waits_for_its_launched(self):
        self.t0 = time.time() - 2                                                                  # 届いたばかり
        r = self.runs(self.legacy_bg(0, "L") + [self.start(1, "t1"), self.ev(1.2, "stop", agent="generalist", aid="a1")])
        self.assertEqual((r["L"]["state"], r["t1"]["state"]), ("background", "running"))           # 応答を待つ（L を閉じない）

    def test_missing_launched_is_closed_by_its_stop(self):
        r = self.runs(self.legacy_bg(0, "L") + [self.start(1, "t1"), self.ev(20, "stop", agent="generalist", aid="a1")])
        self.assertEqual((r["t1"]["state"], r["L"]["state"]), ("done", "background"))              # 応答の欠けた起動のほうを閉じる

    def test_legacy_stop_still_closes_by_approximation(self):
        r = self.runs([self.start(0, "old"), self.ev(0.1, "launched", tuid="old"),
                       self.ev(3, "stop", agent="generalist", aid="zz")])                          # aid の合う起動が最後まで来ない
        self.assertEqual(r["old"]["state"], "done")
        r = self.runs([self.start(0, "old"), self.ev(0.1, "launched", tuid="old"), self.ev(3, "stop", agent="generalist")])
        self.assertEqual(r["old"]["state"], "done")


class NeedsTest(Base):
    """外部レビュー #12（設計 v2）：足りないものを約束にして仕事を止め、そろったら再開する。順序を入れ替えて確かめる。"""

    def setUp(self):
        super().setUp()
        self.write(".claude/agents/researcher.md", "---\nname: researcher\n---\n")
        self.w.order_set(self.root, "C-001", {"目的": "費用比較"})

    def need(self, cid="C-001", content="B社の月額", who="@researcher", **kw):
        return self.w.need(self.root, cid, who, content, kw.get("due", d(3)), kw.get("why", "比較の2行目"), kw.get("check", ""),
                           kw.get("link", ""))

    def done(self, cid, state="done"):
        self.w.mem.commitment_set_state(self.root, cid, state)

    def card(self, cid="C-001"):
        return next(r for r in self.srv.api_commitments(self.root)["rows"] if r["ID"] == cid)

    def test_need_blocks_start_and_done_then_resumes(self):
        nid, made = self.need()
        self.assertTrue(made)
        self.assertEqual(self.w.read_task(self.root, nid)["order"]["レビュー"], "none（不足の収集）")   # 集める仕事の作業依頼
        self.assertTrue(self.w.unmet_conditions(self.root, "C-001"))
        with self.assertRaisesRegex(self.w.mem.MemError, "着手条件"):
            self.w.start_run_within_limits(self.root, "C-001", 1.0)
        self.assertIn("不足", self.w.done_blocker(self.root, "C-001"))
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-001", "state": "done"})[0], 400)
        self.assertEqual([x["task"] for x in self.w.launch_waiting(self.root)], [nid])          # 集める仕事は起動待ち（元は出ない）
        self.assertEqual((self.card()["agent_state"], self.card(nid)["blocks"]), ("needs", ["C-001"]))
        self.done(nid)
        self.assertEqual(self.w.unmet_conditions(self.root, "C-001"), [])
        self.assertEqual([c["kind"] for c in self.w.changes(self.root) if c["task"] == "C-001"], ["再開"])
        self.w.ref_touch(self.root, "C-001", nid)                                                 # 提案を処理した
        self.assertIn("C-001", [x["task"] for x in self.w.launch_waiting(self.root)])           # 一度も起動していないので起動待ち

    def test_partial_report_waits_and_resumes_after_touch(self):
        nid, _ = self.need()
        rid = self.w.start_run_within_limits(self.root, "C-001", 1.0, anyway=True)               # 人が判断して起動した
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.report(rid)
        self.assertEqual(self.w.accept(self.root, rid, "wait", "B社が無い"), "条件待ち")
        self.assertEqual(self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 400)
        self.assertNotIn("C-001", [x["task"] for x in self.w.launch_waiting(self.root)])        # そろうまで出ない
        self.done(nid)
        self.w.ref_touch(self.root, "C-001")                                                      # 場所なし：不足は進めない
        self.assertEqual([c["kind"] for c in self.w.changes(self.root) if c["task"] == "C-001"], ["再開"])
        self.w.ref_touch(self.root, "C-001", nid)
        self.assertIn("C-001", [x["task"] for x in self.w.launch_waiting(self.root)])           # 条件待ちのあと、そろった
        self.assertIn("条件待ち", self.w.done_blocker(self.root, "C-001"))                       # 部分的な成果は完了にしない

    def test_accepted_run_made_without_the_need_resumes(self):
        nid, _ = self.need()
        rid = self.w.start_run_within_limits(self.root, "C-001", 1.0, anyway=True)
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.report(rid)
        self.w.accept(self.root, rid, "ask", "人へ")
        self.done(nid)
        self.w.ref_touch(self.root, "C-001", nid)
        self.assertIn("のあとに不足がそろった", next(x["why"] for x in self.w.launch_waiting(self.root) if x["task"] == "C-001"))
        self.assertIn(f"不足 {nid}", "".join(self.w.stale_refs(self.root, rid)))                 # 不足なしで作った成果は古い

    def test_shared_need_and_double_click_and_limit(self):
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "別件", "--due", d(10))   # C-002
        nid, _ = self.need()
        self.assertEqual(self.need(content="B社の月額。"), (nid, False))                          # 二度押し（表記ゆれ）
        self.assertEqual(self.need("C-002", link=nid), (nid, False))
        self.assertEqual(self.w.needs_map(self.root)[nid], ["C-001", "C-002"])
        self.done(nid)
        self.assertEqual({c["task"] for c in self.w.changes(self.root) if c["kind"] == "再開"}, {"C-001", "C-002"})
        for i in range(3):
            self.need(content=f"別の不足{i}")
        with self.assertRaisesRegex(self.w.mem.MemError, "上限"):
            self.need(content="4つ目")

    def test_gathering_task_cannot_chain(self):
        nid, _ = self.need()
        with self.assertRaisesRegex(self.w.mem.MemError, "新しい不足は作らない"):
            self.need(nid, content="B社の営業の連絡先")

    def test_intermediate_states_do_not_propose(self):
        nid, _ = self.need()
        self.done(nid, "in-progress")                                                             # 担当が作業中
        self.assertEqual([c for c in self.w.changes(self.root) if c["task"] == "C-001"], [])
        self.assertFalse(self.w.pending(self.root)["undecided"])

    def test_dropped_need_lets_it_go_and_reopen_makes_results_stale(self):
        nid, _ = self.need()
        self.done(nid, "dropped")                                                                 # 無しで進めると決めた
        self.assertEqual(self.w.unmet_conditions(self.root, "C-001"), [])
        self.w.ref_touch(self.root, "C-001", nid)                                                 # 再開の提案を処理した
        rid = self.w.start_run_within_limits(self.root, "C-001", 1.0)
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.report(rid)
        self.assertEqual(self.w.stale_refs(self.root, rid), [])
        self.done(nid, "open")                                                                    # やはり要る（再オープン）
        self.assertIn(f"不足 {nid}", "".join(self.w.stale_refs(self.root, rid)))
        self.assertIn("見直し", [c["kind"] for c in self.w.changes(self.root) if c["task"] == "C-001"])

    def test_need_added_between_check_and_start_is_refused(self):
        self.rt.check_task(self.root, "C-001")                                                    # 起動前の検査は通った
        self.need()                                                                               # その間に不足が足された
        with self.assertRaisesRegex(ValueError, "着手条件"):
            self.rt.start_run(self.root, "C-001", 1.0)

    def test_wait_verdict_needs_an_open_need_and_is_not_a_return(self):
        rid = self.finished_run()
        with self.assertRaisesRegex(self.w.mem.MemError, "不足が無い"):
            self.w.accept(self.root, rid, "wait")
        self.need()
        self.w.accept(self.root, rid, "wait")
        for _ in range(2):
            r = self.w.run_start(self.root, "C-001")
            self.w.run_end(self.root, r, "完了", 0.5)
            self.w.accept(self.root, r, "return", "直す")
        r = self.w.run_start(self.root, "C-001")
        self.w.run_end(self.root, r, "完了", 0.5)
        self.assertEqual(self.w.accept(self.root, r, "return", "直す"), "人へ確認")               # 差し戻しの2回に条件待ちは数えない

    def test_self_and_person_needs(self):
        nid, _ = self.need(who="自分", content="予算の上限を決める")
        self.assertEqual(next(r for r in self.w.mem.commitment_rows(self.root) if r["ID"] == nid)["kind"], "task")
        with self.assertRaisesRegex(self.w.mem.MemError, "--due"):
            self.w.need(self.root, "C-001", "佐藤", "見積の再送", None)
        pid, _ = self.need(who="佐藤", content="見積の再送")
        row = next(r for r in self.w.mem.commitment_rows(self.root) if r["ID"] == pid)
        self.assertEqual((row["kind"], row["フォロー日"]), ("given", d(3)))
        self.assertFalse(self.w.task_path(self.root, pid).exists())                               # 人への依頼は作業依頼を作らない

    # ---- 独立レビュー（段階1の実装）の反例
    def test_need_added_after_the_report_still_counts(self):
        rid = self.finished_run()                                                                 # 報告 → 確認役が不足を見つける（普通の順）
        nid, _ = self.need()
        with self.assertRaisesRegex(self.w.mem.MemError, "開いている不足"):
            self.w.accept(self.root, rid, "accept")                                               # 受け入れにはできない
        self.w.accept(self.root, rid, "wait")
        self.done(nid)
        with self.assertRaisesRegex(self.w.mem.MemError, "条件待ち"):
            self.w.accept(self.root, rid, "accept")                                               # 条件待ちを受け入れに書き換えない
        self.assertIn(f"不足 {nid}", "".join(self.w.stale_refs(self.root, rid)))                 # そろう前の成果は古い
        self.w.ref_touch(self.root, "C-001", nid)
        self.assertIn("C-001", [x["task"] for x in self.w.launch_waiting(self.root)])
        self.assertEqual(self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 400)

    def test_dropped_need_does_not_make_the_partial_result_stale(self):
        rid = self.finished_run()
        nid, _ = self.need()
        self.done(nid, "dropped")                                                                 # 無しで進めると決めた
        self.assertEqual(self.w.stale_refs(self.root, rid), [])

    def test_links_cannot_make_cycles_or_chains(self):
        nid, _ = self.need()
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "別件", "--due", d(10))
        other = next(r["ID"] for r in self.w.mem.commitment_rows(self.root) if r.get("内容") == "別件")
        self.need(other, content="別件の不足")
        with self.assertRaisesRegex(self.w.mem.MemError, "不足を持つ仕事"):
            self.need("C-001", link=other)                                                        # 不足を持つ仕事を不足にしない（循環）
        with self.assertRaisesRegex(self.w.mem.MemError, "集める仕事"):
            self.need(nid, link=other)                                                            # 集める仕事に link で付けない

    def test_unattended_ref_add_cannot_bypass_need(self):
        nid, _ = self.need()
        os.environ["AI_AGENCY_UNATTENDED"] = "1"
        try:
            with self.assertRaises(SystemExit):
                self.w.main(["--root", str(self.root), "ref", "add", nid, "C-001", "--use", "不足"])
        finally:
            del os.environ["AI_AGENCY_UNATTENDED"]
        self.assertNotIn(nid, self.w.unmet_conditions(self.root, nid))

    def test_need_recovers_after_a_crash(self):
        real = self.w.ref_add
        self.w.ref_add = lambda *a, **k: (_ for _ in ()).throw(OSError("落ちた"))
        try:
            with self.assertRaises(OSError):
                self.need()                                                                       # 約束と作業依頼のあと、参照の前で落ちた
        finally:
            self.w.ref_add = real
        nid, made = self.need()
        self.assertTrue(made)
        self.assertEqual(sum(1 for r in self.w.mem.commitment_rows(self.root) if "C-001 の不足" in (r.get("内容") or "")), 1)
        self.assertTrue(self.w.task_path(self.root, nid).exists())

    def test_need_text_normalization(self):
        a, _ = self.need(content="A|B 社の月額")
        self.assertEqual(self.need(content="A|B 社の月額")[0], a)                                 # | を含んでも二度押しは1件
        self.assertEqual(self.w.open_needs(self.root, "C-001")[0]["content"], "A／B 社の月額")
        b, _ = self.need(content="単価3.5万円の確認")
        c, made = self.need(content="単価35万円の確認")
        self.assertTrue(made and b != c)                                                          # 数字の小数点は区別する

    def test_dropping_the_blocked_task_stops_the_gathering(self):
        nid, _ = self.need()
        self.done("C-001", "dropped")
        self.assertNotIn(nid, [x["task"] for x in self.w.launch_waiting(self.root)])            # 待つ仕事が無いので起こさない
        self.assertEqual(self.card(nid)["blocks"], [])

    def test_only_delegated_work_can_be_blocked(self):
        self.mem("commitment", "add", "--kind", "task", "--content", "自分の作業", "--due", d(2))
        mine = next(r["ID"] for r in self.w.mem.commitment_rows(self.root) if r.get("タスク") == "自分の作業")
        with self.assertRaisesRegex(self.w.mem.MemError, "エージェントに任せた仕事ではない"):
            self.need(mine)


class CompleteUnderOneLockTest(Base):
    """承認・状態ボタンで完了にするとき、確かめてから done を書くまでに別の書き込み（不足の追加・起動・判定）が
    割り込めない（R-020 から残っていたロックの幅）。割り込む側を別スレッド（別の写しのモジュール＝別プロセスと同じく
    ファイルのロックだけで待つ）で、確かめた直後に走らせる。"""

    def interleave(self, during, target="open_needs", nth=1):
        """ボード側の検査関数 target が nth 回目に呼ばれた瞬間（承認の検査の途中）に、別スレッドで during() を走らせる。"""
        box, real, calls = {}, getattr(self.srv.work, target), []

        def run():
            try:
                box["result"] = during()
            except Exception as e:   # 別の写しの MemError も受ける
                box["error"] = str(e)

        def hooked(*a, **kw):
            calls.append(1)
            if len(calls) == nth:
                box["thread"] = threading.Thread(target=run)
                box["thread"].start()
                box["thread"].join(0.5)   # ロックが無ければこの間に書き終わる
            return real(*a, **kw)

        setattr(self.srv.work, target, hooked)
        return box

    def approved_ready(self):
        rid = self.finished_run()
        self.w.accept(self.root, rid, "accept", "OK")
        return rid

    def test_need_added_during_approve_waits_and_is_refused(self):
        self.approved_ready()
        # 最後の検査（古い条件か：read_status で1回、承認の検査で2回目）を通ったあと、done を書く前に不足が足される並び
        box = self.interleave(lambda: self.w.need(self.root, "C-001", "自分", "予算の上限", d(3), "比較", "", ""),
                              target="stale_refs", nth=2)
        code, res = self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})
        box["thread"].join(30)
        state = next(r["状態"] for r in self.w.mem.commitment_rows(self.root) if r["ID"] == "C-001")
        opened = self.w.open_needs(self.root, "C-001")
        self.assertEqual((code, state), (200, "done"), res)
        self.assertEqual(opened, [])                          # 完了したタスクに開いた不足が残らない
        self.assertIn("既に done", box.get("error", ""))       # 割り込んだ側は、完了を見てから断られる

    def test_launch_during_approve_is_refused_after_done(self):
        self.approved_ready()
        self.rt.check_task(self.root, "C-001")               # 起動前の検査（ロックの外）は通った
        box = self.interleave(lambda: self.w.start_run_within_limits(self.root, "C-001", 1.0))
        code, res = self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})
        box["thread"].join(30)
        self.assertEqual(code, 200, res)
        self.assertIn("既に done", box.get("error", ""))
        self.assertEqual([r["結果"] for r in self.w.read_task(self.root, "C-001")["runs"]], ["完了"])   # 完了したタスクに実行が足されない

    def test_check_then_close_then_record_does_not_start(self):
        self.rt.check_task(self.root, "C-001")               # 起動の検査のあと
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-001", "state": "done"})[0], 200)
        with self.assertRaisesRegex(self.w.mem.MemError, "既に done"):
            self.w.start_run_within_limits(self.root, "C-001", 1.0)   # 記録の直前に確かめ直す

    def state(self, cid="C-001"):
        return next(r["状態"] for r in self.w.mem.commitment_rows(self.root) if r["ID"] == cid)

    def test_drop_on_one_screen_then_done_on_another_is_refused(self):
        # 独立レビュー R-035：取り下げたあと、古い表示の画面で承認・状態ボタン → done に上書きしない
        self.approved_ready()
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-001", "state": "dropped"})[0], 200)
        for path, body in (("/api/task/approve", {"id": "C-001"}), ("/api/commitment/state", {"id": "C-001", "state": "done"})):
            code, res = self.srv.do_post(self.root, path, body)
            self.assertEqual((code, self.state()), (400, "dropped"), res)
            self.assertIn("取り下げ済み", res["error"])

    def test_second_approve_after_done_changes_nothing(self):
        # 独立レビュー R-035：done のあとに途中の変更が足されてから承認の再送 → 完了済みの判定を書き換えない
        rid = self.approved_ready()
        self.assertEqual(self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 200)
        self.w.note(self.root, "C-001", "部長：対象を5社に")
        code, res = self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})
        self.assertEqual((code, self.state()), (200, "done"), res)
        self.assertTrue(self.w.run_row(self.root, rid)["判定"].startswith("受け入れ"))

    def test_run_started_after_status_read_says_running(self):
        # 独立レビュー R-035：承認の read_status（ロックの外）のあとに起動が入る → 「報告が無い」でなく「実行中」
        self.approved_ready()
        real = self.srv.run_task.read_status

        def hooked(root, cid):
            st = real(root, cid)
            self.rt.start_run(self.root, "C-001", 1.0)
            return st

        self.srv.run_task.read_status = hooked
        code, res = self.srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})
        self.assertEqual((code, self.state()), (400, "open"), res)
        self.assertIn("実行中", res["error"])

    def test_cli_suggests_force_only_for_the_gate(self):
        # 独立レビュー R-035：見つからない・取り下げ済みには --force を案内しない（外しても直らない）
        out = self.mem("commitment", "set-state", "C-099", "done", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("--force", out.stdout + out.stderr)

    def test_state_button_and_cli_use_the_same_gate(self):
        self.approved_ready()
        self.w.need(self.root, "C-001", "自分", "予算の上限", d(3), "比較", "", "")
        code, res = self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-001", "state": "done"})
        self.assertEqual(code, 400)
        self.assertIn("不足", res["error"])
        out = self.mem("commitment", "set-state", "C-001", "done", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("--force", out.stdout + out.stderr)


class HumanNeedsTest(Base):
    """#12 段階3（設計 §10 v2）：人・相手の不足の答え・下書き・送った。独立レビュー R-037 の前提（docs/reviews/R-037_test_premises.py）を
    逆の結果で確かめ、順序を入れ替える。"""

    def setUp(self):
        super().setUp()
        self.write(".claude/agents/researcher.md", "---\nname: researcher\n---\n")
        self.w.order_set(self.root, "C-001", {"目的": "費用比較"})

    def need(self, cid="C-001", content="予算の上限", who="自分", **kw):
        return self.w.need(self.root, cid, who, content, kw.get("due", d(3)), kw.get("why", "比較の基準"),
                           kw.get("check", ""), kw.get("link", ""))

    def state(self, cid):
        return next(r["状態"] for r in self.w.mem.commitment_rows(self.root) if r["ID"] == cid)

    def waiting(self):
        return [x["task"] for x in self.w.launch_waiting(self.root)]

    def answers(self, cid="C-001"):
        return [x.get("answer") for x in self.w.needs_view(self.root, cid)]

    def post(self, path, body):
        return self.srv.do_post(self.root, path, body)

    def test_state_button_and_cli_cannot_close_a_need_without_an_answer(self):
        nid, _ = self.need()
        for st in ("done", "dropped"):
            code, res = self.post("/api/commitment/state", {"id": nid, "state": st})
            self.assertEqual(code, 400, res)
            self.assertIn("答える", res["error"])
        out = self.mem("commitment", "set-state", nid, "done", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("answer", out.stdout + out.stderr)                 # 答えへ回す（--force は案内しない）
        self.assertNotIn("--force", out.stdout + out.stderr)
        self.assertEqual(self.state(nid), "open")
        self.assertNotIn("C-001", self.waiting())

    def test_answer_closes_and_the_blocked_task_reads_it_from_one_place(self):
        nid, _ = self.need()
        code, res = self.post("/api/need/answer", {"id": nid, "answer": "月 300万円まで"})
        self.assertEqual((code, self.state(nid)), (200, "done"), res)
        self.assertEqual(self.answers()[0]["text"], "月 300万円まで")
        self.assertEqual([n for n in self.w.read_task(self.root, "C-001")["notes"] if nid in n], [])   # 作業記録には写さない
        self.assertIn("C-001", self.waiting())
        shown = json.loads(self.run_py("tools/work.py", "show", "C-001", check=True).stdout)
        self.assertEqual(shown["needs"][0]["answer"]["text"], "月 300万円まで")

    def test_link_after_answer_and_reopen_after_close_still_read_the_answer(self):
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "別件", "--due", d(10))   # C-002
        nid, _ = self.need()
        self.w.answer(self.root, nid, "月 300万円まで")
        self.assertEqual(self.need("C-002", link=nid), (nid, False))      # あとから同じ不足を待つ仕事
        self.assertEqual(self.answers("C-002")[0]["text"], "月 300万円まで")
        self.w.mem.commitment_set_state(self.root, "C-001", "dropped")    # 止めていた仕事を閉じて、開き直す
        self.w.mem.commitment_set_state(self.root, "C-001", "open")
        self.assertEqual(self.answers()[0]["text"], "月 300万円まで")

    def test_answer_when_no_open_task_is_blocked_is_kept_for_reopen(self):
        nid, _ = self.need()
        self.w.mem.commitment_set_state(self.root, "C-001", "dropped")
        self.assertEqual(self.post("/api/commitment/state", {"id": nid, "state": "dropped"})[0], 200)   # 止めている仕事が無い：普通に閉じてよい
        self.w.mem.commitment_set_state(self.root, nid, "open")
        self.w.answer(self.root, nid, "月 300万円まで")                     # 止めている仕事が閉じていても答えは不足の記録に残る
        self.w.mem.commitment_set_state(self.root, "C-001", "open")
        self.assertEqual(self.answers()[0]["text"], "月 300万円まで")

    def test_second_answer_same_is_noop_different_is_refused_with_the_first(self):
        nid, _ = self.need()
        self.assertEqual(self.post("/api/need/answer", {"id": nid, "answer": "月 300万円まで"})[0], 200)
        code, res = self.post("/api/need/answer", {"id": nid, "answer": "月 300万円まで"})
        self.assertEqual(code, 200, res)                                   # 二度押し：変更なし
        code, res = self.post("/api/need/answer", {"id": nid, "answer": "月 500万円まで"})   # 別の画面の別の答え
        self.assertEqual(code, 400)
        self.assertIn("月 300万円まで", res["error"])                      # 黙って捨てず、先の答えを見せる
        self.assertEqual(len(self.w.read_need_rec(self.root, nid)["answers"]), 1)
        self.w.mem.commitment_set_state(self.root, nid, "open")           # 開き直したら新しい版
        self.w.answer(self.root, nid, "月 500万円まで")
        self.assertEqual(self.answers()[0]["text"], "月 500万円まで")

    def test_crash_between_record_and_state_does_not_duplicate(self):
        nid, _ = self.need()
        rec = {"answers": [{"版": 1, "日時": "2026-11-06 10:00:00", "誰が": "人", "種類": "回答", "body": "月 300万円まで"}], "drafts": []}
        self.w._write_need_rec(self.root, nid, rec)                       # 記録を書いたあと、状態の前で落ちた
        self.w.answer(self.root, nid, "月 300万円まで")                     # やり直し
        self.assertEqual((len(self.w.read_need_rec(self.root, nid)["answers"]), self.state(nid)), (1, "done"))

    def test_without_does_not_make_the_result_stale(self):
        rid = self.finished_run()
        self.w.accept(self.root, rid, "ask", "人へ")
        nid, _ = self.need()
        self.w.answer(self.root, nid, "今期は上限なし", without=True)
        self.assertEqual((self.state(nid), self.w.stale_refs(self.root, rid)), ("dropped", []))
        self.assertEqual(self.w.done_blocker(self.root, "C-001"), "")
        self.assertEqual(self.answers()[0]["kind"], "無しで進める")

    def test_answer_before_report_is_not_stale_after_report_is(self):
        rid = self.w.start_run_within_limits(self.root, "C-001", 1.0)
        nid, _ = self.need()
        self.w.answer(self.root, nid, "月 300万円まで")                     # 実行中に答えた（報告の前に読む）
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.report(rid, minutes_ago=-2)                                   # 報告は答えのあと
        self.assertFalse(any(s.startswith(f"不足 {nid}") for s in self.w.stale_refs(self.root, rid)))
        self.assertNotIn("C-001", self.waiting())                          # そろってから作った成果：再実行しない
        rid2 = self.w.start_run_within_limits(self.root, "C-001", 1.0)    # 逆の順：報告のあとに答えた
        nid2, _ = self.need(content="比べ方")
        self.w.run_end(self.root, rid2, "完了", 0.5)
        self.report(rid2, minutes_ago=2)
        self.w.answer(self.root, nid2, "総額で比べる")
        self.assertTrue(any(s.startswith(f"不足 {nid2}") for s in self.w.stale_refs(self.root, rid2)))

    def test_gathering_need_is_closed_by_approval_not_answer(self):
        nid, _ = self.need(who="@researcher", content="B社の月額")
        self.w.start_run_within_limits(self.root, nid, 1.0)
        with self.assertRaisesRegex(self.w.MemError, "承認"):
            self.w.answer(self.root, nid, "B社は月 5万円")
        self.assertEqual(self.state(nid), "open")

    def test_person_need_draft_sent_and_follow_in_order(self):
        pid, _ = self.need(who="佐藤", content="見積の再送", due=d(0))
        kinds = lambda: [s["what"] for s in self.w.scan(self.root) if s.get("task") == pid]
        self.assertTrue(kinds() and "下書きを作る" in kinds()[0])           # 送る前に「催促」を出さない
        r = self.run_py("tools/work.py", "draft", pid, "--via", "メール", "--subject", "見積の再送のお願い",
                        "--body", "佐藤様\\n見積を再送いただけますか", env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)                        # 無人は下書きまで
        self.assertEqual(kinds(), [])                                      # 未送信：提案はしない（ボードと朝のブリーフに出る）
        self.w.draft(self.root, pid, "メール", "見積の再送のお願い", "佐藤様\n至急で再送をお願いします")   # 人が直す（同じ版）
        r = self.run_py("tools/work.py", "draft", pid, "--body", "上書き", env={"AI_AGENCY_UNATTENDED": "1"})
        rec = self.w.read_need_rec(self.root, pid)
        self.assertEqual((len(rec["drafts"]), rec["drafts"][0]["body"]), (1, "佐藤様\n至急で再送をお願いします"))   # 無人は人の版を消さない
        self.assertIn("無人では書き換えない", r.stdout)
        r = self.run_py("tools/work.py", "sent", pid, env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertNotEqual(r.returncode, 0)                               # 送ったことにできるのは人だけ
        code, res = self.post("/api/need/sent", {"id": pid, "version": "1", "follow": d(0)})
        self.assertEqual(code, 200, res)
        code, res = self.post("/api/need/sent", {"id": pid, "version": "1"})   # 二度押し
        self.assertEqual(code, 200)
        self.assertIn("変更なし", res["message"])
        rec = self.w.read_need_rec(self.root, pid)
        self.assertEqual([bool(x["送った"]) for x in rec["drafts"]], [True])   # 送っていない催促を記録しない
        self.assertEqual(self.state(pid), "open")                          # 送ったの正本は記録（状態は変えない）
        self.assertTrue(any("催促" in k for k in kinds()))                 # 送ったあとフォロー日：催促の下書き
        r = self.run_py("tools/work.py", "draft", pid, "--body", "再度のお願い", env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertEqual(len(self.w.read_need_rec(self.root, pid)["drafts"]), 2)   # 送った版は書き換えず次の版
        self.assertEqual(self.post("/api/need/answer", {"id": pid, "answer": "再送された（11/6 受領）"})[0], 200)
        self.assertEqual(kinds(), [])                                      # 閉じたら催促しない

    def test_unattended_cannot_answer(self):
        nid, _ = self.need()
        r = self.run_py("tools/work.py", "answer", nid, "上限なし", env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.state(nid), "open")

    def test_default_follow(self):
        from datetime import date as _d
        t = _d(2026, 11, 6)
        self.assertEqual(self.w.default_follow("2026-11-20", t), "2026-11-08")
        self.assertEqual(self.w.default_follow("2026-11-07", t), "2026-11-07")
        self.assertEqual(self.w.default_follow("2026-11-01", t), "2026-11-07")   # 期限を過ぎていれば翌日
        self.assertEqual(self.w.default_follow("なし", t), "2026-11-08")

    def test_board_shows_need_card_and_waiting_on_me(self):
        nid, _ = self.need()
        pid, _ = self.need(who="佐藤", content="見積の再送")
        rows = {r["ID"]: r for r in self.srv.api_commitments(self.root)["rows"]}
        self.assertEqual((rows[nid]["need"]["kind"], rows[nid]["need"]["blocks"]), ("self", ["C-001"]))
        self.assertEqual((rows[pid]["need"]["kind"], rows[pid]["need"]["draft"]), ("person", None))
        self.w.answer(self.root, nid, "月 300万円まで")
        rows = {r["ID"]: r for r in self.srv.api_commitments(self.root)["rows"]}
        self.assertEqual([x["id"] for x in rows["C-001"]["answered"]], [nid])
        self.assertEqual([x["id"] for x in rows["C-001"]["needs"]], [pid])

    # ---- 独立レビュー R-037（実装）の反例
    def backdate_answer(self, nid, seconds):
        rec = self.w.read_need_rec(self.root, nid)
        rec["answers"][-1]["日時"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - seconds))
        self.w._write_need_rec(self.root, nid, rec)

    def test_link_of_an_answered_need_after_the_report_makes_it_stale(self):
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "別件", "--due", d(10))   # C-002
        self.w.order_set(self.root, "C-002", {"目的": "別件"})
        nid, _ = self.need("C-002")
        self.w.answer(self.root, nid, "月 300万円まで")
        self.backdate_answer(nid, 600)                                     # ずっと前に答えた
        rid = self.finished_run("C-001", report=False)
        self.report(rid, minutes_ago=2)                                    # 報告は答えのあと
        self.need("C-001", link=nid)                                       # 報告のあとに、答え済みの不足を足した
        self.assertTrue(any(s.startswith(f"不足 {nid}") for s in self.w.stale_refs(self.root, rid)))
        self.assertIn("C-001", self.waiting())

    def test_reopen_and_a_different_answer_makes_the_result_stale(self):
        nid, _ = self.need()
        self.w.answer(self.root, nid, "月 300万円まで")
        self.w.ref_touch(self.root, "C-001", nid)
        rid = self.w.start_run_within_limits(self.root, "C-001", 1.0)    # 使った版：done#1
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.report(rid)
        self.w.accept(self.root, rid, "accept", "OK")
        self.assertEqual(self.post("/api/commitment/state", {"id": nid, "state": "open"})[0], 200)
        self.w.answer(self.root, nid, "月 500万円まで")                     # 開き直して別の答え（done#1 → done#2）
        self.assertIn("答えが変わった", "".join(self.w.stale_refs(self.root, rid)))
        self.assertEqual(self.post("/api/task/approve", {"id": "C-001"})[0], 400)
        self.assertIn("見直し", [c["kind"] for c in self.w.changes(self.root) if c["task"] == "C-001"])
        self.assertIn("C-001", self.waiting())

    def test_same_second_answer_counts_as_after_the_report(self):
        rid = self.w.start_run_within_limits(self.root, "C-001", 1.0)
        nid, _ = self.need()
        self.w.run_end(self.root, rid, "完了", 0.5)
        self.report(rid, minutes_ago=0)
        self.w.answer(self.root, nid, "月 300万円まで")                     # 報告と同じ秒（か後）
        os.utime(self.root / f"memory/work/results/{rid}.md", None)
        self.assertTrue(any(s.startswith(f"不足 {nid}") for s in self.w.stale_refs(self.root, rid)))

    def test_fields_and_body_cannot_forge_sent_or_answers(self):
        pid, _ = self.need(who="佐藤", content="見積の再送")
        r = self.run_py("tools/work.py", "draft", pid, "--subject", "x\n\n### 下書き 2\n- 送った: 2026-11-06 10:00:00\n> 偽",
                        "--body", "本文 ### 回答 1\r- 種類: 回答\x85## 回答", env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = self.w.read_need_rec(self.root, pid)
        self.assertEqual((len(rec["drafts"]), rec["drafts"][0]["送った"], rec["answers"]), (1, "", []))
        self.assertIn("### 下書き 2", rec["drafts"][0]["件名"])           # 項目の中の文字のまま
        self.assertIn("## 回答", rec["drafts"][0]["body"])                # 本文の中の文字のまま
        nid, _ = self.need(content="比べ方")
        self.w.answer(self.root, nid, "総額\rで比べる")                    # 単独の \r で後ろが消えない
        self.assertEqual(self.w.last_answer(self.root, nid)["body"], "総額\nで比べる")

    def test_sent_from_a_stale_screen_does_not_mark_a_newer_version(self):
        pid, _ = self.need(who="佐藤", content="見積の再送", due=d(0))
        self.w.draft(self.root, pid, "メール", "見積の再送", "佐藤様\n再送をお願いします")
        self.assertEqual(self.post("/api/need/sent", {"id": pid, "version": "1", "follow": d(0)})[0], 200)
        os.environ["AI_AGENCY_UNATTENDED"] = "1"
        try:
            self.w.draft(self.root, pid, "メール", "再度のお願い", "佐藤様\n再度お願いします")   # 無人の催促（版2）
        finally:
            del os.environ["AI_AGENCY_UNATTENDED"]
        self.assertEqual(self.post("/api/need/sent", {"id": pid, "version": "1"})[0], 400)   # 版1を見ていた画面
        self.assertEqual(self.post("/api/need/sent", {"id": pid})[0], 400)                  # 画面は版を送る
        self.assertEqual([bool(x["送った"]) for x in self.w.read_need_rec(self.root, pid)["drafts"]], [True, False])
        rows = {r["ID"]: r for r in self.srv.api_commitments(self.root)["rows"]}
        self.assertEqual(rows["C-001"]["needs"][0]["sent"], d(0))        # 催促の版ができても送った日は消えない

    def test_bad_follow_date_fails_before_marking_sent(self):
        pid, _ = self.need(who="佐藤", content="見積の再送", due=d(5))
        self.w.draft(self.root, pid, "メール", "見積の再送", "本文")
        self.assertEqual(self.post("/api/need/sent", {"id": pid, "version": "1", "follow": "2026-02-30"})[0], 400)
        self.assertEqual(self.w.read_need_rec(self.root, pid)["drafts"][0]["送った"], "")

    def test_need_closed_without_an_answer_blocks_until_answered(self):
        nid, _ = self.need()
        self.assertEqual(self.post("/api/commitment/state", {"id": "C-001", "state": "dropped"})[0], 200)
        self.assertEqual(self.post("/api/commitment/state", {"id": nid, "state": "done"})[0], 200)   # 止めている仕事が無いので閉じられた
        self.assertEqual(self.post("/api/commitment/state", {"id": "C-001", "state": "open"})[0], 200)
        self.assertIn("答えの記録が無い", "".join(self.w.unmet_conditions(self.root, "C-001")))
        self.assertNotIn("C-001", self.waiting())
        rows = {r["ID"]: r for r in self.srv.api_commitments(self.root)["rows"]}
        self.assertTrue(rows[nid]["need"]["unanswered"])
        self.assertEqual(self.post("/api/need/answer", {"id": nid, "answer": "月 300万円まで"})[0], 200)   # 閉じたままでも答えを残せる
        self.assertEqual(self.w.unmet_conditions(self.root, "C-001"), [])
        self.assertEqual(self.answers()[0]["text"], "月 300万円まで")

    def test_link_to_a_person_commitment_closed_without_answer_blocks(self):
        self.mem("commitment", "add", "--kind", "given", "--who", "佐藤", "--content", "見積の再送", "--due", d(3), "--follow", d(2))
        self.mem("commitment", "set-state", "C-002", "done")
        self.need("C-001", link="C-002")
        self.assertIn("答えの記録が無い", "".join(self.w.unmet_conditions(self.root, "C-001")))

    def test_needs_overview_follows_the_person_flow(self):
        nid, _ = self.need()
        pid, _ = self.need(who="佐藤", content="見積の再送")
        nxt = lambda: {x["id"]: x["next"] for x in self.w.needs_overview(self.root)}
        self.assertEqual(nxt(), {nid: "あなたが答える", pid: "下書きを作る"})
        self.w.draft(self.root, pid, "メール", "見積", "本文")
        self.assertIn("を送る", nxt()[pid])
        self.w.sent(self.root, pid, version=1)
        self.assertIn("返事を待つ", nxt()[pid])
        self.w.answer(self.root, pid, "再送された")
        self.w.answer(self.root, nid, "月 300万円まで")
        self.assertEqual(nxt(), {})
        r = self.run_py("tools/work.py", "needs", "--json", env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertEqual((r.returncode, json.loads(r.stdout)["items"]), (0, []))   # 無人の朝のブリーフ・見直しも読める

    def test_scan_skips_needs_whose_blocked_tasks_are_closed(self):
        pid, _ = self.need(who="佐藤", content="見積の再送", due=d(1))
        self.assertTrue([s for s in self.w.scan(self.root) if s.get("task") == pid])
        self.w.mem.commitment_set_state(self.root, "C-001", "dropped")
        self.assertEqual([s for s in self.w.scan(self.root) if s.get("task") == pid], [])

    def test_unattended_can_fix_its_own_unsent_draft_but_not_a_persons(self):
        pid, _ = self.need(who="佐藤", content="見積の再送")
        env = {"AI_AGENCY_UNATTENDED": "1"}
        self.run_py("tools/work.py", "draft", pid, "--body", "書き損じ", env=env, check=True)
        self.run_py("tools/work.py", "draft", pid, "--body", "直した", "--by", "人", env=env, check=True)
        d1 = self.w.read_need_rec(self.root, pid)["drafts"]
        self.assertEqual([(x["版"], x["body"], x["書き手"]) for x in d1], [(1, "直した", "見直し（無人）")])   # 自分の版は直せる・人を名乗れない
        self.w.draft(self.root, pid, "メール", "", "人が直した")
        self.run_py("tools/work.py", "draft", pid, "--body", "上書き", env=env, check=True)
        self.assertEqual(self.w.read_need_rec(self.root, pid)["drafts"][-1]["body"], "人が直した")


class UnattendedSendGuardTest(TempRepo):
    """無人のとき、コネクタの相手に届く操作を断る（設計 §10 v2・独立レビュー R-037 の M5）。人の会話では断らない。"""

    def guard(self, tool, unattended):
        ev = json.dumps({"tool_name": tool, "tool_input": {"to": "x@example.com", "body": "DELETE FROM t"},
                         "hook_event_name": "PreToolUse", "session_id": "abcdef1234"})
        return self.run_py("tools/hooks/guardrail.py", stdin=ev, env={"AI_AGENCY_UNATTENDED": "1"} if unattended else None)

    def test_blocks_send_like_tools_only_when_unattended(self):
        for tool in ("mcp__gmail__send_message", "mcp__slack__chat_postMessage", "mcp__gmail__reply",
                     "mcp__calendar__create_event", "mcp__drive__share_file", "mcp__gmail__forward",
                     "mcp__notion__notion-calendar-create-events", "mcp__x__sendMessage", "mcp__x__replyToThread",
                     "mcp__mail__messages.send", "mcp__box__create_file_comment", "mcp__teams__create_message",
                     "mcp__claude-in-chrome__computer", "mcp__claude-in-chrome__form_input", "mcp__gmail__delete_draft"):
            self.assertEqual(self.guard(tool, True).returncode, 2, tool)
            self.assertEqual(self.guard(tool, False).returncode, 0, tool)
        for tool in ("mcp__gmail__create_draft", "mcp__gmail__search_threads", "mcp__calendar__list_events",
                     "mcp__drive__read_file_content", "mcp__gmail__get_message", "mcp__notion__notion-search",
                     "mcp__claude-in-chrome__read_page"):
            self.assertEqual(self.guard(tool, True).returncode, 0, tool)   # 読み取り・下書きは無人でもよい

    def test_settings_route_mcp_tools_to_the_guard(self):
        d_ = json.loads((Path(__file__).resolve().parent.parent / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertTrue(any(h.get("matcher") == "mcp__.*" and "guardrail.py" in h["hooks"][0]["command"]
                            for h in d_["hooks"]["PreToolUse"]))


if __name__ == "__main__":
    unittest.main()
