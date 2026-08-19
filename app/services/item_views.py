"""Reusable Telegram keyboards for Phase 4 item actions."""

from app.models.actions import ActionCallback, BrainDumpItem, CallbackAction
from app.models.classification import CaptureType
from app.models.telegram import InlineKeyboardButton, InlineKeyboardMarkup


class ItemActionViewBuilder:
    @classmethod
    def action_keyboard(cls, item: BrainDumpItem) -> InlineKeyboardMarkup:
        if item.is_planned_purchase:
            rows = [
                [
                    cls._button("Focus", CallbackAction.FOCUS, item),
                    cls._button("Bought", CallbackAction.BOUGHT, item),
                ],
                [
                    cls._button("Keep", CallbackAction.KEEP, item),
                    cls._button("Delete", CallbackAction.DELETE, item),
                ],
            ]
        elif item.is_routine_purchase:
            rows = [
                [
                    cls._button("Bought", CallbackAction.BOUGHT, item),
                    cls._button("Snooze", CallbackAction.SNOOZE_MENU, item),
                ],
                [cls._button("Delete", CallbackAction.DELETE, item)],
            ]
        elif item.type is CaptureType.TASK:
            rows = [
                [
                    cls._button("Done", CallbackAction.DONE, item),
                    cls._button("Snooze", CallbackAction.SNOOZE_MENU, item),
                ],
                [
                    cls._button("Keep", CallbackAction.KEEP, item),
                    cls._button("Delete", CallbackAction.DELETE, item),
                ],
            ]
        elif item.type in {CaptureType.IDEA, CaptureType.THOUGHT}:
            rows = [
                [
                    cls._button("Keep", CallbackAction.KEEP, item),
                    cls._button("Delete", CallbackAction.DELETE, item),
                ]
            ]
        else:
            rows = [[cls._button("Delete", CallbackAction.DELETE, item)]]
        rows.append([InlineKeyboardButton(text="Open", url=item.page_url)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @classmethod
    def snooze_keyboard(cls, item: BrainDumpItem) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    cls._button("Tomorrow", CallbackAction.SNOOZE_TOMORROW, item),
                    cls._button("Next week", CallbackAction.SNOOZE_NEXT_WEEK, item),
                ],
                [
                    cls._button("2 weeks", CallbackAction.SNOOZE_TWO_WEEKS, item),
                    cls._button("1 month", CallbackAction.SNOOZE_ONE_MONTH, item),
                ],
                [cls._button("Back", CallbackAction.BACK, item)],
            ]
        )

    @staticmethod
    def _button(text: str, action: CallbackAction, item: BrainDumpItem) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text,
            callback_data=ActionCallback(action=action, page_id=item.page_id).encode(),
        )
