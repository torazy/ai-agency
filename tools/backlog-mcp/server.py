#!/usr/bin/env python3
"""
Backlog MCP Server
Backlog REST API をラップするカスタム MCP サーバー。
FastMCP ライブラリを使用。認証: APIキー（クエリパラメータ）。

環境変数:
    BACKLOG_API_KEY  : BacklogのAPIキー
    BACKLOG_SPACE    : Backlogスペース名（例: yourcompany → yourcompany.backlog.jp）
"""

import os
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastmcp import FastMCP

# ── 初期化 ──────────────────────────────────────────────────────────────────

mcp = FastMCP("backlog")

_API_KEY = os.environ.get("BACKLOG_API_KEY", "")
_SPACE = os.environ.get("BACKLOG_SPACE", "")
_BASE_URL = f"https://{_SPACE}.backlog.jp/api/v2" if _SPACE else ""


def _base_url() -> str:
    if not _BASE_URL:
        raise RuntimeError(
            "BACKLOG_SPACE 環境変数が設定されていません。"
            "例: BACKLOG_SPACE=yourcompany"
        )
    return _BASE_URL


def _params(**kwargs: Any) -> dict[str, Any]:
    """APIキーを含むクエリパラメータを生成する"""
    if not _API_KEY:
        raise RuntimeError("BACKLOG_API_KEY 環境変数が設定されていません。")
    return {"apiKey": _API_KEY, **{k: v for k, v in kwargs.items() if v is not None}}


async def _get(path: str, **kwargs: Any) -> Any:
    """GET リクエストを送信して結果を返す"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{_base_url()}{path}",
            params=_params(**kwargs),
        )
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict[str, Any]) -> Any:
    """POST リクエストを送信して結果を返す"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{_base_url()}{path}",
            params={"apiKey": _API_KEY},
            data=body,  # Backlog API v2 は application/x-www-form-urlencoded（JSON 本文は受け付けない）
        )
        resp.raise_for_status()
        return resp.json()


async def _patch(path: str, body: dict[str, Any]) -> Any:
    """PATCH リクエストを送信して結果を返す"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.patch(
            f"{_base_url()}{path}",
            params={"apiKey": _API_KEY},
            data=body,  # 同上（form 形式）
        )
        resp.raise_for_status()
        return resp.json()


# ── ツール定義 ───────────────────────────────────────────────────────────────


@mcp.tool()
async def backlog_list_projects() -> list[dict[str, Any]]:
    """
    アクセス可能なBacklogプロジェクトの一覧を取得する。
    各プロジェクトの id, projectKey, name を返す。
    """
    projects = await _get("/projects")
    return [
        {
            "id": p["id"],
            "projectKey": p["projectKey"],
            "name": p["name"],
        }
        for p in projects
    ]


@mcp.tool()
async def backlog_search_issues(
    project_key: str,
    keyword: Optional[str] = None,
    assignee_id: Optional[int] = None,
    status_id: Optional[list[int]] = None,
    priority_id: Optional[list[int]] = None,
    count: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Backlog のチケット（Issue）を検索する。

    Args:
        project_key: BacklogプロジェクトキーID（例: "MYPROJECT"）。必須。
        keyword: 検索キーワード。省略可。
        assignee_id: 担当者のバックログユーザーID。省略可。
        status_id: 状態IDのリスト（1=未対応, 2=処理中, 3=処理済み, 4=完了）。省略可。
        priority_id: 優先度IDのリスト（2=高, 3=中, 4=低）。省略可。
        count: 取得件数（最大100）。デフォルト20。
        offset: 取得開始位置。デフォルト0。
    """
    # プロジェクトキーからIDを取得する
    projects = await _get("/projects")
    project_id = None
    for p in projects:
        if p["projectKey"] == project_key:
            project_id = p["id"]
            break

    if project_id is None:
        return [{"error": f"プロジェクトキー '{project_key}' が見つかりません。"}]

    params: dict[str, Any] = {
        "projectId[]": project_id,
        "count": min(count, 100),
        "offset": offset,
    }
    if keyword:
        params["keyword"] = keyword
    if assignee_id:
        params["assigneeId[]"] = assignee_id
    if status_id:
        params["statusId[]"] = status_id
    if priority_id:
        params["priorityId[]"] = priority_id

    issues = await _get("/issues", **params)

    space = _SPACE
    return [
        {
            "id": i["id"],
            "issueKey": i["issueKey"],
            "summary": i["summary"],
            "status": i["status"]["name"],
            "priority": i["priority"]["name"],
            "assignee": i["assignee"]["name"] if i.get("assignee") else None,
            "dueDate": i.get("dueDate"),
            "url": f"https://{space}.backlog.jp/view/{i['issueKey']}",
        }
        for i in issues
    ]


@mcp.tool()
async def backlog_get_issue(issue_key: str) -> dict[str, Any]:
    """
    Backlog のチケット詳細を取得する。

    Args:
        issue_key: チケットキー（例: "MYPROJECT-123"）。必須。
    """
    encoded_key = quote(issue_key, safe="")
    issue = await _get(f"/issues/{encoded_key}")

    space = _SPACE
    return {
        "id": issue["id"],
        "issueKey": issue["issueKey"],
        "summary": issue["summary"],
        "description": issue.get("description", ""),
        "status": issue["status"]["name"],
        "priority": issue["priority"]["name"],
        "assignee": issue["assignee"]["name"] if issue.get("assignee") else None,
        "startDate": issue.get("startDate"),
        "dueDate": issue.get("dueDate"),
        "createdUser": issue["createdUser"]["name"],
        "created": issue["created"],
        "updated": issue["updated"],
        "url": f"https://{space}.backlog.jp/view/{issue['issueKey']}",
    }


@mcp.tool()
async def backlog_create_issue(
    project_key: str,
    summary: str,
    issue_type_id: int,
    description: Optional[str] = None,
    priority_id: int = 3,
    assignee_id: Optional[int] = None,
    due_date: Optional[str] = None,
) -> dict[str, Any]:
    """
    Backlog に新しいチケットを作成する。

    Args:
        project_key: BacklogプロジェクトキーID（例: "MYPROJECT"）。必須。
        summary: チケットの件名。必須。
        issue_type_id: 種別ID。必須。（プロジェクトの種別IDは事前に確認が必要）
        description: チケットの詳細説明。省略可。
        priority_id: 優先度ID（2=高, 3=中, 4=低）。デフォルト3（中）。
        assignee_id: 担当者のバックログユーザーID。省略可。
        due_date: 期限日（YYYY-MM-DD形式）。省略可。
    """
    # プロジェクトキーからIDを取得する
    projects = await _get("/projects")
    project_id = None
    for p in projects:
        if p["projectKey"] == project_key:
            project_id = p["id"]
            break

    if project_id is None:
        return {"error": f"プロジェクトキー '{project_key}' が見つかりません。"}

    body: dict[str, Any] = {
        "projectId": project_id,
        "summary": summary,
        "issueTypeId": issue_type_id,
        "priorityId": priority_id,
    }
    if description:
        body["description"] = description
    if assignee_id:
        body["assigneeId"] = assignee_id
    if due_date:
        body["dueDate"] = due_date

    issue = await _post("/issues", body)

    space = _SPACE
    return {
        "id": issue["id"],
        "issueKey": issue["issueKey"],
        "summary": issue["summary"],
        "url": f"https://{space}.backlog.jp/view/{issue['issueKey']}",
    }


@mcp.tool()
async def backlog_update_issue(
    issue_key: str,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    status_id: Optional[int] = None,
    priority_id: Optional[int] = None,
    assignee_id: Optional[int] = None,
    due_date: Optional[str] = None,
) -> dict[str, Any]:
    """
    Backlog のチケットを更新する。

    Args:
        issue_key: チケットキー（例: "MYPROJECT-123"）。必須。
        summary: 新しい件名。省略可（変更しない場合）。
        description: 新しい説明。省略可（変更しない場合）。
        status_id: 新しい状態ID（1=未対応, 2=処理中, 3=処理済み, 4=完了）。省略可。
        priority_id: 新しい優先度ID（2=高, 3=中, 4=低）。省略可。
        assignee_id: 新しい担当者のバックログユーザーID。省略可。
        due_date: 新しい期限日（YYYY-MM-DD形式）。省略可。
    """
    body: dict[str, Any] = {}
    if summary is not None:
        body["summary"] = summary
    if description is not None:
        body["description"] = description
    if status_id is not None:
        body["statusId"] = status_id
    if priority_id is not None:
        body["priorityId"] = priority_id
    if assignee_id is not None:
        body["assigneeId"] = assignee_id
    if due_date is not None:
        body["dueDate"] = due_date

    encoded_key = quote(issue_key, safe="")
    issue = await _patch(f"/issues/{encoded_key}", body)

    space = _SPACE
    return {
        "id": issue["id"],
        "issueKey": issue["issueKey"],
        "summary": issue["summary"],
        "status": issue["status"]["name"],
        "url": f"https://{space}.backlog.jp/view/{issue['issueKey']}",
    }


@mcp.tool()
async def backlog_add_comment(
    issue_key: str,
    content: str,
) -> dict[str, Any]:
    """
    Backlog のチケットにコメントを追加する。

    Args:
        issue_key: チケットキー（例: "MYPROJECT-123"）。必須。
        content: コメント本文（Markdown使用可）。必須。
    """
    encoded_key = quote(issue_key, safe="")
    comment = await _post(
        f"/issues/{encoded_key}/comments",
        {"content": content},
    )

    space = _SPACE
    return {
        "id": comment["id"],
        "content": comment["content"],
        "createdUser": comment["createdUser"]["name"],
        "created": comment["created"],
        "issueUrl": f"https://{space}.backlog.jp/view/{issue_key}",
    }


# ── エントリポイント ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
