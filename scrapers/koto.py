"""江東区スポーツネットのスクレイパー(共通エンジン stagia を利用)。

サイト固有の事情:
  - トップページから「パソコン版 入口」を踏まないと入れない
  - 分類は1段のみ(屋内=1 / 屋外=2)
  - 施設の絞り込みは部屋(g_heyacd)の1段だけ
  - 1日3枠(09:00-12:00 / 13:00-17:00 / 18:00-21:30)。1枠が3〜4時間と長い
  - サービス提供時間は6:00〜24:00。時間外は取得できない
"""
from __future__ import annotations

from typing import Iterable, List, Sequence

from .base import Slot
from .stagia import StagiaSite, fetch_all_slots as _fetch

SITE = StagiaSite(
    municipality="江東区",
    entry_url="https://yoyaku.koto-sports.net/",
    reservation_url="https://yoyaku.koto-sports.net/",
    entry_link_text="パソコン版 入口",
    category_selections=[("g_bunruicd_1_show", "1")],  # 屋内
    facility_select_names=["g_heyacd"],
    time_block_starts=["9:00", "13:00", "18:00"],
    time_block_ends=["12:00", "17:00", "21:30"],
)

RIYOSMK_VOLLEYBALL = "2"

DEFAULT_MIN_CONSECUTIVE = 2
DEFAULT_ALWAYS_NOTIFY_INDEXES = (1,)  # 13:00-17:00 は1枠でも通知


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
