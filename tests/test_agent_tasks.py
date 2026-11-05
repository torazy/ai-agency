"""エージェントに任せるタスク（約束台帳の @handle）と、エージェントの動きの記録（agent-activity フック → ボード）。"""

import json
import os
import subprocess
import sys
import time
import unittest

from helpers import TempRepo, d


class AgentTaskMemTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.write(".claude/agents/generalist.md", "---\nname: generalist\n---\n")
        self.write(".claude/agents/legal.md", "---\nname: legal\n---\n")

    def test_given_to_agent_needs_no_follow_and_checks_handle(self):
        self.mem("commitment", "add", "--kind", "given", "--who", "@generalist", "--content", "競合3社の料金調査", "--due", d(5))
        c = self.read("memory/commitments.md")
        self.assertIn(f"| @generalist | 競合3社の料金調査 | {d(5)} | {d(5)} |", c)      # フォロー日は期限と同じ
        r = self.mem("commitment", "add", "--kind", "given", "--who", "@nobody", "--content", "x", "--due", d(3), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("@generalist", r.stdout + r.stderr)                                      # いるエージェントを示す
        self.assertNotEqual(self.mem("commitment", "add", "--kind", "given", "--who", "@../x", "--content", "x",
                                     check=False).returncode, 0)
        # 人に出す指示は従来どおりフォロー日が必要
        self.assertNotEqual(self.mem("commitment", "add", "--kind", "given", "--who", "山田", "--content", "見積",
                                     "--due", d(5), check=False).returncode, 0)
        self.mem("check")

    def test_set_content(self):
        self.mem("commitment", "add", "--kind", "task", "--content", "PSR を出す（意味は確認中）", "--due", d(2))
        self.mem("commitment", "set-content", "C-001", "パイプライン状況報告を出す")
        self.assertIn("| パイプライン状況報告を出す |", self.read("memory/commitments.md"))
        self.assertNotEqual(self.mem("commitment", "set-content", "C-001", "", check=False).returncode, 0)
        self.assertNotEqual(self.mem("commitment", "set-content", "C-099", "x", check=False).returncode, 0)
        r = self.run_py("tools/mem.py", "commitment", "set-content", "C-001", "y", env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertNotEqual(r.returncode, 0)                                                     # 無人実行では書き換えない


def hook_ev(hook, **kw):
    return json.dumps({"hook_event_name": hook, "session_id": "s1", **kw}, ensure_ascii=False)


class AgentActivityHookTest(TempRepo):
    def hook(self, event, env=None):
        r = self.run_py("tools/hooks/agent-activity.py", stdin=event, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), {"suppressOutput": True})

    def events(self):
        p = self.root / "memory/task-log/agent-runs.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def test_foreground_background_failure_and_stop(self):
        self.hook(hook_ev("PreToolUse", tool_name="Agent", tool_use_id="t1",
                          tool_input={"subagent_type": "generalist", "description": "C-007 の下調べ", "prompt": "C-008 も見る"}))
        self.hook(hook_ev("PostToolUse", tool_name="Agent", tool_use_id="t1", tool_input={}))
        self.hook(hook_ev("PreToolUse", tool_name="Agent", tool_use_id="t2",
                          tool_input={"description": "長い調査", "prompt": "x", "run_in_background": True}))
        self.hook(hook_ev("PostToolUse", tool_name="Agent", tool_use_id="t2", tool_input={"run_in_background": True},
                          tool_response={"isAsync": True, "status": "async_launched", "agentId": "a1"}))
        self.hook(hook_ev("PostToolUseFailure", tool_name="Task", tool_use_id="t3", tool_input={}))
        self.hook(hook_ev("SubagentStop", agent_type="general-purpose", agent_id="a1"), env={"AI_AGENCY_UNATTENDED": "1"})
        self.hook(hook_ev("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}))       # 関係ないものは記録しない
        self.hook("not json")                                                                       # 壊れた入力でも邪魔しない
        ev = self.events()
        self.assertEqual([e["ev"] for e in ev], ["start", "end", "start", "launched", "fail", "stop"])
        self.assertEqual(ev[0]["agent"], "generalist")
        self.assertEqual(ev[0]["tasks"], ["C-007", "C-008"])
        self.assertEqual(ev[2]["agent"], "general-purpose")
        self.assertTrue(ev[2]["bg"])
        self.assertTrue(ev[5].get("unattended"))
        self.assertEqual((ev[3]["aid"], ev[5]["aid"]), ("a1", "a1"))                              # 起動と終了を agentId で結ぶ


class BoardActivityTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.write(".claude/agents/generalist.md", "---\nname: generalist\n---\n")
        self.mem("commitment", "add", "--kind", "given", "--who", "@generalist", "--content", "調査A", "--due", d(5))
        self.mem("commitment", "add", "--kind", "given", "--who", "@generalist", "--content", "調査B", "--due", d(5))
        self.mem("commitment", "add", "--kind", "given", "--who", "山田", "--content", "見積", "--due", d(5), "--follow", d(4))
        self.mem("commitment", "add", "--kind", "task", "--content", "体裁を整える", "--due", d(2))
        now = time.time()
        rows = [
            {"ts": now - 300, "sid": "s", "ev": "start", "tuid": "a", "agent": "generalist", "desc": "C-001 調査A", "tasks": ["C-001"], "bg": False},
            {"ts": now - 200, "sid": "s", "ev": "end", "tuid": "a"},
            {"ts": now - 60, "sid": "s", "ev": "start", "tuid": "b", "agent": "generalist", "desc": "C-002 調査B", "tasks": ["C-002"], "bg": True},
            {"ts": now - 59, "sid": "s", "ev": "launched", "tuid": "b"},
            {"ts": now - 9 * 3600, "sid": "s", "ev": "start", "tuid": "c", "agent": "legal", "desc": "放置", "tasks": [], "bg": False},
        ]
        self.write("memory/task-log/agent-runs.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nbroken\n")
        self.srv = self.load("tools/board/server.py", "board_activity")
        self.srv.mem.ok = lambda m: self.srv.mem.LAST_OK.append(m)

    def test_activity_states(self):
        a = self.srv.api_activity(self.root)
        self.assertEqual([r["tuid"] for r in a["running"]], ["b"])
        states = {r["tuid"]: r["state"] for r in a["recent"]}
        self.assertEqual(states, {"a": "done", "c": "unknown"})                 # 終了の記録が無く時間がたったものは不明
        self.assertEqual(a["by_agent"]["generalist"]["running"], 1)
        self.assertEqual(a["by_agent"]["generalist"]["today"], 2)

    def test_background_stop_closes_run(self):
        with open(self.root / "memory/task-log/agent-runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "sid": "s", "ev": "stop", "agent": "generalist"}) + "\n")
        self.assertEqual(self.srv.api_activity(self.root)["running"], [])

    def test_background_stop_matches_agent_id(self):
        # 同じ会話・同じ種類を2件バックグラウンドで起動し、後から起動した方が先に終わる（テストインスタンスで実測した順）
        now = time.time()
        rows = [
            {"ts": now - 30, "sid": "s2", "ev": "start", "tuid": "x", "agent": "generalist", "desc": "C-901 長い", "tasks": [], "bg": True},
            {"ts": now - 30, "sid": "s2", "ev": "launched", "tuid": "x", "aid": "ax"},
            {"ts": now - 28, "sid": "s2", "ev": "start", "tuid": "y", "agent": "generalist", "desc": "C-902 短い", "tasks": [], "bg": True},
            {"ts": now - 28, "sid": "s2", "ev": "launched", "tuid": "y", "aid": "ay"},
            {"ts": now - 20, "sid": "s2", "ev": "stop", "agent": "generalist", "aid": "ay"},
        ]
        with open(self.root / "memory/task-log/agent-runs.jsonl", "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        a = self.srv.api_activity(self.root)
        self.assertEqual(sorted(r["tuid"] for r in a["running"]), ["b", "x"])     # 先に終わったのは短い方（y）
        self.assertEqual({r["tuid"]: r["state"] for r in a["recent"]}.get("y"), "done")

    def test_commitments_assignee_and_agent_state(self):
        rows = {r["ID"]: r for r in self.srv.api_commitments(self.root)["rows"]}
        self.assertEqual((rows["C-001"]["assignee"], rows["C-001"]["agent_state"]), ("agent", "review"))   # 終わった→確認待ち
        self.assertEqual((rows["C-002"]["assignee"], rows["C-002"]["agent_state"]), ("agent", "running"))
        self.assertEqual(rows["C-003"]["assignee"], "person")
        self.assertEqual(rows["C-004"]["assignee"], "self")
        self.mem("commitment", "set-state", "C-001", "done")
        rows = {r["ID"]: r for r in self.srv.api_commitments(self.root)["rows"]}
        self.assertEqual(rows["C-001"]["agent_state"], "")                    # 完了にしたら確認待ちは消える

    def test_content_endpoint(self):
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/content", {"id": "C-004", "content": "体裁と誤字"})[0], 200)
        self.assertIn("| 体裁と誤字 |", self.read("memory/commitments.md"))
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/content", {"id": "C-004"})[0], 400)


class RunTaskTest(TempRepo):
    """tools/run_task.py：着手の検査・実行履歴・上限・停止・ボードからの起動（claude は起動しない）。"""

    def setUp(self):
        super().setUp()
        self.write(".claude/agents/pm.md", "---\nname: pm\n---\n")
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "スケジュール案", "--due", d(5))
        self.mem("commitment", "add", "--kind", "task", "--content", "自分の作業", "--due", d(5))
        self.rt = self.load("tools/run_task.py", "run_task_t")
        self.rt.work.mem.ok = lambda m: None
        self.rt.shutil.which = lambda name: "claude"

    def fake_run(self, cid="C-001", result="完了", cost=0.5, report=True):
        rid = self.rt.work.run_start(self.root, cid)
        if result != "実行中":
            self.rt.work.run_end(self.root, rid, result, cost)
        if report:
            self.write(f"memory/work/results/{rid}.md", f"# {rid}\n\n## 結論\n- 3案\n")
        return rid

    def test_check_task(self):
        self.assertEqual(self.rt.check_task(self.root, "C-001")["相手"], "@pm")
        for cid, why in [("C-002", "エージェントに任せた"), ("C-099", "無い"), ("x; rm", "形が違う")]:
            with self.assertRaises(ValueError) as cm:
                self.rt.check_task(self.root, cid)
            self.assertIn(why, str(cm.exception))
        rid = self.fake_run(result="実行中", report=False)
        self.rt.state_path(self.root, rid).parent.mkdir(parents=True, exist_ok=True)
        self.rt.state_path(self.root, rid).write_text(json.dumps({"start": time.time(), "pid": os.getpid()}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.rt.check_task(self.root, "C-001")                      # 二重起動しない（プロセスが生きている）
        self.rt.check_task(self.root, "C-001", child=True)              # 起動した本人は通す
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        self.rt.state_path(self.root, rid).write_text(json.dumps({"start": time.time() - 60, "pid": int(dead.stdout)}), encoding="utf-8")
        self.assertEqual(self.rt.read_status(self.root, "C-001")["state"], "unknown")  # プロセスが無い：終了不明として閉じる
        self.assertEqual(self.rt.work.run_row(self.root, rid)["結果"], "終了不明")
        self.mem("commitment", "set-state", "C-001", "done")
        with self.assertRaises(ValueError):
            self.rt.check_task(self.root, "C-001")
        (self.root / ".ai-agency-template").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "本体"):
            self.rt.check_task(self.root, "C-002")

    def test_allowed_tools_stay_read_only(self):
        tools = self.rt.allowed_tools()
        self.assertIn("Edit(memory/work/results/**)", tools)
        self.assertFalse([t for t in tools if t.startswith(("Write", "Edit")) and t != "Edit(memory/work/results/**)"])
        self.assertFalse([t for t in tools if t.startswith(("Bash(", "PowerShell(")) and "mem.py" in t
                          and not any(op in t for op in ("list", "check", "show"))])
        self.assertFalse([t for t in tools if t.startswith(("Bash(", "PowerShell(")) and "work.py" in t
                          and not any(op in t for op in ("show", "note", "handoff", "status"))])
        self.assertFalse([t for t in tools if "run_task.py" in t])            # 作業セッションは他の作業を起動しない
        self.assertNotIn("Bash", tools)
        self.assertNotIn("PowerShell", tools)
        cmd = self.rt.build_command("C-001", "C-001-R2", 1, "qa")
        self.assertEqual(cmd[2], "/run-task C-001 --run C-001-R2 --review qa")

    def test_capacity_limits(self):
        p = self.root / "memory/work/settings.md"
        p.write_text(p.read_text(encoding="utf-8").replace("| 同時実行の上限 | 2 |", "| 同時実行の上限 | 1 |")
                     .replace("| 1日の費用上限（USD） | 10 |", "| 1日の費用上限（USD） | 3 |"), encoding="utf-8")
        self.rt.check_capacity(self.root, 2.5)
        self.fake_run(cost=1.0)
        with self.assertRaisesRegex(ValueError, "1日の上限"):
            self.rt.check_capacity(self.root, 2.5)                      # 1.0 + 2.5 > 3
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "別件", "--due", d(5))
        self.fake_run("C-003", result="実行中", report=False)
        with self.assertRaisesRegex(ValueError, "同時実行"):
            self.rt.check_capacity(self.root, 0.5)

    def test_budget_floor_and_cut_off_leaves_note(self):
        for b in (0.1, 0.49, 20.5):
            with self.assertRaisesRegex(ValueError, "0.5〜20"):
                self.rt.launch(self.root, "C-001", b)
        self.assertFalse((self.root / "memory/work/tasks/C-001.md").exists() and self.rt.work.read_task(self.root, "C-001")["runs"])
        # 上限で打ち切られて報告が無いときは、作業記録の途中の変更に残す（実地：0.1 USD で起動だけで 0.30 使い報告なし）
        out = json.dumps({"subtype": "error_max_budget_usd", "total_cost_usd": 0.3})
        self.rt.build_command = lambda *a, **k: [sys.executable, "-c", f"print({out!r}); raise SystemExit(1)"]
        self.rt.work.ensure_task(self.root, "C-001")
        rid = self.rt.work.run_start(self.root, "C-001")
        self.rt.run_foreground(self.root, "C-001", rid, 0.5)
        t = self.rt.work.read_task(self.root, "C-001")
        self.assertEqual((t["runs"][-1]["結果"], t["runs"][-1]["費用"]), ("上限で打ち切り", "0.30"))
        self.assertIn("報告が残っていない", t["notes"][-1])

    def test_stop_marks_run(self):
        rid = self.fake_run(result="実行中", report=False)
        self.rt.state_path(self.root, rid).parent.mkdir(parents=True, exist_ok=True)
        self.rt.state_path(self.root, rid).write_text(json.dumps({"start": time.time(), "pid": 999999}), encoding="utf-8")
        self.assertEqual(self.rt.stop(self.root, "C-001", "新しい条件"), rid)
        t = self.rt.work.read_task(self.root, "C-001")
        self.assertEqual(t["runs"][-1]["結果"], "停止")
        self.assertIn("新しい条件", t["notes"][-1])
        with self.assertRaises(ValueError):
            self.rt.stop(self.root, "C-001")

    def test_board_run_approve_and_result(self):
        srv = self.load("tools/board/server.py", "board_runtask")
        srv.mem.ok = lambda m: srv.mem.LAST_OK.append(m)
        calls = []
        srv.run_task.launch = lambda root, cid, budget=None, review=None, anyway=False: (
            calls.append((cid, budget)), {"id": cid, "run": cid + "-R1", "agent": "@pm", "budget": budget or 1})[-1]
        self.assertEqual(srv.do_post(self.root, "/api/task/run", {"id": "C-001", "budget": "0.5"})[0], 200)
        self.assertEqual(calls, [("C-001", 0.5)])
        self.assertEqual(srv.do_post(self.root, "/api/task/run", {"id": "C-001", "budget": "50"})[0], 400)
        self.assertEqual(srv.do_post(self.root, "/api/task/run", {"id": "C-001", "budget": "0.1"})[0], 400)
        self.assertEqual(srv.do_post(self.root, "/api/task/run", {"id": "C-001", "budget": "abc"})[0], 400)
        self.assertEqual(srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 400)   # 報告が無い
        rid = self.fake_run()
        row = next(r for r in srv.api_commitments(self.root)["rows"] if r["ID"] == "C-001")
        self.assertEqual((row["has_result"], row["agent_state"]), (True, "review"))
        res = srv.api_task_result(self.root, "C-001")
        self.assertIn("3案", res["text"])
        self.assertEqual(res["run"], rid)
        self.assertEqual(srv.api_task_result(self.root, "../x"), {"error": "ID が不正"})
        self.assertEqual(srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})[0], 200)
        self.assertTrue(self.rt.work.read_task(self.root, "C-001")["runs"][-1]["判定"].startswith("受け入れ"))
        self.assertEqual(next(r for r in self.mem_rows() if r["ID"] == "C-001")["状態"], "done")

    def mem_rows(self):
        return self.rt.mem.commitment_rows(self.root)

    def test_approve_refuses_stale_result(self):
        srv = self.load("tools/board/server.py", "board_stale")
        srv.mem.ok = lambda m: srv.mem.LAST_OK.append(m)
        self.write("memory/projects/x/status.md", "# s\n\n## 見積\n- 未着\n")
        self.rt.work.ref_add(self.root, "C-001", "memory/projects/x/status.md#見積", "前提", "見積")
        self.fake_run()
        self.write("memory/projects/x/status.md", "# s\n\n## 見積\n- 120万\n")
        code, res = srv.do_post(self.root, "/api/task/approve", {"id": "C-001"})
        self.assertEqual(code, 400)
        self.assertIn("古い条件", res["error"])
        self.assertEqual(next(r for r in self.mem_rows() if r["ID"] == "C-001")["状態"], "open")
        row = next(r for r in srv.api_commitments(self.root)["rows"] if r["ID"] == "C-001")
        self.assertEqual(row["changes"], ["見直し"])

    def test_session_start_mentions_waiting_reports_and_proposals(self):
        rid = self.fake_run()
        self.rt.work.propose(self.root, "会議の準備", "定例が前倒し", "カレンダー", "慌てない", "@pm")
        out = self.run_py("tools/hooks/session-start.py").stdout
        self.assertIn(f"[任せたタスク] 報告が届いて確認待ち：{rid}（", out)
        self.assertIn("[提案] 人の判断待ち 0 件・未判定 1 件", out)
        self.rt.work.accept(self.root, rid, "accept")
        self.mem("commitment", "set-state", "C-001", "done")
        out = self.run_py("tools/hooks/session-start.py").stdout
        self.assertIn(f"承認済みで記憶への反映待ち：{rid}。", out)
        self.assertEqual(self.run_py("tools/run_task.py", "--applied", "C-001").returncode, 0)
        self.assertNotEqual(self.run_py("tools/run_task.py", "--applied", "C-002").returncode, 0)
        self.assertNotIn("反映待ち", self.run_py("tools/hooks/session-start.py").stdout)

    def test_cli_rejects_without_side_effects(self):
        r = self.run_py("tools/run_task.py", "C-002")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / "memory/work/tasks/C-002.md").exists())


class ReviewPlanTest(TempRepo):
    """進め方（レビューの有無・レビュアー）の記録と、報告のレビュー節の読み取り。"""

    def setUp(self):
        super().setUp()
        for h in ("pm", "qa"):
            self.write(f".claude/agents/{h}.md", f"---\nname: {h}\n---\n")
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "スケジュール案", "--due", d(5))
        self.rt = self.load("tools/run_task.py", "run_task_plan")
        self.rt.work.mem.ok = lambda m: None

    def test_plan_validation_is_kept_in_work_order(self):
        out = self.run_py("tools/run_task.py", "--plan", "C-001", "--review", "@qa", "--reason", "部長に出す", check=True).stdout
        self.assertIn("レビュー @qa（部長に出す）", out)
        self.assertEqual(self.rt.plan_of(self.root, "C-001"), {"review": "qa", "reason": "部長に出す"})
        self.assertIn("| レビュー | qa（部長に出す） |", self.read("memory/work/tasks/C-001.md"))     # 作業依頼に残る
        for bad in ("pm", "nobody", "../x"):                                  # 作成者自身・いない・不正
            self.assertNotEqual(self.run_py("tools/run_task.py", "--plan", "C-001", "--review", bad).returncode, 0)
        self.assertEqual(self.rt.plan_of(self.root, "C-001")["review"], "qa")  # 失敗しても前の進め方は残る
        self.rt.set_plan(self.root, "C-001", "none")
        self.assertEqual(self.rt.plan_of(self.root, "C-001"), {"review": "none", "reason": ""})

    def test_board_plan_and_review_badge(self):
        srv = self.load("tools/board/server.py", "board_plan")
        srv.mem.ok = lambda m: srv.mem.LAST_OK.append(m)
        self.assertEqual(srv.do_post(self.root, "/api/task/plan", {"id": "C-001", "review": "qa", "reason": "社外"})[0], 200)
        self.assertEqual(srv.do_post(self.root, "/api/task/plan", {"id": "C-001", "review": "pm"})[0], 400)
        self.assertEqual(srv.do_post(self.root, "/api/task/plan", {"id": "C-001", "review": "a b"})[0], 400)
        row = next(r for r in srv.api_commitments(self.root)["rows"] if r["ID"] == "C-001")
        self.assertEqual(row["plan"]["review"], "qa")
        rid = self.rt.work.run_start(self.root, "C-001")
        self.rt.work.run_end(self.root, rid, "完了", 1.0)
        self.write(f"memory/work/results/{rid}.md", "# C-001\n\n## 結論\nx\n\n## レビュー\n"
                   "- レビュアー: @qa ／ 判定: 要修正 → 修正済み（1回）\n- 指摘と対応:\n  - [重大] 根拠 → 追記\n- 残った指摘: 表の単位\n")
        row = next(r for r in srv.api_commitments(self.root)["rows"] if r["ID"] == "C-001")
        self.assertEqual(row["review"], {"reviewer": "@qa", "verdict": "要修正 → 修正済み（1回）", "left": True})
        self.assertEqual(srv.review_of("## レビュー\n- レビュアー: なし ／ 判定: なし（下調べ）\n- 残った指摘: なし\n")["left"], False)
        self.assertEqual(srv.review_of("## 結論\nx\n"), {})
        # 実際の報告で出た形：判定が太字、「残った指摘（重大）:」の下に箇条書き
        r = srv.review_of("## レビュー\n- レビュアー: @qa ／ 判定: **要修正 → 未修正**（上限）\n- 残った指摘（重大）:\n  1. 矛盾\n"
                          "- 残った指摘（軽微）:\n  - 単位\n")
        self.assertEqual((r["verdict"], r["left"]), ("要修正 → 未修正（上限）", True))

    def test_default_budget_counts_review(self):
        import os
        os.environ.pop("MAX_BUDGET_USD", None)
        self.assertEqual(self.rt.default_budget(self.root, "none"), 1.0)
        self.assertEqual(self.rt.default_budget(self.root, "qa"), 2.5)
        self.assertEqual(self.rt.default_budget(self.root, "auto"), 2.5)
        out = '{"type":"result","subtype":"success","result":"C-001：要点","total_cost_usd":1.8364}'
        self.assertEqual(self.rt.cost_of(out), 1.84)
        self.assertEqual(self.rt.summarize_output(out), "C-001：要点")
        self.assertIsNone(self.rt.cost_of("plain text"))
        self.assertEqual(self.rt.summarize_output("plain text"), "plain text")


class ProposalBoardTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.write(".claude/agents/pm.md", "---\nname: pm\n---\n")
        self.srv = self.load("tools/board/server.py", "board_props")
        self.srv.mem.ok = lambda m: self.srv.mem.LAST_OK.append(m)
        self.srv.work.propose(self.root, "定例の資料準備", "定例が前倒し", "カレンダー", "当日慌てない", "@pm")
        self.srv.work.propose(self.root, "古い比較表の見直し", "料金改定", "ベンダーのお知らせ", "誤った比較を出さない", "@pm")

    def test_adopt_hold_and_work_api(self):
        code, res = self.srv.do_post(self.root, "/api/proposal/adopt", {"sid": "S-001", "who": "@pm", "due": d(3)})
        self.assertEqual(code, 200, res)
        self.assertIn("C-001", self.read("memory/commitments.md"))
        self.assertEqual(self.srv.work.proposal_rows(self.root)[0]["判定"], "採用→C-001")
        self.assertEqual(self.srv.do_post(self.root, "/api/proposal/decide", {"sid": "S-002", "decision": "hold", "reason": "来週"})[0], 200)
        self.assertEqual(self.srv.do_post(self.root, "/api/proposal/decide", {"sid": "S-002", "decision": "hold"})[0], 400)
        w = self.srv.api_work(self.root)
        self.assertEqual(w["limits"]["max_running"], 2)
        self.assertEqual([p["ID"] for p in w["proposals"]], ["S-002", "S-001"])
        self.assertEqual(w["proposals_open"], [])


class BoardGuardTest(TempRepo):
    def test_request_allowed(self):
        srv = self.load("tools/board/server.py", "board_guard")
        f = srv.request_allowed
        self.assertEqual(f("GET", {"Host": "127.0.0.1:8765"}, 8765), "")
        self.assertEqual(f("GET", {"Host": "localhost:8765"}, 8765), "")
        self.assertNotEqual(f("GET", {"Host": "attacker.example:8765"}, 8765), "")     # DNS リバインディング
        self.assertNotEqual(f("GET", {"Host": "127.0.0.1:9999"}, 8765), "")
        ok = {"Host": "127.0.0.1:8765", "Content-Type": "application/json"}
        self.assertEqual(f("POST", ok, 8765), "")
        self.assertNotEqual(f("POST", {**ok, "Content-Type": "text/plain"}, 8765), "")   # フォーム送信型の CSRF
        self.assertNotEqual(f("POST", {**ok, "Origin": "https://attacker.example"}, 8765), "")
        self.assertEqual(f("POST", {**ok, "Origin": "http://localhost:8765"}, 8765), "")


if __name__ == "__main__":
    unittest.main()
