"""Application-facing item action persistence contract."""

from datetime import date
from typing import Protocol

from app.models.actions import BrainDumpItem


class ItemPersistenceError(RuntimeError):
    """Raised when a Brain Dump item cannot be read or mutated safely."""


class ItemRepository(Protocol):
    async def get_by_id(self, *, page_id: str) -> BrainDumpItem | None:
        """Load one active item, scoped to Brain Dump v2."""

    async def trash(self, *, page_id: str) -> bool:
        """Move one active page to trash; return false if it became stale."""

    async def set_snoozed_until(self, *, page_id: str, value: date) -> bool:
        """Set SnoozedUntil; return false if the item became stale."""

    async def list_planned_purchases(self) -> list[BrainDumpItem]:
        """Return every active Planned shopping item in Brain Dump v2."""

    async def set_purchase_focus(self, *, page_id: str, focused: bool) -> bool:
        """Update PurchaseFocus; return false if the item became stale."""

    async def update_planned_purchase_state(
        self,
        *,
        page_id: str,
        snoozed_until: date,
        focused: bool,
    ) -> bool:
        """Update cooldown and focus; return false if the item became stale."""
