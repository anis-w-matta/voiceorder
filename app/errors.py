class VoiceOrderError(Exception):
    pass


class AudioReadFailed(VoiceOrderError):
    pass


class TranscriptionFailed(VoiceOrderError):
    pass


class AlreadyCommitted(VoiceOrderError):
    def __init__(self, order_nb: str):
        self.order_nb = order_nb
        super().__init__(f"Already committed as {order_nb}")


class VoiceMessageNotFound(VoiceOrderError):
    def __init__(self, voice_message_id: int):
        self.voice_message_id = voice_message_id
        super().__init__(f"No voice message {voice_message_id}")


class RequestNotFound(VoiceOrderError):
    def __init__(self, request_id: int):
        self.request_id = request_id
        super().__init__(f"No pending request {request_id}")


class RequestNotReviewable(VoiceOrderError):
    pass


class AlreadyDecided(VoiceOrderError):
    """A decision (commit/reject) has already been recorded, so the request
    must not be re-decided - doing so detaches the buffer row from the order
    that was really placed and overwrites the audit trail."""

    def __init__(self, status: str, order_nb: str | None = None):
        self.status = status
        self.order_nb = order_nb
        super().__init__(f"Already decided ({status})")


class UnresolvedLines(VoiceOrderError):
    pass


class CustomerNotFound(VoiceOrderError):
    """No customer row exists for this number.

    Guards OrderCommitService.commit(): order_header.cust_nb has no FK
    constraint to customer (see the migration), so nothing at the database
    level stops an order being created for a customer number that doesn't
    exist. This is the explicit check that closes that gap - a request
    must never become an order for a customer the database doesn't know
    about, and no operator action can override that.
    """

    def __init__(self, cust_nb: str | None):
        self.cust_nb = cust_nb
        super().__init__(f"No customer {cust_nb!r}")


class TargetOrderNotFound(VoiceOrderError):
    """A return named an order number that isn't a real sales order.

    Guards the RETURN branch of OrderCommitService.commit(): the customer
    for a return is pulled from the order it's returning (never picked
    independently - see AcceptIn.target_order_nb), so an order number that
    doesn't resolve to a real SO must stop the commit rather than leave
    cust_nb unset or, worse, silently keep whatever stale value the request
    already had.
    """

    def __init__(self, order_nb: str | None):
        self.order_nb = order_nb
        super().__init__(f"No sales order {order_nb!r}")


