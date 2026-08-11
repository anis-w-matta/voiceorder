from app.models import ActivityLog


def _build(event_type, message, level, voice_message_id, request_id,
          cust_nb, order_nb, details) -> ActivityLog:
    return ActivityLog(
        event_type=event_type, level=level, message=message,
        voice_message_id=voice_message_id, request_id=request_id,
        cust_nb=cust_nb, order_nb=order_nb, details=details or {})


def log(session, event_type: str, message: str, *, level: str = "info",
        voice_message_id: int | None = None, request_id: int | None = None,
        cust_nb: str | None = None, order_nb: str | None = None,
        details: dict | None = None) -> ActivityLog:
    """Write as part of the caller's own transaction.

    Use this for events that describe state the same transaction is also
    writing (a request being drafted, an order being committed): if that
    transaction rolls back, the state it would have described never existed
    either, so rolling the log entry back with it is correct.
    """
    entry = _build(event_type, message, level, voice_message_id, request_id,
                   cust_nb, order_nb, details)
    session.add(entry)
    session.flush()
    return entry


def log_standalone(event_type: str, message: str, *, level: str = "info",
                   voice_message_id: int | None = None,
                   request_id: int | None = None, cust_nb: str | None = None,
                   order_nb: str | None = None,
                   details: dict | None = None) -> None:
    """Write in its own committed transaction, independent of any caller
    transaction that might still roll back.

    Use this for events that record something being *refused* (an update
    attempt outside the buffer state, a blocked injection attempt, a worker
    failure): the calling code is typically about to raise, which would roll
    back a log entry written on the shared session, erasing the very audit
    trail the refusal was supposed to leave behind.
    """
    from app.db import session_scope

    with session_scope() as s:
        s.add(_build(event_type, message, level, voice_message_id,
                     request_id, cust_nb, order_nb, details))
