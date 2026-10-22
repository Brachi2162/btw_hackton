"""Import a folder of scans: the secretary gives a folder, the server picks
the relevant files itself (no browser upload).

The scanner folder is mounted into the container at INBOX_DIR (/inbox),
from the host path SCAN_FOLDER (docker-compose.yml). A path typed in the UI
may be the full Windows path inside that folder, a subfolder name, or empty
for the whole folder.

Relevant = a document by its content (PDF / TIFF / images, never by
extension), not empty and not over the upload limit, not a temp or system
file, not still being written, and not already imported (same SHA-256).
Optionally only files changed today. The folder may also be a web address
that lists the scanner's files (inbox_url.py), with the same rules.
"""

import hashlib
import re
from itertools import islice
import time
from urllib.parse import unquote
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlmodel import select

from vera.batch import MAX_UPLOAD_BYTES
from vera.db import BatchRow, DocumentRow
from vera.inbox_url import UrlFolder, is_url, scan_url
from vera.pipeline.ingest import UnsupportedFileError, sniff_kind

SKIP_NAMES = re.compile(
    r"^(~\$|\.|thumbs\.db$|desktop\.ini$)|\.(tmp|part|crdownload)$", re.I
)
SETTLE_SECONDS = 5  # a file modified this recently may still be written
MAX_FILES = 500
MAX_SCAN = 5000  # directory entries looked at, at most

REASON_HE = {
    "ok": "ייקלט",
    "duplicate": "כבר נקלט",
    "not_document": "לא טופס (לא PDF או תמונה)",
    "empty": "קובץ ריק",
    "too_large": "גדול מ-50MB",
    "system": "קובץ זמני או קובץ מערכת",
    "writing": "עדיין נכתב, ייקלט בפעם הבאה",
    "old": "לא מהיום",
    "no_date": "השרת לא מציין מתי הקובץ נוצר",
    "fetch_failed": "השרת לא נתן להוריד אותו",
}


class InboxError(ValueError):
    """A folder the server can't use (message in Hebrew, for the UI)."""


def _norm(p: str) -> str:
    """C:\\a\\b, "C:/a/b/", file:///C:/a%20b -> C:/a/b (comparable form)."""
    p = p.strip().strip('"').strip("'").strip()
    if p.lower().startswith("file:"):
        p = unquote(re.sub(r"^file:/*", "", p, flags=re.I))
    p = p.replace("\\", "/").rstrip("/")
    return re.sub(r"^([a-zA-Z]:)$", r"\1/", p) if p else p


def resolve(root: Path, host_root: str, text: str) -> Path:
    """Map what the user typed to a folder under the mounted root."""
    t = _norm(text)
    host = _norm(host_root)
    if re.match(r"^(https?|ftp)://|^//", t, re.I):
        raise InboxError(
            "אפשר להזין תיקייה במחשב הזה (למשל C:\\Scans) או כתובת http של "
            "שרת שמציג את רשימת הקבצים. תיקיית רשת (\\\\server) צריך קודם "
            "להעתיק לתיקייה מקומית"
        )
    if not t:
        rel = ""
    elif host and t.lower().startswith(host.lower()):
        rel = t[len(host) :]
    elif t.lower().startswith(_norm(str(root)).lower()):
        rel = t[len(_norm(str(root))) :]
    elif re.match(r"^[a-zA-Z]:/|^/|^//", t):
        raise InboxError(
            f"לשרת אין גישה לתיקייה {text.strip()}: הוא רואה רק את "
            f"{host_root or root}. כדי לאפשר כל תיקייה בכונן, מגדירים "
            "SCAN_FOLDER=C:\\ בקובץ .env ומפעילים מחדש"
        )
    else:
        rel = t
    parts = [p for p in PurePosixPath(rel.strip("/")).parts if p not in ("", ".")]
    if ".." in parts:
        raise InboxError("נתיב לא חוקי")
    folder = root.joinpath(*parts).resolve()
    if folder != root.resolve() and root.resolve() not in folder.parents:
        raise InboxError("נתיב לא חוקי")
    if not folder.is_dir():
        raise InboxError(f"התיקייה {text.strip() or root} לא קיימת")
    return folder


def _imported_hashes(bs) -> set[str]:
    with bs.sv.repo.session() as s:
        rows = s.exec(
            select(DocumentRow.sha256)
            .join(BatchRow, DocumentRow.batch_id == BatchRow.id)
            .where(BatchRow.demo == False)  # noqa: E712 (SQL expression)
        ).all()
    return set(rows)


def scan(bs, text: str, today_only: bool = False, recursive: bool = False) -> dict:
    """Preview: every file with whether it will be imported and why not.
    `text` is a folder path, or a folder's web address (inbox_url; a listing
    is always walked into its subfolders)."""
    if is_url(text):
        return scan_url(bs, text, today_only, _imported_hashes(bs), REASON_HE)
    root = Path(bs.s.inbox_dir)
    if not root.is_dir():
        raise InboxError(
            "תיקיית הסורק לא מחוברת לשרת. מגדירים SCAN_FOLDER בקובץ .env "
            "ומפעילים מחדש"
        )
    if not _norm(text) and re.match(r"^[a-zA-Z]:/?$", _norm(bs.s.scan_folder_host)):
        raise InboxError("צריך להזין נתיב לתיקייה, למשל C:\\Scans\\2026-10-22")
    folder = resolve(root, bs.s.scan_folder_host, text)
    seen = _imported_hashes(bs)
    now = time.time()
    today = datetime.now(timezone.utc).astimezone().date()
    files, batch_hashes = [], set()
    walker = folder.rglob("*") if recursive else folder.glob("*")
    # bounded: a huge folder must not hang the request
    for p in sorted(islice(walker, MAX_SCAN), key=lambda x: str(x).lower()):
        if not p.is_file() or len(files) >= MAX_FILES:
            continue
        st = p.stat()
        reason, sha = "ok", None
        if SKIP_NAMES.search(p.name):
            reason = "system"
        elif st.st_size == 0:
            reason = "empty"
        elif st.st_size > MAX_UPLOAD_BYTES:
            reason = "too_large"
        elif now - st.st_mtime < SETTLE_SECONDS:
            reason = "writing"
        elif (
            today_only
            and datetime.fromtimestamp(st.st_mtime, timezone.utc).astimezone().date()
            != today
        ):
            reason = "old"
        else:
            with p.open("rb") as f:
                head = f.read(16)
            try:
                sniff_kind(head)
            except UnsupportedFileError:
                reason = "not_document"
            else:
                sha = hashlib.sha256(p.read_bytes()).hexdigest()
                if sha in seen or sha in batch_hashes:
                    reason = "duplicate"
                batch_hashes.add(sha)
        files.append(
            {
                "path": p.relative_to(folder).as_posix(),
                "size": st.st_size,
                "modified": datetime.fromtimestamp(
                    st.st_mtime, timezone.utc
                ).isoformat(),
                "relevant": reason == "ok",
                "reason": reason,
                "reason_he": REASON_HE[reason],
            }
        )
    return {
        "folder": _norm(text) or _norm(bs.s.scan_folder_host) or str(root),
        "relevant": sum(f["relevant"] for f in files),
        "skipped": sum(not f["relevant"] for f in files),
        "files": files,
    }


def import_folder(
    bs, text: str, user: str, today_only: bool = False, recursive: bool = False
) -> dict:
    """Create a batch from the relevant files of a folder. The caller starts
    processing (the same job as an upload)."""
    preview = scan(bs, text, today_only, recursive)
    chosen = [f for f in preview["files"] if f["relevant"]]
    if not chosen:
        return {**preview, "batch_id": None, "added": []}
    if is_url(text):
        remote = UrlFolder(text)

        def read(f):
            return remote.fetch(f["url"])

    else:
        folder = resolve(Path(bs.s.inbox_dir), bs.s.scan_folder_host, text)

        def read(f):
            return (folder / f["path"]).read_bytes()

    d = datetime.now(timezone.utc).astimezone()
    batch = bs.create_batch(f"בוקר {d:%d/%m}", None, user)
    added = []
    for f in chosen:
        try:
            added.append(bs.add_file(batch.id, f["path"], read(f), user))
        except (UnsupportedFileError, InboxError) as e:  # changed since the preview
            f["relevant"], f["reason_he"] = False, str(e)
    return {**preview, "batch_id": batch.id, "added": added}
