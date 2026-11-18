"""Mock cloud drive for case files (spec 3.3 Document management).

Stands in for Google Drive / OneDrive: beneficiaries upload receipts and other
case files, Intake attaches them to a case, Records searches them (through the
records RAG index) and Refunds reads them when reviewing a claim.

Files live under `<database folder>/drive/<customer_id>/<file_id>`, with
metadata in the `files` table. Putting the folder next to the database keeps
red-team sandboxes and tests away from live files, the same way ChromaDB is.

Upload rules (each is tested in RT-28):
- The beneficiary's file name is display text only. It never becomes part of a
  path, so a name like `../../app/main.py` cannot escape the drive folder.
- Only a short list of types is accepted, the extension must match the file's
  leading bytes for PDF and images, and files are capped at 1 MB.
- Text is extracted only from plain-text types. PDFs and images are stored but
  not shown to the model.

Uploaded text is untrusted. screen_text() looks for instruction-like phrases
before a file is shown to the model; a flagged file is withheld and the
withholding is audit-logged. The screen is pattern based, so a paraphrase can
get past it. Payment rules never depend on it: the guardrail still applies.
"""
import hashlib
import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import records_store
from app.audit import log_event
from app.db import now_ts

MAX_BYTES = 1_000_000
MAX_TEXT_FOR_MODEL = 4000
MAX_NAME = 120
TEXT_TYPES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json"}
BINARY_TYPES = {".pdf": ("application/pdf", (b"%PDF-",)),
                ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
                ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
                ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",))}
ALLOWED_EXTENSIONS = tuple(TEXT_TYPES) + tuple(BINARY_TYPES)
CUSTOMER_ID = re.compile(r"^[A-Z]+-\d{3,}$")

# Phrases that try to instruct the model rather than describe care received.
SCREEN_PATTERNS = [
    ("ignore earlier instructions", r"ignore (?:all|any|previous|prior|the above)"),
    ("disregard instructions", r"disregard (?:all|any|previous|prior|the)"),
    ("system override", r"system (?:override|prompt|message)"),
    ("role change", r"you are now|act as|master admin|authori[sz]ed as"),
    ("approval order", r"approve (?:immediately|this|the claim|in full)|disburse"),
    ("new instructions", r"new instructions?"),
    ("fake chat turn", r"(?:^|\n)\s*(?:assistant|system|developer)\s*:|<\s*/?\s*(?:system|instructions?)\s*>"),
]


class DriveError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def drive_dir(conn: sqlite3.Connection) -> Path:
    """The drive folder that belongs to this connection's database."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main" and row[2]:
            return Path(row[2]).resolve().parent / "drive"
    raise DriveError("NO_DRIVE", "In-memory databases have no drive folder.")


def reset(conn: sqlite3.Connection) -> None:
    try:
        shutil.rmtree(drive_dir(conn), ignore_errors=True)
    except DriveError:
        pass


def display_name(name: str) -> str:
    """Keep only the last path segment and printable characters. Display only."""
    base = re.split(r"[\\/]", str(name or ""))[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip().lstrip(".")
    return base[:MAX_NAME] or "file"


def screen_text(text: str) -> List[str]:
    """Instruction-like phrases found in uploaded text (empty if none)."""
    norm = re.sub(r"[ \t]+", " ", (text or "").lower())
    return [label for label, pattern in SCREEN_PATTERNS if re.search(pattern, norm)]


def _check(name: str, data: bytes) -> Dict[str, Any]:
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DriveError("FILE_TYPE_NOT_ALLOWED",
                         f"Only {', '.join(ALLOWED_EXTENSIONS)} files can be uploaded.")
    if not data:
        raise DriveError("FILE_EMPTY", "The file is empty.")
    if len(data) > MAX_BYTES:
        raise DriveError("FILE_TOO_LARGE", f"Files are limited to {MAX_BYTES // 1_000_000} MB.")
    if ext in TEXT_TYPES:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise DriveError("FILE_CONTENT_MISMATCH", f"A {ext} file must be UTF-8 text.")
        if "\x00" in text:
            raise DriveError("FILE_CONTENT_MISMATCH", f"A {ext} file must be plain text.")
        return {"content_type": TEXT_TYPES[ext], "text": text}
    content_type, magics = BINARY_TYPES[ext]
    if not data.startswith(magics):
        raise DriveError("FILE_CONTENT_MISMATCH", f"The file's contents are not a {ext} file.")
    return {"content_type": content_type, "text": None}


def store_file(conn: sqlite3.Connection, customer_id: str, name: str, data: bytes,
               uploaded_by: str) -> Dict[str, Any]:
    """Save an upload for `customer_id` (the caller must already be authenticated as them)."""
    if not CUSTOMER_ID.match(customer_id or "") or not conn.execute(
            "SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
        raise DriveError("UNKNOWN_BENEFICIARY", f"Beneficiary {customer_id!r} does not exist.")
    shown = display_name(name)
    checked = _check(shown, data)
    file_id = f"FILE-{uuid.uuid4().hex[:10].upper()}"
    folder = drive_dir(conn) / customer_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / file_id).write_bytes(data)
    row = {
        "id": file_id, "customer_id": customer_id, "case_id": None, "name": shown,
        "content_type": checked["content_type"], "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "text": checked["text"],
        "uploaded_by": uploaded_by, "uploaded_at": now_ts(),
    }
    conn.execute(
        "INSERT INTO files (id, customer_id, case_id, name, content_type, size, sha256, text, uploaded_by, uploaded_at) "
        "VALUES (:id, :customer_id, :case_id, :name, :content_type, :size, :sha256, :text, :uploaded_by, :uploaded_at)",
        row)
    conn.commit()
    log_event(conn, uploaded_by, "DRIVE_UPLOAD", "ALLOWED",
              {"file_id": file_id, "name": shown, "size": len(data), "sha256": row["sha256"],
               "screen_flags": screen_text(checked["text"] or "")})
    records_store.index_file(conn, file_id)
    return public(row)


def public(row: Any) -> Dict[str, Any]:
    """File metadata safe to return to agents and the UI (no disk path, no text)."""
    r = dict(row)
    return {k: r.get(k) for k in ("id", "customer_id", "case_id", "name", "content_type", "size", "sha256",
                                  "uploaded_by", "uploaded_at")} | {"has_text": r.get("text") is not None}


def get_row(conn: sqlite3.Connection, file_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()


def read_bytes(conn: sqlite3.Connection, row: sqlite3.Row) -> bytes:
    path = (drive_dir(conn) / row["customer_id"] / row["id"]).resolve()
    if drive_dir(conn).resolve() not in path.parents:  # defence in depth; ids come from our own table
        raise DriveError("FILE_NOT_FOUND", "File is outside the drive folder.")
    return path.read_bytes()


def list_files(conn: sqlite3.Connection, customer_id: str, case_id: Optional[str] = None) -> List[Dict[str, Any]]:
    sql, args = "SELECT * FROM files WHERE customer_id = ?", [customer_id]
    if case_id:
        sql += " AND case_id = ?"
        args.append(case_id)
    return [public(r) for r in conn.execute(sql + " ORDER BY uploaded_at DESC, id", args)]


def attach(conn: sqlite3.Connection, file_id: str, case_id: str) -> Dict[str, Any]:
    row = get_row(conn, file_id)
    if row["case_id"] and row["case_id"] != case_id:
        raise DriveError("FILE_ALREADY_ATTACHED", f"{file_id} is already attached to {row['case_id']}.")
    conn.execute("UPDATE files SET case_id = ? WHERE id = ?", (case_id, file_id))
    conn.commit()
    records_store.index_file(conn, file_id)
    return public(get_row(conn, file_id))


def for_model(conn: sqlite3.Connection, file_id: str, actor: str) -> Dict[str, Any]:
    """What the reviewing agent may pass to the model: screened, truncated text."""
    row = get_row(conn, file_id)
    out = public(row)
    text = row["text"]
    if text is None:
        return out | {"text": None, "withheld": False, "note": "Not a text file; contents not shown to the model."}
    flags = screen_text(text)
    if flags:
        log_event(conn, actor, "DRIVE_FILE_WITHHELD", "BLOCKED",
                  {"file_id": file_id, "flags": flags, "reason": "instruction-like text in an uploaded file"},
                  row["case_id"])
        return out | {"text": None, "withheld": True, "flags": flags,
                      "note": "Withheld from the model: the file contains instruction-like text."}
    return out | {"text": text[:MAX_TEXT_FOR_MODEL], "withheld": False, "truncated": len(text) > MAX_TEXT_FOR_MODEL}
