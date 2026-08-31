import signal
import sys
import threading

from sqlalchemy import text, update

from app.config import settings
from app.db import session_scope
from app.models import VoiceMessage
from app.pipeline import IntakePipeline
from app.services.activity_log import log as log_activity
from app.services.audio_store import AudioStore
from app.services.commit import reconcile_stuck_commit
from app.services.gemini_command_extractor import GeminiCommandExtractor
from app.services.gemini_transcriber import GeminiTranscriber

# Windows consoles/log redirection default to the legacy cp1252 codepage.
# Without this, print()ing an exception that echoes back Arabic transcript
# or phone text (a routine occurrence here) raises UnicodeEncodeError,
# uncaught, which kills the whole worker loop rather than just that message.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class Worker:
    def __init__(self, pipeline: IntakePipeline):
        self.pipeline = pipeline
        self._stop = threading.Event()

    def stop(self, *_):
        self._stop.set()

    def recover_stale(self, s):
        s.execute(text("""
            UPDATE voice_message
            SET status = 'failed', error = 'worker timeout'
            WHERE status = 'transcribing'
              AND claimed_at < now() - (:mins || ' minutes')::interval
        """), {"mins": settings.worker_stale_minutes})

    def claim_one(self):
        with session_scope() as s:
            self.recover_stale(s)
            vid = s.execute(text("""
                SELECT id FROM voice_message
                WHERE status IN ('received','failed') AND attempts < :max
                ORDER BY received_at
                FOR UPDATE SKIP LOCKED LIMIT 1
            """), {"max": settings.worker_max_attempts}).scalar()
            if vid is None:
                return None
            s.execute(update(VoiceMessage).where(VoiceMessage.id == vid)
                      .values(status="transcribing", claimed_at=text("now()"),
                              attempts=VoiceMessage.attempts + 1))
            return vid

    def mark_failed(self, vid: int, err: str):
        with session_scope() as s:
            s.execute(update(VoiceMessage).where(VoiceMessage.id == vid)
                      .values(status="failed", error=err[:2000]))
            log_activity(s, "error", f"processing voice message {vid} "
                        f"failed: {err[:500]}", level="error",
                        voice_message_id=vid)

    def reconcile_stuck_commits(self):
        """Re-drives PendingRequest rows that have been stuck in
        "committing" longer than commit_reconcile_stale_seconds - the
        commit saga's crash-recovery sweep (see
        app.services.commit.reconcile_stuck_commit). The staleness gate
        exists so this never races a request that's still genuinely
        inside its own synchronous accept() call, only ones that crashed
        or are taking unreasonably long. FOR UPDATE SKIP LOCKED, same
        claim pattern as claim_one() above, so this is safe to run
        alongside another worker process.
        """
        with session_scope() as s:
            req_id = s.execute(text("""
                SELECT id FROM pending_request
                WHERE status = 'committing'
                  AND decided_at < now() - (:secs || ' seconds')::interval
                ORDER BY decided_at
                FOR UPDATE SKIP LOCKED LIMIT 1
            """), {"secs": settings.commit_reconcile_stale_seconds}).scalar()
            if req_id is None:
                return
            reconcile_stuck_commit(s, req_id)

    def run(self):
        print("worker started", flush=True)
        while not self._stop.is_set():
            vid = self.claim_one()
            if vid is None:
                try:
                    self.reconcile_stuck_commits()
                except Exception as e:
                    print(f"reconciliation sweep failed: {e}", flush=True)
                self._stop.wait(settings.worker_poll_seconds)
                continue
            try:
                self.pipeline.process(vid)
                print(f"processed {vid}", flush=True)
            except Exception as e:
                print(f"FAILED {vid}: {e}", flush=True)
                self.mark_failed(vid, str(e))


def main():
    stt = GeminiTranscriber()
    command_extractor = GeminiCommandExtractor()
    w = Worker(IntakePipeline(stt, AudioStore(), command_extractor))
    signal.signal(signal.SIGINT, w.stop)
    signal.signal(signal.SIGTERM, w.stop)
    w.run()


if __name__ == "__main__":
    main()
