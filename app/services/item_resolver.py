from dataclasses import asdict, dataclass

from rapidfuzz import fuzz
from sqlalchemy import select, text

from app.config import settings
from app.models import Item, OrderDetail, OrderHeader
from app.schemas.enums import MatchMethod


FUZZY_ALIAS_THRESHOLD = 80


def _tokenize(text_val: str) -> list[str]:
    """Split into word-character runs (Unicode-aware, so Arabic counts)."""
    tokens, cur = [], []
    for ch in text_val:
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            tokens.append("".join(cur))
            cur = []
    if cur:
        tokens.append("".join(cur))
    return tokens


def _on_word_boundary(haystack: str, needle: str) -> bool:
    """True if `needle` occurs in `haystack` not glued to another word.

    Guards the substring fallback against matches buried inside a longer
    word ("brush" in "toothbrush"). Works for Arabic as well as Latin:
    str.isalnum() is Unicode-aware, so Arabic letters count as word
    characters and the space/punctuation around a spoken word does not.
    """
    if not needle:
        return False
    i = haystack.find(needle)
    while i != -1:
        before = haystack[i - 1] if i > 0 else " "
        after_i = i + len(needle)
        after = haystack[after_i] if after_i < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        i = haystack.find(needle, i + 1)
    return False


@dataclass
class Candidate:
    item_nb: str
    item_desc: str
    category: str
    score: float
    method: str

    def dict(self):
        return asdict(self)


class ItemResolver:
    def __init__(self, session, accept=None, suggest=None):
        self.s = session
        self.accept = accept if accept is not None else settings.fuzzy_accept
        self.suggest = suggest if suggest is not None else settings.fuzzy_suggest

    def _history(self, cust_nb: str) -> set[str]:
        return set(self.s.execute(
            select(OrderDetail.item_nb)
            .join(OrderHeader,
                  (OrderDetail.order_nb == OrderHeader.order_nb) &
                  (OrderDetail.order_type == OrderHeader.order_type))
            .where(OrderHeader.cust_nb == cust_nb)
        ).scalars().all())

    def resolve(self, raw: str, cust_nb: str | None = None):
        q = (raw or "").strip()
        if not q:
            return None, []

        it = self.s.get(Item, q.upper())
        if it:
            c = Candidate(it.item_number, it.item_desc, it.category, 1.0,
                          MatchMethod.exact.value)
            return c, [c]

        it = self.s.scalars(select(Item).where(Item.item_desc.ilike(q))).first()
        if it:
            c = Candidate(it.item_number, it.item_desc, it.category, 0.98,
                          MatchMethod.exact.value)
            return c, [c]

        row = self.s.execute(text("""
            SELECT i.item_number, i.item_desc, i.category
            FROM item_alias a JOIN item i ON i.item_number = a.item_number
            WHERE lower(a.alias) = lower(:q) LIMIT 1
        """), {"q": q}).first()
        if row:
            c = Candidate(row.item_number, row.item_desc, row.category, 0.96,
                          MatchMethod.alias.value)
            return c, [c]

        rows = self.s.execute(text("""
            SELECT item_number, item_desc, category, score, method FROM (
              SELECT i.item_number, i.item_desc, i.category,
                     similarity(i.item_desc, :q) AS score, 'fuzzy' AS method
              FROM item i WHERE i.item_desc % :q
              UNION ALL
              SELECT i.item_number, i.item_desc, i.category,
                     similarity(a.alias, :q) AS score, 'alias' AS method
              FROM item_alias a JOIN item i ON i.item_number = a.item_number
              WHERE a.alias % :q
            ) u ORDER BY score DESC LIMIT 30
        """), {"q": q}).all()

        hist = self._history(cust_nb) if cust_nb else set()
        best: dict[str, Candidate] = {}
        for r in rows:
            rf = fuzz.token_set_ratio(q.lower(), r.item_desc.lower()) / 100.0
            score = max(float(r.score), rf) if r.method == "fuzzy" \
                else float(r.score)
            if r.item_number in hist:
                score = min(1.0, score + 0.10)
            cand = Candidate(r.item_number, r.item_desc, r.category,
                             round(score, 3), r.method)
            if r.item_number not in best or cand.score > best[r.item_number].score:
                best[r.item_number] = cand

        cands = sorted(best.values(), key=lambda c: c.score, reverse=True)
        cands = [c for c in cands if c.score >= self.suggest][:5]
        top = cands[0] if cands and cands[0].score >= self.accept else None
        return top, cands

    def find_in_text(self, text_val: str) -> list[Candidate]:
        """Find every catalogue item whose alias or description appears
        literally inside `text_val`. Covers the case where the extractor
        leaves `product` unset and returns the whole spoken sentence as
        raw_text: pg_trgm's `%` operator compares the *entire* sentence
        against a short alias, so its similarity usually falls below the
        default threshold and resolve()'s fuzzy path finds nothing even
        though the alias is right there in the text. Can return more than
        one item, e.g. two products named in a single merged line."""
        t = (text_val or "").strip()
        if not t:
            return []

        rows = self.s.execute(text("""
            SELECT i.item_number, i.item_desc, i.category,
                   a.alias AS alias, 'alias' AS method
            FROM item_alias a JOIN item i ON i.item_number = a.item_number
            WHERE length(a.alias) >= 3 AND strpos(lower(:t), lower(a.alias)) > 0
            UNION ALL
            SELECT i.item_number, i.item_desc, i.category,
                   i.item_desc AS alias, 'exact' AS method
            FROM item i
            WHERE length(i.item_desc) >= 3 AND strpos(lower(:t), lower(i.item_desc)) > 0
        """), {"t": t}).all()

        low = t.lower()
        best: dict[str, Candidate] = {}
        for r in rows:
            needle = (r.alias or "").lower()
            if not _on_word_boundary(low, needle):
                continue          # e.g. "brush" inside "toothbrush"
            score = 0.94 if r.method == "alias" else 0.90
            if r.item_number not in best or score > best[r.item_number].score:
                best[r.item_number] = Candidate(
                    r.item_number, r.item_desc, r.category, score,
                    MatchMethod.substring.value)

        # Arabizi has no standard spelling ("roleh" vs "rolleh", "lasse2"
        # vs "lase3"), so an exact literal substring often misses a word
        # a human would recognise instantly. Catch those with a fuzzy pass
        # over items the exact scan didn't already find, scored lower so
        # a genuine exact hit always wins the top slot.
        alias_rows = self.s.execute(text("""
            SELECT i.item_number, i.item_desc, i.category, a.alias AS alias
            FROM item_alias a JOIN item i ON i.item_number = a.item_number
            WHERE length(a.alias) >= 3
        """)).all()
        tokens = _tokenize(low)
        for r in alias_rows:
            if r.item_number in best:
                continue
            alias = (r.alias or "").lower()
            alias_tokens = alias.split()
            windows = tokens if len(alias_tokens) <= 1 else [
                " ".join(tokens[i:i + len(alias_tokens)])
                for i in range(len(tokens) - len(alias_tokens) + 1)]
            for cand_str in windows:
                if abs(len(cand_str) - len(alias)) > 2:
                    continue
                if fuzz.ratio(cand_str, alias) >= FUZZY_ALIAS_THRESHOLD:
                    best[r.item_number] = Candidate(
                        r.item_number, r.item_desc, r.category, 0.85,
                        MatchMethod.substring.value)
                    break

        return sorted(best.values(), key=lambda c: c.score, reverse=True)
