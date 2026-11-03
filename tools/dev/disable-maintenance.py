#!/usr/bin/env python3
"""
disable-maintenance.py ── enable-maintenance.py が生成したメンテナンス層を取り除き、本体フォルダでも
製品（秘書）が動く状態に戻す。settings.local.json の他のキー（permissions.allow 等）は保持する。
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL = ROOT / ".claude" / "settings.local.json"
EXCLUDE_FILES = ["CLAUDE.md", "memory/dashboard.md"]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    folder = ROOT.name
    if LOCAL.exists():
        settings = json.loads(LOCAL.read_text(encoding="utf-8"))
        ours = {f"**/{folder}/{f}" for f in EXCLUDE_FILES}
        settings["claudeMdExcludes"] = [p for p in settings.get("claudeMdExcludes", []) if p not in ours]
        if not settings["claudeMdExcludes"]:
            settings.pop("claudeMdExcludes")
        settings.pop("disableAllHooks", None)
        settings.pop("skillOverrides", None)
        servers = set(settings.pop("disabledMcpjsonServers", []))
        if servers:
            settings["enabledMcpjsonServers"] = sorted(set(settings.get("enabledMcpjsonServers", [])) | servers)
        settings.pop("$comment_maintenance", None)
        LOCAL.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for p in [ROOT / "CLAUDE.local.md", ROOT / ".ai-agency-template"]:
        if p.exists():
            p.unlink()
    for d in (ROOT / ".claude" / "skills").glob("maint-*"):
        if d.is_dir():
            shutil.rmtree(d)

    print("[disable-maintenance] メンテナンス層を取り除きました。新しいセッションから製品（秘書）が動きます。")


if __name__ == "__main__":
    main()
