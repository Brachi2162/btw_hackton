"""Scanner folder over HTTP: a scanner or file server that publishes its
folder as a web page listing the files (nginx/Apache/IIS directory listing,
`python -m http.server`). Same rules as a mounted folder (inbox.py): only
documents by content, not empty or too large, not still being written, not
already imported, and optionally only today's.

Safety: only http/https; links are followed only below the given address on
the same host (no wandering off to other servers); bounded depth, file count,
file size and time per request.
"""

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from vera.batch import MAX_UPLOAD_BYTES
from vera.pipeline.ingest import UnsupportedFileError, sniff_kind

MAX_DEPTH = 3
TIMEOUT = 20


def is_url(text: str) -> bool:
    return bool(re.match(r"^\s*https?://", text or "", re.I))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


class UrlFolder:
    """List and fetch the files under one folder address."""

    def __init__(self, url: str, client=None):
        import httpx

        self.base = url.strip().strip('"')
        self._base = urlsplit(self.base)
        self.client = client or httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "VERA"}
        )

    def _inside(self, url: str) -> bool:
        u = urlsplit(url)
        return (
            u.scheme == self._base.scheme
            and u.netloc == self._base.netloc
            and u.path.startswith(self._base.path)
            and not u.query
        )

    def _get(self, url: str):
        from vera.inbox import InboxError

        try:
            r = self.client.get(url)
        except Exception as e:
            raise InboxError(f"אין גישה לכתובת {url} ({type(e).__name__})") from e
        # A redirect may only stay on the same server ("scans" -> "scans/").
        if urlsplit(str(r.url)).netloc != self._base.netloc:
            raise InboxError(f"הכתובת {url} מפנה לשרת אחר")
        if r.status_code in (401, 403):
            raise InboxError(f"השרת לא מרשה לקרוא את {url} (צריך הרשאה)")
        if r.status_code != 200:
            raise InboxError(f"הכתובת {url} החזירה שגיאה {r.status_code}")
        return r

    def list_files(self, max_files: int) -> list[dict]:
        """[{"url", "path", "size", "modified"}], from the listing pages."""
        out, seen = [], set()

        def walk(url: str, depth: int):
            r = self._get(url)
            if "html" not in r.headers.get("content-type", ""):
                from vera.inbox import InboxError

                raise InboxError(
                    f"הכתובת {url} היא לא רשימת קבצים. צריך כתובת של תיקייה "
                    "שהשרת מציג כרשימה"
                )
            parser = _Links()
            parser.feed(r.text)
            for href in parser.hrefs:
                target = urljoin(url, href).split("#")[0]
                if target in seen or not self._inside(target) or target == url:
                    continue
                seen.add(target)
                if target.endswith("/"):
                    if depth < MAX_DEPTH:
                        walk(target, depth + 1)
                elif len(out) < max_files:
                    out.append(self._head(target))

        first = self._get(self.base)
        # The folder is where the listing really is (after a redirect).
        final = str(first.url)
        self.base = final if final.endswith("/") else final.rsplit("/", 1)[0] + "/"
        self._base = urlsplit(self.base)
        walk(final, 0)
        return out

    def _head(self, url: str) -> dict:
        size, modified = None, None
        try:
            r = self.client.head(url)
            if r.status_code == 200:
                if r.headers.get("content-length", "").isdigit():
                    size = int(r.headers["content-length"])
                if r.headers.get("last-modified"):
                    modified = parsedate_to_datetime(r.headers["last-modified"])
        except Exception:  # the GET later says what's wrong
            pass
        return {
            "url": url,
            "path": unquote(urlsplit(url).path[len(self._base.path) :]),
            "size": size,
            "modified": modified,
        }

    def fetch(self, url: str) -> bytes:
        """The file, up to the upload limit (larger: rejected, not truncated)."""
        from vera.inbox import InboxError

        data = bytearray()
        try:
            with self.client.stream("GET", url) as r:
                if r.status_code != 200:
                    raise InboxError(f"{url}: שגיאה {r.status_code}")
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > MAX_UPLOAD_BYTES:
                        raise UnsupportedFileError("הקובץ גדול מ-50MB")
        except (InboxError, UnsupportedFileError):
            raise
        except Exception as e:
            raise InboxError(f"{url}: לא הורד ({type(e).__name__})") from e
        return bytes(data)


def scan_url(bs, url: str, today_only: bool, seen: set[str], reasons: dict) -> dict:
    """Preview of a folder address: the same shape as inbox.scan."""
    from vera.inbox import MAX_FILES, SETTLE_SECONDS, SKIP_NAMES, InboxError

    folder = UrlFolder(url)
    now = datetime.now(timezone.utc)
    today = now.astimezone().date()
    files, batch_hashes = [], set()
    for f in sorted(folder.list_files(MAX_FILES), key=lambda x: x["path"].lower()):
        name = f["path"].rsplit("/", 1)[-1]
        mod = f["modified"]
        reason = "ok"
        if SKIP_NAMES.search(name):
            reason = "system"
        elif f["size"] == 0:
            reason = "empty"
        elif f["size"] is not None and f["size"] > MAX_UPLOAD_BYTES:
            reason = "too_large"
        elif mod is not None and (now - mod).total_seconds() < SETTLE_SECONDS:
            reason = "writing"
        elif today_only and (mod is None or mod.astimezone().date() != today):
            reason = "old" if mod is not None else "no_date"
        else:
            try:
                data = folder.fetch(f["url"])
                if not data:
                    raise UnsupportedFileError("empty")
                sniff_kind(data[:16])
            except InboxError:  # one file the server won't give: skip it only
                reason = "fetch_failed"
            except UnsupportedFileError as e:
                reason = "too_large" if "50MB" in str(e) else "not_document"
                if str(e) == "empty":
                    reason = "empty"
            else:
                sha = hashlib.sha256(data).hexdigest()
                if sha in seen or sha in batch_hashes:
                    reason = "duplicate"
                batch_hashes.add(sha)
        files.append(
            {
                "path": f["path"],
                "url": f["url"],
                "size": f["size"] or 0,
                "modified": mod.isoformat() if mod else None,
                "relevant": reason == "ok",
                "reason": reason,
                "reason_he": reasons[reason],
            }
        )
    return {
        "folder": folder.base,
        "relevant": sum(f["relevant"] for f in files),
        "skipped": sum(not f["relevant"] for f in files),
        "files": files,
    }
