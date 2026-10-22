"""Watch a folder (D-017): every new form that lands in it goes to review by
itself, without anyone pressing "import".

The server looks at the folder every INTERVAL seconds with the same rules as
"import from folder" (inbox.scan): documents only, not empty or too large, not
still being written, never one already imported. New files of the day go into
one batch per day ("מעקב תיקייה dd/mm"), and that batch is processed like an
upload. The setting is kept in DATA_DIR/watch.json, so a restart resumes it.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from vera import inbox
from vera.db import BatchRow

log = logging.getLogger(__name__)

INTERVAL = 20  # seconds between looks at the folder


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class FolderWatch:
    def __init__(self, bs, interval: float = INTERVAL):
        self.bs = bs
        self.interval = interval
        self.path = Path(bs.s.data_dir) / "watch.json"
        self.state = self._load()
        self.status = {"last_check": None, "error": None, "added_today": 0}
        self._task: asyncio.Task | None = None

    # --- setting ---

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"enabled": False}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def get(self) -> dict:
        return {
            "enabled": self.state.get("enabled", False),
            "folder": self.state.get("folder", ""),
            "today_only": self.state.get("today_only", True),
            "recursive": self.state.get("recursive", False),
            "batch_id": self.state.get("batch_id"),
            "interval": self.interval,
            **self.status,
        }

    def set(
        self,
        folder: str,
        enabled: bool,
        user: str,
        today_only: bool = True,
        recursive: bool = False,
    ) -> dict:
        """Turn watching on (the folder must be readable now) or off."""
        if enabled:
            inbox.scan(self.bs, folder, today_only, recursive)  # raises InboxError
        self.state.update(
            enabled=enabled,
            folder=folder,
            today_only=today_only,
            recursive=recursive,
            user=user,
        )
        self.status["error"] = None
        self._save()
        with self.bs.sv.repo.session() as s:
            self.bs.audit(
                s, user, "watch_on" if enabled else "watch_off", folder or "-"
            )
            s.commit()
        return self.get()

    # --- the loop ---

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            if self.state.get("enabled"):
                try:
                    await self.tick()
                except Exception as e:  # the loop must outlive a bad look
                    log.warning("folder watch: %s", e)
                    self.status["error"] = str(e)[:300]
            await asyncio.sleep(self.interval)

    async def tick(self) -> dict | None:
        """One look at the folder; new forms go into today's watch batch and
        are processed. Returns what was added, or None."""
        st = self.state
        preview = await asyncio.to_thread(
            inbox.scan,
            self.bs,
            st["folder"],
            st.get("today_only", True),
            st.get("recursive", False),
        )
        self.status.update(last_check=_now().isoformat(), error=None)
        chosen = [f for f in preview["files"] if f["relevant"]]
        if not chosen:
            return None
        batch_id = self._todays_batch()
        user = st.get("user", "lab")
        added = await asyncio.to_thread(
            inbox.add_chosen, self.bs, batch_id, st["folder"], chosen, user
        )
        if added:
            self.status["added_today"] += len(added)
            await self.bs.sv.jobs.submit(self.bs.process_batch, batch_id)
            log.info("folder watch: %d new file(s) -> batch %s", len(added), batch_id)
        return {"batch_id": batch_id, "added": added}

    def _todays_batch(self) -> str:
        """One batch per day for watched files; a new one if today's was
        already exported (locked)."""
        today = _now().date().isoformat()
        bid = self.state.get("batch_id")
        if bid and self.state.get("batch_day") == today:
            with self.bs.sv.repo.session() as s:
                b = s.get(BatchRow, bid)
                if b is not None and not b.locked:
                    return bid
        if self.state.get("batch_day") != today:
            self.status["added_today"] = 0
        d = _now()
        batch = self.bs.create_batch(
            f"מעקב תיקייה {d:%d/%m}", None, self.state.get("user", "lab")
        )
        self.state.update(batch_id=batch.id, batch_day=today)
        self._save()
        return batch.id
