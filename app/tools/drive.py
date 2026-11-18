"""Drive tools: case-file operations on the mock cloud drive (app/drive.py).

Who may call what (see guardrails.AGENT_PERMISSIONS):
- IntakeAgent attaches a beneficiary's uploaded files to their new case.
- RecordsAgent lists and searches the beneficiary's files.
- RefundsAgent reads a file to review the claim; the text is screened first.
Uploads are a human action (POST /me/drive/files), not an agent tool.
"""
from typing import Any, Dict, List

from app import drive, records_store
from app.tools.base import ToolContext, tool


@tool("drive.list_files", "List a beneficiary's case files.", {"customer_id": "string"})
def list_files(ctx: ToolContext, customer_id: str) -> List[Dict[str, Any]]:
    return drive.list_files(ctx.conn, customer_id)


@tool("drive.search_files", "Semantic search (RAG) over a beneficiary's case files. Returns metadata only.",
      {"customer_id": "string", "query": "string"})
def search_files(ctx: ToolContext, customer_id: str, query: str) -> List[Dict[str, Any]]:
    return records_store.search(ctx.conn, customer_id, query, kinds=["file"])


@tool("drive.attach", "Attach an uploaded file to a reimbursement case.", {"file_id": "string", "case_id": "string"})
def attach(ctx: ToolContext, file_id: str, case_id: str) -> Dict[str, Any]:
    return drive.attach(ctx.conn, file_id, case_id)


@tool("drive.get_file", "Read a case file for review. Instruction-like text is withheld.", {"file_id": "string"})
def get_file(ctx: ToolContext, file_id: str) -> Dict[str, Any]:
    return drive.for_model(ctx.conn, file_id, ctx.agent)
