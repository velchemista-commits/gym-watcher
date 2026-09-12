"""松戸市公共施設インターネット予約システムのスクレイパー。

比較的新しいUIで、条件(いつ/どこで/なにを)を選んで検索する形式。

実機検証で分かったこと:
  - 空き状況の照会はログイン不要
  - 検索条件のselectは name ではなく id で指定する(#days / #bname / #iname /
    #purpose)。「いつ」は折りたたまれているので #collapse-when を開いてから操作する
  - 館(#bname)を選ぶと部屋(#iname)の選択肢が入れ替わる
  - 結果は週表示のテーブル(table[id^="week-info"])で、**行が時間帯・列が日付**と
    他自治体と転置された形。日付ヘッダーに年が入らないため、月が戻ったら年を繰り上げる
  - 「翌週」は getWeekInfoAjax(4, 0, 0) のAjax。ページ遷移しないので表の更新を待つ
  - 空きセルは img[alt="空きのマーク"](他に 予約あり/受付期間外/時間外/一般開放/
    休館/保守/雨天 がある)
  - 1日6枠(9/11/13/15/17/19時の2時間単位)
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Iterable, List, Optional, Sequence, Tuple

from playwright.sync_api import Page, sync_playwright

from .base import Slot, build_time_label, find_qualifying_runs

MUNICIPALITY = "松戸市"
TOP_URL = "https://yoyaku.city.matsudo.chiba.jp/web/"
RESERVATION_URL = "https://yoyaku.city.matsudo.chiba.jp/web/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

PURPOSE_VOLLEYBALL = "2000_2040"

AVAILABLE_MARK = "空き"

BLOCK_HOURS = 2  # 1枠2時間


@dataclass(frozen=True)
class RoomTarget:
    building_code: str  # #bname の値
    room_code: str  # #iname の値
    label: str  # 通知に出す名前


TARGET_ROOMS = [
    RoomTarget("1_2020", "20200100", "松戸市小金原体育館 競技場全面"),
    RoomTarget("2_2040", "20400100", "柿ノ木台公園体育館 競技場全面"),
    RoomTarget("4_2030", "20300100", "松戸市常盤平体育館 競技場全面"),
    RoomTarget("2_2000", "20000100", "松戸運動公園 体育館競技場全面"),
]

DEFAULT_MIN_CONSECUTIVE = 2
DEFAULT_ALWAYS_NOTIFY_INDEXES: tuple[int, ...] = ()

_MAX_WEEKS = 12

_WEEK_JS = """() => {
  const tbl = document.querySelector('table[id^="week-info"]');
  if (!tbl) return null;
  const dates = [];
  tbl.querySelectorAll('thead th').forEach((th, i) => {
    if (i === 0) return;
    dates.push(th.innerText.replace(/\\s+/g, ' ').trim());
  });
  const rows = [];
  tbl.querySelectorAll('tbody tr').forEach(tr => {
    const cells = Array.from(tr.children);
    if (!cells.length) return;
    const label = cells[0].innerText.replace(/\\s+/g, ' ').trim();
    const marks = cells.slice(1).map(td => {
      const img = td.querySelector('img');
      return img ? (img.alt || '') : '';
    });
    rows.push({label: label, marks: marks});
  });
  return {dates: dates, rows: rows};
}"""


def fetch_all_slots(
    target_dates: Iterable[str],
    sport_code: str = PURPOSE_VOLLEYBALL,
    min_consecutive: int = DEFAULT_MIN_CONSECUTIVE,
    always_notify_indexes: Iterable[int] = DEFAULT_ALWAYS_NOTIFY_INDEXES,
    facility_filters: Sequence[str] = (),
) -> List[Slot]:
    target_set = set(target_dates)
    if not target_set:
        return []
    last_target = max(target_set)

    rooms = [
        r for r in TARGET_ROOMS
        if not facility_filters or any(f in r.label for f in facility_filters)
    ]

    slots: List[Slot] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_context(user_agent=USER_AGENT, locale="ja-JP").new_page()
            for room in rooms:
                try:
                    _search(page, room, sport_code)
                except Exception as e:
                    print(f"松戸市: {room.label} の検索に失敗しました: {e}", file=sys.stderr)
                    continue

                seen_last = False
                for _ in range(_MAX_WEEKS):
                    week = page.evaluate(_WEEK_JS)
                    if not week or not week["dates"]:
                        break

                    dates = _resolve_dates(week["dates"])
                    slots.extend(
                        _week_to_slots(
                            week["rows"], dates, room.label, target_set,
                            min_consecutive, always_notify_indexes
                        )
                    )
                    if any(d and d >= last_target for d in dates):
                        seen_last = True
                        break
                    if not _go_to_next_week(page, week["dates"][0]):
                        break
                if not seen_last:
                    print(
                        f"松戸市: {room.label} は表示可能期間の上限まで確認しました。",
                        file=sys.stderr,
                    )
        finally:
            browser.close()

    return slots


def _search(page: Page, room: RoomTarget, purpose_code: str) -> None:
    page.goto(TOP_URL, timeout=45000)
    page.wait_for_load_state("networkidle")
    _dismiss_modal(page)

    _ensure_expanded(page, "#collapse-when")
    page.select_option("#days", value="31")  # 1か月

    _ensure_expanded(page, "#collapse-where")
    page.select_option("#bname", value=room.building_code)
    page.wait_for_timeout(1500)  # 部屋一覧が入れ替わるのを待つ
    page.select_option("#iname", value=room.room_code)
    page.select_option("#purpose", value=purpose_code)
    page.wait_for_timeout(600)

    page.click("#btn-go")
    page.wait_for_timeout(3500)
    page.wait_for_load_state("networkidle")


def _ensure_expanded(page: Page, toggle_selector: str) -> None:
    """折りたたみセクションを開く。

    検索を1回行った後にトップページへ戻ると、前回開いた状態のまま表示される
    ことがある(実機検証で確認済み)。無条件にトグルをクリックすると開いている
    ものを閉じてしまうため、トグル自身の aria-expanded を見て、閉じている
    場合だけクリックする(中の項目の表示有無では判定しない — 例えば「どこで」
    セクションの #bname は折りたたみ状態に関わらず常に表示されている)。
    """
    if page.get_attribute(toggle_selector, "aria-expanded") == "true":
        return
    page.click(toggle_selector)
    page.wait_for_timeout(600)


def _dismiss_modal(page: Page) -> None:
    """お知らせ等のモーダルがランダムなタイミングで表示されるため、出ていれば閉じる。

    表示が遅延することがあるため少し待ってから確認する。
    """
    page.wait_for_timeout(500)
    for selector in ('button.close', 'button:has-text("閉じる")'):
        button = page.locator(selector)
        for i in range(button.count()):
            try:
                if button.nth(i).is_visible():
                    button.nth(i).click()
                    page.wait_for_timeout(200)
            except Exception:
                pass


def _go_to_next_week(page: Page, current_first_date: str, timeout_ms: int = 15000) -> bool:
    link = page.locator('[onclick*="getWeekInfoAjax(4"]')
    if link.count() == 0:
        return False
    link.first.click()

    # Ajaxで表だけ差し替わるので、先頭の日付が変わるまで待つ
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        page.wait_for_timeout(300)
        week = page.evaluate(_WEEK_JS)
        if week and week["dates"] and week["dates"][0] != current_first_date:
            return True
    return False


def _resolve_dates(headers: Sequence[str]) -> List[Optional[str]]:
    """「9月12日 土曜」形式のヘッダーを YYYYMMDD に直す。

    ヘッダーに年が無いため、今日を起点に、月が戻ったら翌年として扱う。
    """
    today = date_cls.today()
    resolved: List[Optional[str]] = []
    year = today.year
    previous_month: Optional[int] = None

    for header in headers:
        m = re.search(r"(\d{1,2})月\s*(\d{1,2})日", header)
        if not m:
            resolved.append(None)
            continue
        month, day = int(m.group(1)), int(m.group(2))
        if previous_month is not None and month < previous_month:
            year += 1
        elif previous_month is None and month < today.month:
            year = today.year + 1
        previous_month = month
        resolved.append(f"{year:04d}{month:02d}{day:02d}")
    return resolved


def _week_to_slots(
    rows: Sequence[dict],
    dates: Sequence[Optional[str]],
    facility: str,
    target_set: set,
    min_consecutive: int,
    always_notify_indexes: Iterable[int],
) -> List[Slot]:
    """週表(行=時間帯, 列=日付)を日付ごとの連続空き区間に変換する。"""
    timed_rows = [(hour, row["marks"]) for row in rows
                  if (hour := _parse_hour(row["label"])) is not None]
    if not timed_rows:
        return []

    starts = [f"{hour}:00" for hour, _ in timed_rows]
    ends = [f"{hour + BLOCK_HOURS}:00" for hour, _ in timed_rows]

    slots: List[Slot] = []
    for column, date_str in enumerate(dates):
        if not date_str or date_str not in target_set:
            continue
        available = [
            column < len(marks) and marks[column] == AVAILABLE_MARK
            for _, marks in timed_rows
        ]
        for start_idx, end_idx in find_qualifying_runs(
            available, min_consecutive, always_notify_indexes
        ):
            slots.append(
                Slot(
                    municipality=MUNICIPALITY,
                    facility=facility,
                    date=date_str,
                    time_label=build_time_label(starts, ends, start_idx, end_idx),
                )
            )
    return slots


def _parse_hour(label: str) -> Optional[int]:
    """「９時」「9時」などから開始時刻を取り出す。時間帯以外の行はNone。"""
    normalized = unicodedata.normalize("NFKC", label)
    m = re.fullmatch(r"(\d{1,2})時", normalized.strip())
    return int(m.group(1)) if m else None
