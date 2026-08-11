class VoiceOrderError(Exception):
    pass


class TranscriptionFailed(VoiceOrderError):
    pass


class ClassificationFailed(VoiceOrderError):
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


class OrderNotFound(VoiceOrderError):
    def __init__(self, order_nb: str, order_type: str):
        self.order_nb = order_nb
        self.order_type = order_type
        super().__init__(f"No order {order_nb}/{order_type}")


class OrderCustomerMismatch(VoiceOrderError):
    """The order exists but belongs to a different customer.

    Bill requests are keyed by (cust_nb, order_nb) from whoever is asking,
    so this is the check that stops customer A from pulling a bill for
    customer B's order by guessing/enumerating order numbers.
    """

    def __init__(self, order_nb: str, cust_nb: str):
        self.order_nb = order_nb
        self.cust_nb = cust_nb
        super().__init__(f"Order {order_nb} does not belong to {cust_nb}")


class EmptyOrder(VoiceOrderError):
    def __init__(self, order_nb: str):
        self.order_nb = order_nb
        super().__init__(f"Order {order_nb} has no lines")


class SmtpNotConfigured(VoiceOrderError):
    pass
