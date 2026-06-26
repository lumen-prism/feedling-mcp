from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class Salience(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class MemoryMoment:
    """Current Memory Garden-shaped data after decrypting an existing moment."""

    id: str
    type: str
    title: str = ""
    description: str = ""
    her_quote: str = ""
    context: str = ""
    linked_dimension: str = ""


@dataclass
class MemoryCard:
    """Local MemoryCard v1 shape used by the demo core."""

    id: str
    summary: str
    verbatim: str
    bucket_refs: list[str]
    source_type: str
    context: str = ""
    status: MemoryStatus = MemoryStatus.ACTIVE
    salience: Salience = Salience.MEDIUM
    is_open_thread: bool = False
    follow_up: str = ""
    sensitive_scope: str = ""
    supersedes: list[str] = field(default_factory=list)
    superseded_by: str = ""


@dataclass(frozen=True)
class MemoryIndexItem:
    """Lightweight directory item for agent recall; intentionally excludes verbatim."""

    memory_id: str
    summary: str
    bucket_refs: list[str]
    status: str
    salience: str
    is_open_thread: bool
    score: float


@dataclass(frozen=True)
class MemoryFetchResult:
    """Full detail returned only after the agent chooses a memory id from index."""

    memory_id: str
    summary: str
    verbatim: str
    context: str
    source_type: str
    bucket_refs: list[str]
    status: str
    salience: str
    follow_up: str = ""
    sensitive_scope: str = ""
    supersedes: list[str] = field(default_factory=list)
    superseded_by: str = ""


@dataclass(frozen=True)
class FetchBatch:
    """Fetch response shape that keeps failure information explicit."""

    memories: list[MemoryFetchResult]
    missing_ids: list[str]
    unavailable_ids: list[str]


def _summary_for(moment: MemoryMoment) -> str:
    return moment.description.strip() or moment.title.strip()


def _bucket_refs_for(moment: MemoryMoment) -> list[str]:
    bucket = moment.linked_dimension.strip() or moment.context.strip() or "uncategorized"
    return [bucket]


def memory_moment_to_card(
    moment: MemoryMoment,
    *,
    salience: Salience = Salience.MEDIUM,
    sensitive_scope: str = "",
) -> MemoryCard:
    return MemoryCard(
        id=moment.id,
        summary=_summary_for(moment),
        verbatim=moment.her_quote.strip(),
        bucket_refs=_bucket_refs_for(moment),
        source_type=moment.type or "unknown",
        context=moment.context.strip(),
        salience=salience,
        sensitive_scope=sensitive_scope,
    )


def card_to_index_item(card: MemoryCard) -> MemoryIndexItem:
    return MemoryIndexItem(
        memory_id=card.id,
        summary=card.summary,
        bucket_refs=list(card.bucket_refs),
        status=card.status.value,
        salience=card.salience.value,
        is_open_thread=card.is_open_thread,
        score=_score_for(card),
    )


def card_to_fetch_result(card: MemoryCard) -> MemoryFetchResult:
    return MemoryFetchResult(
        memory_id=card.id,
        summary=card.summary,
        verbatim=card.verbatim,
        context=card.context,
        source_type=card.source_type,
        bucket_refs=list(card.bucket_refs),
        status=card.status.value,
        salience=card.salience.value,
        follow_up=card.follow_up,
        sensitive_scope=card.sensitive_scope,
        supersedes=list(card.supersedes),
        superseded_by=card.superseded_by,
    )


def memory_moment_to_index_item(moment: MemoryMoment) -> MemoryIndexItem:
    return card_to_index_item(memory_moment_to_card(moment))


def memory_moment_to_fetch_result(moment: MemoryMoment) -> MemoryFetchResult:
    return card_to_fetch_result(memory_moment_to_card(moment))


class InMemoryMemoryCore:
    """Small local core for product/contract verification before backend wiring."""

    def __init__(self, initial_cards: Iterable[MemoryCard] = ()) -> None:
        self._cards: dict[str, MemoryCard] = {card.id: card for card in initial_cards}

    def insert(self, card: MemoryCard) -> MemoryCard:
        if card.id in self._cards:
            raise ValueError(f"memory already exists: {card.id}")
        card.status = MemoryStatus.ACTIVE
        self._cards[card.id] = card
        return card

    def supersede(self, old_id: str, new_card: MemoryCard) -> MemoryCard:
        old_card = self._cards.get(old_id)
        if old_card is None:
            raise KeyError(f"memory not found: {old_id}")
        if new_card.id in self._cards:
            raise ValueError(f"memory already exists: {new_card.id}")

        old_card.status = MemoryStatus.SUPERSEDED
        old_card.superseded_by = new_card.id
        new_card.status = MemoryStatus.ACTIVE
        new_card.supersedes = [*new_card.supersedes, old_id]
        self._cards[new_card.id] = new_card
        return new_card

    def index(
        self,
        *,
        status_in: tuple[MemoryStatus, ...] = (MemoryStatus.ACTIVE,),
        limit: int = 50,
    ) -> list[MemoryIndexItem]:
        cards = [card for card in self._cards.values() if card.status in status_in]
        cards.sort(key=_score_for, reverse=True)
        return [card_to_index_item(card) for card in cards[:limit]]

    def fetch(
        self,
        ids: Iterable[str],
        *,
        include_statuses: tuple[MemoryStatus, ...] = (MemoryStatus.ACTIVE,),
    ) -> FetchBatch:
        memories: list[MemoryFetchResult] = []
        missing_ids: list[str] = []
        unavailable_ids: list[str] = []

        for memory_id in ids:
            card = self._cards.get(memory_id)
            if card is None:
                missing_ids.append(memory_id)
                continue
            if card.status not in include_statuses:
                unavailable_ids.append(memory_id)
                continue
            memories.append(card_to_fetch_result(card))

        return FetchBatch(
            memories=memories,
            missing_ids=missing_ids,
            unavailable_ids=unavailable_ids,
        )


def _score_for(card: MemoryCard) -> float:
    salience_score = {
        Salience.LOW: 0.35,
        Salience.MEDIUM: 0.55,
        Salience.HIGH: 0.8,
        Salience.CRITICAL: 1.0,
    }[card.salience]
    if card.is_open_thread:
        salience_score += 0.05
    return min(salience_score, 1.0)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return to_jsonable(asdict(value))
    return value
