"""葛飾区公共施設予約システムのスクレイパー。

さらに別ベンダーのJSP製(`*.do`)で、画面遷移はすべてJavaScript関数経由。

実機検証で分かったこと:
  - メニューのうち「施設の空き状況」だけがログイン不要(他はログイン画面へ飛ぶ)
  - 利用目的から(屋内スポーツ → バレーボール)絞り込むと、対象の館だけが出る
  - 館ごとに1ヶ月カレンダーが出る。館の切り替えは select[name=selectBldCd] +
    再表示ボタン、月移動は「次の月」リンク
  - カレンダーの日セルは selectDay(..., 年, 月, 日) の形で日付が埋め込まれている
  - カレンダーが「予約あり」の日は時間帯を開いても空きゼロであることを確認済み。
    ドリルダウンは「一部空き」等の日だけでよく、取得が大幅に速くなる
  - 時間枠は館ごとに異なる(奥戸は4枠 900-1130/1230-1500/1530-1800/1830-2100、
    水元総スポ体育館は5枠 900-1100/1130-1330/1400-1600/1630-1830/1900-2100)。
    そのため枠は固定せず、日別画面のヘッダーから読み取る
  - 空きセルは img[alt="空き"]
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from itertools import groupby
from typing import Dict, Iterable, List, Sequence

from playwright.sync_api import Page, sync_playwright

from .base import Slot, build_time_label, find_qualifying_runs

MUNICIPALITY = "葛飾区"
TOP_URL = "https://rsv.shisetsu.city.katsushika.lg.jp/katsushika/web/"
RESERVATION_URL = "https://rsv.shisetsu.city.katsushika.lg.jp/katsushika/web/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

PURPOSE_CATEGORY = "屋内スポーツ"
PURPOSE_VOLLEYBALL = "バレーボール"


@dataclass(frozen=True)
class BuildingTarget:
    code: str  # select[name=selectBldCd] の値
    name: str
    purpose: str  # 利用目的。選ぶ目的によって表示される部屋が変わる
    rooms: Sequence[str]  # 対象にする部屋名(分割面は含めない)


# 水元総スポ体育館は「バレーボール」だとメインアリーナがＡ面／Ｂ面に分かれて
# しか出てこず、全面を取れない。「その他」で検索すると全面が選べる。
TARGET_BUILDINGS = [
    BuildingTarget("500100", "奥戸総スポ　体育館", PURPOSE_VOLLEYBALL, ("大体育室全面",)),
    BuildingTarget("500600", "奥戸総スポ　エイトホール", PURPOSE_VOLLEYBALL, ("エイトホール",)),
    BuildingTarget("500910", "水元総スポ体育館", "その他", ("メインアリーナ全面",)),
]

AVAILABLE_MARK = "空き"
# カレンダー上でこの表示の日は時間帯を開いても空きがないため、開かない
NO_VACANCY_DAY_MARKS = {"受付期間外", "予約あり", "休館", "保守点検"}

_MAX_MONTHS = 3

DEFAULT_MIN_CONSECUTIVE = 2
DEFAULT_ALWAYS_NOTIFY_INDEXES: tuple[int, ...] = ()

_CALENDAR_JS = """() => Array.from(document.querySelectorAll('a[href*="selectDay"]')).map(a => {
    const m = a.getAttribute('href').match(/(\\d{4}),\\s*(\\d{1,2}),\\s*(\\d{1,2})\\)/);
    const img = a.querySelector('img');
    return {
      date: m ? m[1] + m[2].padStart(2, '0') + m[3].padStart(2, '0') : '',
      alt: img ? img.alt : ''
    };
}).filter(x => x.date)"""

_TIME_HEADER_JS = """() => {
  let best = '';
  document.querySelectorAll('tr').forEach(tr => {
    const txt = tr.innerText.replace(/\\s+/g, ' ').trim();
    if (txt.includes('回') && /\\d{3,4}-\\d{3,4}/.test(txt)) {
      if (best === '' || txt.length < best.length) best = txt;
    }
  });
  return best;
}"""

_DAY_JS = """() => {
  const out = [];
  document.querySelectorAll('tr').forEach(tr => {
    const tds = Array.from(tr.children);
    if (tds.length < 2) return;
    const label = tds[0].innerText.trim();
    if (label.length === 0 || label.length > 20) return;
    const marks = tds.slice(1).map(td => {
      const img = td.querySelector('img');
      return img ? (img.alt || '') : '';
    });
    if (marks.length >= 2 && marks.some(m => m !== '')) out.push({room: label, marks: marks});
  });
  return out;
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

    slots: List[Slot] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_context(user_agent=USER_AGENT, locale="ja-JP").new_page()

            # 目的ごとに検索画面をやり直す(目的によって出てくる部屋が変わるため)
            for purpose, group in groupby(
                sorted(TARGET_BUILDINGS, key=lambda b: b.purpose), key=lambda b: b.purpose
            ):
                buildings = list(group)
                _open_calendar(page, purpose, buildings[0].name)

                for building in buildings:
                    try:
                        _switch_building(page, building.code)
                    except Exception as e:
                        print(
                            f"葛飾区: {building.name} に切り替えられませんでした: {e}",
                            file=sys.stderr,
                        )
                        continue

                    rooms = list(facility_filters) or list(building.rooms)
                    for _ in range(_MAX_MONTHS):
                        days = page.evaluate(_CALENDAR_JS)
                        if not days:
                            break

                        for day in days:
                            if day["date"] not in target_set:
                                continue
                            if day["alt"] in NO_VACANCY_DAY_MARKS:
                                continue
                            slots.extend(
                                _read_day(page, day["date"], building.name, min_consecutive,
                                          always_notify_indexes, rooms)
                            )

                        if any(d["date"] >= last_target for d in days):
                            break
                        if not _go_to_next_month(page):
                            break
        finally:
            browser.close()

    return slots


def _click_and_wait(page: Page, selector: str, timeout_ms: int = 20000) -> None:
    try:
        with page.expect_navigation(wait_until="load", timeout=timeout_ms):
            page.locator(selector).first.click()
    except Exception:
        pass
    page.wait_for_timeout(400)


def _open_calendar(page: Page, purpose: str, first_building: str) -> None:
    page.goto(TOP_URL, timeout=45000)
    page.wait_for_load_state("networkidle")

    # 施設予約モード → 「施設の空き状況」(ここだけログイン不要)
    for script in ("startMode(1)", "canLogin()"):
        try:
            with page.expect_navigation(wait_until="load", timeout=20000):
                page.evaluate(script)
        except Exception:
            pass
        page.wait_for_timeout(500)

    _click_and_wait(page, 'a:has(img[alt="利用目的から"])')
    _click_and_wait(page, f'a:has-text("{PURPOSE_CATEGORY}")')
    _click_and_wait(page, f'a:text-is("{purpose}")')

    # 館選択画面 → 最初の館でカレンダーを開く(以降はselectで切り替える)
    _click_and_wait(page, f'a:has-text("{first_building}")')


def _switch_building(page: Page, building_code: str) -> None:
    page.select_option('select[name="selectBldCd"]', value=building_code)
    page.wait_for_timeout(300)
    _click_and_wait(page, 'a[href*="gRsvWTransInstSrchInstAction"]')


def _go_to_next_month(page: Page) -> bool:
    link = page.locator('a:has(img[alt="次の月"])')
    if link.count() == 0:
        return False
    _click_and_wait(page, 'a:has(img[alt="次の月"])')
    return True


def _read_day(
    page: Page,
    date_str: str,
    building: str,
    min_consecutive: int,
    always_notify_indexes: Iterable[int],
    facility_filters: Sequence[str],
) -> List[Slot]:
    year, month, day = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
    _click_and_wait(page, f'a[href*="{year}, {month}, {day})"]')

    starts, ends = _parse_time_blocks(page.evaluate(_TIME_HEADER_JS))
    rows: List[Dict] = page.evaluate(_DAY_JS)
    slots: List[Slot] = []
    for row in rows:
        room = row["room"]
        if facility_filters and not any(f in room for f in facility_filters):
            continue
        available = [m == AVAILABLE_MARK for m in row["marks"]]
        for start_idx, end_idx in find_qualifying_runs(
            available, min_consecutive, always_notify_indexes
        ):
            slots.append(
                Slot(
                    municipality=MUNICIPALITY,
                    facility=f"{building} {room}".strip(),
                    date=date_str,
                    time_label=build_time_label(starts, ends, start_idx, end_idx),
                )
            )

    _click_and_wait(page, 'a:has(img[alt="もどる"])')
    return slots


def _parse_time_blocks(header_text: str) -> tuple[List[str], List[str]]:
    """「１回 900-1130 ２回 1230-1500 …」から枠の開始・終了時刻を取り出す。

    時間枠は館ごとに異なるため、画面の表示から都度読み取る。
    """
    starts: List[str] = []
    ends: List[str] = []
    for raw_start, raw_end in re.findall(r"(\d{3,4})-(\d{3,4})", header_text or ""):
        starts.append(_format_time(raw_start))
        ends.append(_format_time(raw_end))
    return starts, ends


def _format_time(value: str) -> str:
    return f"{int(value[:-2])}:{value[-2:]}"
