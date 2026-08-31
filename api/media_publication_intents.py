from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass


class MediaPublicationIntentConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MediaPublicationIntent:
    id: str
    media_id: str
    requested_by: int | None
    request_idempotency_key: str
    status: str
    transcript_version_id: str | None
    publication_index_job_id: str | None
    error_code: str | None
    created_at: int
    updated_at: int
    completed_at: int | None


def _intent(row: sqlite3.Row) -> MediaPublicationIntent:
    return MediaPublicationIntent(**dict(row))


class MediaPublicationIntentStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def load(self, intent_id: str) -> MediaPublicationIntent:
        row = self.conn.execute(
            "SELECT * FROM media_publication_requests WHERE id=?", (intent_id,)
        ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return _intent(row)

    def create(
        self,
        *,
        media_id: str,
        requested_by: int,
        request_idempotency_key: str,
        now: int,
    ) -> MediaPublicationIntent:
        replay = self.conn.execute(
            "SELECT * FROM media_publication_requests WHERE request_idempotency_key=?",
            (request_idempotency_key,),
        ).fetchone()
        if replay is not None:
            if replay["media_id"] != media_id or replay["requested_by"] != requested_by:
                raise MediaPublicationIntentConflict("idempotency_conflict")
            return _intent(replay)
        active = self.conn.execute(
            """SELECT id FROM media_publication_requests
               WHERE media_id=? AND status IN ('pending_transcription','ready_to_publish','publishing')""",
            (media_id,),
        ).fetchone()
        if active is not None:
            raise MediaPublicationIntentConflict("active_request_exists")
        intent_id = str(uuid.uuid4())
        try:
            self.conn.execute(
                """INSERT INTO media_publication_requests(
                       id,media_id,requested_by,request_idempotency_key,status,created_at,updated_at
                   ) VALUES (?,?,?,?,'pending_transcription',?,?)""",
                (intent_id, media_id, requested_by, request_idempotency_key, now, now),
            )
        except sqlite3.IntegrityError as exc:
            replay = self.conn.execute(
                "SELECT * FROM media_publication_requests WHERE request_idempotency_key=?",
                (request_idempotency_key,),
            ).fetchone()
            if replay is not None and replay["media_id"] == media_id and replay["requested_by"] == requested_by:
                return _intent(replay)
            raise MediaPublicationIntentConflict("active_request_exists") from exc
        self.conn.commit()
        return self.load(intent_id)

    def cancel(self, intent_id: str, *, now: int) -> MediaPublicationIntent:
        """Cancel an intent that has not yet entered the formal publication stage.

        Only intents in ``pending_transcription`` / ``ready_to_publish`` can be
        cancelled; this returns the media to the not-yet-published state and
        allows a fresh publish intent afterwards (cancelled intents are not
        active). Intents already ``publishing`` / ``published`` are rejected.

        Unlike the other store methods this does NOT commit: the caller
        composes cancellation with the transcription-job cancellation and the
        media reset inside one transaction and owns the commit/rollback.
        """
        changed = self.conn.execute(
            """UPDATE media_publication_requests
               SET status='cancelled',completed_at=?,updated_at=?
               WHERE id=? AND status IN ('pending_transcription','ready_to_publish')""",
            (now, now, intent_id),
        ).rowcount
        if changed != 1:
            raise MediaPublicationIntentConflict("intent_not_cancellable")
        return self.load(intent_id)

    def mark_publishing(
        self,
        intent_id: str,
        *,
        transcript_version_id: str,
        publication_index_job_id: str,
        now: int,
    ) -> MediaPublicationIntent:
        changed = self.conn.execute(
            """UPDATE media_publication_requests
               SET status='publishing',transcript_version_id=?,publication_index_job_id=?,
                   error_code=NULL,updated_at=?
               WHERE id=? AND status IN ('pending_transcription','ready_to_publish','failed')""",
            (transcript_version_id, publication_index_job_id, now, intent_id),
        ).rowcount
        if changed != 1:
            raise MediaPublicationIntentConflict("invalid_status_transition")
        self.conn.commit()
        return self.load(intent_id)

    def mark_published(self, intent_id: str, *, now: int) -> MediaPublicationIntent:
        changed = self.conn.execute(
            """UPDATE media_publication_requests
               SET status='published',updated_at=?,completed_at=?
               WHERE id=? AND status='publishing'""",
            (now, now, intent_id),
        ).rowcount
        if changed != 1:
            raise MediaPublicationIntentConflict("invalid_status_transition")
        self.conn.commit()
        return self.load(intent_id)
