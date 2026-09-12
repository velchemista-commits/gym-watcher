"""青梅市施設予約管理システムのスクレイパー(共通エンジン p_kashikan を利用)。

サイト固有の情報:
  - 1日6枠: 9-12 / 12-13 / 13-15 / 15-17 / 17-19 / 19-21
  - バレーボールの目的コードは 037
"""
from __future__ import annotations

from typing import Iterable, List, Sequence

from .base import Slot
from .p_kashikan import PKashikanSite, fetch_all_slots as _fetch

SITE = PKashikanSite(
    municipality="青梅市",
    base_url="https://k4.p-kashikan.jp/ome-city/index.php",
    time_block_starts=["9:00", "12:00", "13:00", "15:00", "17:00", "19:00"],
    time_block_ends=["12:00", "13:00", "15:00", "17:00", "19:00", "21:00"],
)

MOKUTEKI_VOLLEYBALL = "037"

DEFAULT_MIN_CONSECUTIVE = 3
DEFAULT_ALWAYS_NOTIFY_INDEXES: tuple[int, ...] = ()


def fetch_all_slots(
    target_dates: Iterable[str],
    mokuteki_code: str = MOKUTEKI_VOLLEYBALL,
    min_consecutive: int = DEFAULT_MIN_CONSECUTIVE,
    always_notify_indexes: Iterable[int] = DEFAULT_ALWAYS_NOTIFY_INDEXES,
    facility_filters: Sequence[str] = (),
) -> List[Slot]:
    return _fetch(
        SITE, target_dates, mokuteki_code, min_consecutive, always_notify_indexes, facility_filters
    )
