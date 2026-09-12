"""足立区施設予約システムのスクレイパー(共通エンジン p_kashikan を利用)。

青梅市と同一ベンダー(P-kashikan)だが、以下が異なる:
  - データセルは9列あり、実際の利用枠(9:00-12:00 / 12:30-15:00 /
    15:30-18:00 / 18:30-21:00、公式ページで梅田地域学習センターの
    時間区分として確認済み)は index 1,3,5,7 にあり、間には準備時間帯
    などの「隙間」セル(index 0,2,4,6,8)が挟まる(セル幅(px)を1時間=37px
    として換算し30分単位に丸めると、公式の4枠の境界と完全に一致する
    ことを実機検証で確認済み)。隙間セルにもごく稀に空きマークが立つ
    ことがあったが、9列すべてを対象にすると実枠どうしが隙間セルで
    分断され「2枠連続」等の判定が本来の意図(実枠が2つ連続)からずれて
    しまうため、実枠4つ(index 1,3,5,7)だけを対象にする
  - バレーボールの目的コードは 021
"""
from __future__ import annotations

from typing import Iterable, List, Sequence

from .base import Slot
from .p_kashikan import PKashikanSite, fetch_all_slots as _fetch

SITE = PKashikanSite(
    municipality="足立区",
    base_url="https://k5.p-kashikan.jp/adachi-ku/",
    time_block_starts=["9:00", "12:30", "15:30", "18:30"],
    time_block_ends=["12:00", "15:00", "18:00", "21:00"],
    real_cell_indices=(1, 3, 5, 7),
)

MOKUTEKI_VOLLEYBALL = "021"

DEFAULT_MIN_CONSECUTIVE = 2
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
