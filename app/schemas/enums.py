from enum import Enum


class Intent(str, Enum):
    add_order = "add_order"
    repeat_order = "repeat_order"
    repeat_order_adjusted = "repeat_order_adjusted"
    return_order = "return_order"
    other = "other"


class ChangeType(str, Enum):
    add = "add"
    remove = "remove"
    increase = "increase"
    decrease = "decrease"


class RequestStatus(str, Enum):
    new = "new"
    in_review = "in_review"
    callback = "callback"
    rejected = "rejected"
    committed = "committed"
    # Set durably (own DB transaction) right before calling
    # catalog-service's POST /orders, so a crash mid-call leaves a
    # recoverable trace - see OrderCommitService.commit() and
    # app/worker.py's reconcile_stuck_commits(). Treated the same as
    # committed/rejected everywhere a request is "already decided" -
    # never re-enterable while a commit might be in flight for it.
    committing = "committing"


class MatchMethod(str, Enum):
    exact = "exact"
    alias = "alias"
    fuzzy = "fuzzy"
    substring = "substring"
    prior_order = "prior_order"
    manual = "manual"
    qra_bonus = "qra_bonus"


class QraType(str, Enum):
    """P = substitution (buying item_buy converts the line to item_get),
    T = bonus (buying item_buy also adds a free item_get line), B = both
    on the same QraDetail row. See app/services/qra_engine.py."""
    p = "P"
    t = "T"
    b = "B"
