"""荒川区施設予約システムのスクレイパー(共通エンジン stagia を利用)。

江東区と同一ベンダーのシステムだが、以下が異なる:
  - 入口リンクは不要で gin_menu に直接アクセスできる
  - 分類が2段(スポーツ施設=1300 → 屋内施設=1550)
  - 施設の絞り込みが2段(施設 g_basyocd → 部屋 g_heyacd)
  - 1日4枠(09:00-12:00 / 12:30-15:00 / 15:30-18:00 / 18:30-21:30)
  - 定期メンテナンスは毎月26日0時〜6時
"""
from __future__ import annotations

from typing import Iterable, List, Sequence

from .base import Slot
from .stagia import StagiaSite, fetch_all_slots as _fetch

SITE = StagiaSite(
    municipality="荒川区",
    entry_url="https://shisetsu.city.arakawa.tokyo.jp/stagia/reserve/gin_menu",
    reservation_url="https://shisetsu.city.arakawa.tokyo.jp/stagia/reserve/gin_menu",
    category_selections=[
        ("g_bunruicd_1_show", "1300"),  # スポーツ施設
        ("g_bunruicd_2_show", "1550"),  # 屋内施設
    ],
    facility_select_names=["g_basyocd", "g_heyacd"],
    time_block_starts=["9:00", "12:30", "15:30", "18:30"],
    time_block_ends=["12:00", "15:00", "18:00", "21:30"],
)

RIYOSMK_VOLLEYBALL = "250"

DEFAULT_MIN_CONSECUTIVE = 2
DEFAULT_ALWAYS_NOTIFY_INDEXES: tuple[int, ...] = ()


def fetch_all_slots(
    target_dates: Iterable[str],
    sport_code: str = RIYOSMK_VOLLEYBALL,
    min_consecutive: int = DEFAULT_MIN_CONSECUTIVE,
    always_notify_indexes: Iterable[int] = DEFAULT_ALWAYS_NOTIFY_INDEXES,
    facility_filters: Sequence[str] = (),
) -> List[Slot]:
    return _fetch(
        SITE, target_dates, sport_code, min_consecutive, always_notify_indexes, facility_filters
    )
