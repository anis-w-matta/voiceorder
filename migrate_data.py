"""One-off data cutover: copies salesman/voice_message/pending_request/
pending_request_line/activity_log from the real Postgres `public` schema
into the new SQL Server database's `dbo` schema, as part of the
Postgres -> SQL Server 2025 migration.

Run ONCE, against a freshly-migrated (empty) SQL Server database - i.e.
after `alembic upgrade head` has created the tables there, before the
service is ever pointed at SQL Server for real traffic. Not idempotent (no
ON CONFLICT/MERGE handling): re-running it against a database that already
has rows will raise a primary-key violation rather than silently
duplicating or skipping - a fresh cutover should only ever run once, and a
failed run should be re-tried on a re-truncated destination, not resumed.

id (surrogate autoincrement PKs on voice_message/pending_request/
pending_request_line/activity_log) is intentionally NOT copied - SQL
Server's IDENTITY assigns fresh ones. This means voice_message_id/
request_id foreign keys can't just be copied verbatim either: each table
is copied in FK-dependency order (voice_message before pending_request
before pending_request_line) and the old-id -> new-id mapping from each
step is used to translate the next table's foreign keys. salesman.login_id
is a real natural-key primary key, copied as-is.

JSON columns (candidates/line_flags/resolution_meta/attributes/qualifiers/
intents/raw_model_output/flags/transcript_attempts/languages/segments/
details) come back from psycopg already deserialized as Python list/dict -
they need re-serializing with json.dumps() for pyodbc, which (unlike
psycopg for JSONB) does not do this automatically for a generic JSON
column.

Requires psycopg installed (to read from Postgres) even though the
service's own requirements.txt no longer needs it -
`pip install psycopg[binary]` into the venv before running this once.

Run with the venv active, after editing SRC/DST below for the real
source/destination credentials: python migrate_data.py
"""
import json

from sqlalchemy import create_engine, text

SRC = "postgresql+psycopg://voiceorder:changeme@localhost/voiceorder"
DST = ("mssql+pyodbc://voiceorder:changeme@localhost/voiceorder"
      "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes")

_JSON_COLS = {
    "voice_message": {"transcript_attempts", "languages", "segments"},
    "pending_request": {"intents", "raw_model_output", "flags"},
    "pending_request_line": {"candidates", "line_flags", "resolution_meta",
                             "attributes", "qualifiers"},
    "activity_log": {"details"},
}


def _prep(table: str, cols: list[str], row) -> dict:
    d = dict(zip(cols, row))
    for c in _JSON_COLS.get(table, ()):
        if c in d and d[c] is not None:
            d[c] = json.dumps(d[c])
    return d


def main():
    src_engine = create_engine(SRC)
    dst_engine = create_engine(DST)

    with src_engine.connect() as src, dst_engine.begin() as dst:
        # salesman: natural key (login_id), no id remapping needed.
        cols = ["login_id", "password_hash", "name", "email", "is_active",
               "role", "created_at"]
        rows = src.execute(text(
            f"SELECT {', '.join(cols)} FROM salesman")).all()
        for row in rows:
            dst.execute(text(
                f"INSERT INTO salesman ({', '.join(cols)}) "
                f"VALUES ({', '.join(f':{c}' for c in cols)})"),
                _prep("salesman", cols, row))
        print(f"salesman: {len(rows)} rows copied")

        # voice_message: surrogate id remapped (pending_request references
        # it via voice_message_id).
        cols = ["id", "phone_raw", "audio_path", "duration_sec", "transcript",
                "normalized_transcript", "transcript_quality",
                "transcription_disagreement", "transcript_attempts",
                "transcript_conf", "language", "languages", "segments",
                "status", "transcript_source", "error", "attempts",
                "received_at", "claimed_at", "processed_at"]
        rows = src.execute(text(
            f"SELECT {', '.join(cols)} FROM voice_message")).all()
        vm_id_map: dict[int, int] = {}
        insert_cols = [c for c in cols if c != "id"]
        for row in rows:
            d = _prep("voice_message", cols, row)
            old_id = d.pop("id")
            result = dst.execute(text(
                f"INSERT INTO voice_message ({', '.join(insert_cols)}) "
                f"OUTPUT INSERTED.id "
                f"VALUES ({', '.join(f':{c}' for c in insert_cols)})"), d)
            vm_id_map[old_id] = result.scalar()
        print(f"voice_message: {len(rows)} rows copied")

        # pending_request: surrogate id remapped (pending_request_line
        # references it via request_id); voice_message_id translated
        # through vm_id_map.
        cols = ["id", "voice_message_id", "cust_nb", "intents",
                "primary_intent", "target_order_nb", "target_order_type",
                "raw_model_output", "flags", "classification_quality",
                "status", "assigned_to", "claimed_at", "created_at",
                "decided_at", "decided_by", "decision_note",
                "committed_order_nb", "commit_intent_id"]
        rows = src.execute(text(
            f"SELECT {', '.join(cols)} FROM pending_request")).all()
        req_id_map: dict[int, int] = {}
        insert_cols = [c for c in cols if c != "id"]
        for row in rows:
            d = _prep("pending_request", cols, row)
            old_id = d.pop("id")
            d["voice_message_id"] = vm_id_map[d["voice_message_id"]]
            result = dst.execute(text(
                f"INSERT INTO pending_request ({', '.join(insert_cols)}) "
                f"OUTPUT INSERTED.id "
                f"VALUES ({', '.join(f':{c}' for c in insert_cols)})"), d)
            req_id_map[old_id] = result.scalar()
        print(f"pending_request: {len(rows)} rows copied")

        # pending_request_line: request_id translated through req_id_map.
        cols = ["request_id", "line_nb", "raw_text", "raw_lang", "item_nb",
                "item_desc", "qty", "uom", "match_confidence",
                "match_method", "change", "operator_edited", "candidates",
                "category", "line_flags", "resolution_meta", "attributes",
                "qualifiers"]
        rows = src.execute(text(
            f"SELECT {', '.join(cols)} FROM pending_request_line")).all()
        for row in rows:
            d = _prep("pending_request_line", cols, row)
            d["request_id"] = req_id_map[d["request_id"]]
            dst.execute(text(
                f"INSERT INTO pending_request_line ({', '.join(cols)}) "
                f"VALUES ({', '.join(f':{c}' for c in cols)})"), d)
        print(f"pending_request_line: {len(rows)} rows copied")

        # activity_log: voice_message_id/request_id are informational
        # (no FK constraint - see the model) but translated anyway so
        # historical log rows still point at the right migrated entity.
        cols = ["ts", "event_type", "level", "voice_message_id",
                "request_id", "cust_nb", "order_nb", "message", "details"]
        rows = src.execute(text(
            f"SELECT {', '.join(cols)} FROM activity_log")).all()
        for row in rows:
            d = _prep("activity_log", cols, row)
            if d["voice_message_id"] is not None:
                d["voice_message_id"] = vm_id_map.get(d["voice_message_id"])
            if d["request_id"] is not None:
                d["request_id"] = req_id_map.get(d["request_id"])
            dst.execute(text(
                f"INSERT INTO activity_log ({', '.join(cols)}) "
                f"VALUES ({', '.join(f':{c}' for c in cols)})"), d)
        print(f"activity_log: {len(rows)} rows copied")


if __name__ == "__main__":
    main()
