"""市川市公共施設予約システムのスクレイパー。

他の自治体とは別ベンダーのASP.NET(WebForms)製で、ウィザード形式で進む:
  スポーツ施設 → 施設選択 → 日時選択 → 施設別空き状況 → 時間帯別空き状況

実機検証で分かったこと:
  - 空き状況の閲覧はログイン不要
  - 日時選択で「1ヶ月」「全日」「曜日(土日祝)」を指定でき、1ヶ月分の土日祝を
    1画面でまとめて取得できる
  - 「施設別空き状況」は日単位(○/△/×)のみ。時間帯別を見るには対象セルを
    選択して「次へ」に進む必要がある
  - 日付はセルのid(例: ..._b20260912)に埋め込まれている
  - 「次へ」ボタンは施設ブロックごとにも存在するため、フッターのボタン
    (#ucPCFooter_btnForward)を明示的に押す必要がある
  - 表示期間を変えると選択が解除されるため、1期間ずつ処理する
  - 1日6枠(9-11 / 11-13 / 13-15 / 15-17 / 17-19 / 19-21、2時間単位)
  - 時間帯別の凡例: ○=空きあり / △=用途によっては使用可能 / ×=空きなし /
    －=申込対象外。誤通知を避けるため ○ のみを空きとして扱う
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from typing import Dict, Iterable, List, Sequence, Tuple

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

from .base import Slot, build_time_label, find_qualifying_runs

MUNICIPALITY = "市川市"
TOP_URL = "https://reserve.city.ichikawa.lg.jp/"
RESERVATION_URL = "https://reserve.city.ichikawa.lg.jp/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# 監視対象の館(施設選択画面のボタン名)
TARGET_BUILDINGS = ["国府台市民体育館", "信篤市民体育館"]

# 日単位グリッドで対象にする部屋(柔道場・剣道場・武道場・相撲場などを除外)
ROOM_KEYWORD = "体育館"

# 時間帯別画面で対象にする面(半面・1/2面は除外)
SUBROOM_KEYWORD = "全面"

TIME_BLOCK_STARTS = ["9:00", "11:00", "13:00", "15:00", "17:00", "19:00"]
TIME_BLOCK_ENDS = ["11:00", "13:00", "15:00", "17:00", "19:00", "21:00"]

AVAILABLE_DAY_MARKS = {"○", "△"}  # 日単位で「時間帯別を見る価値がある」印
AVAILABLE_BLOCK_MARK = "○"  # 時間帯別で空きとみなす印

FOOTER_NEXT = "#ucPCFooter_btnForward"
FOOTER_BACK = "#ucPCFooter_btnBack"

_MAX_PERIODS = 3  # 1期間=1ヶ月。lookahead 60日をカバーできる回数


def fetch_all_slots(
    target_dates: Iterable[str],
    sport_code: str = "",  # このシステムには種目指定がないため未使用
    min_consecutive: int = 2,
    always_notify_indexes: Iterable[int] = (),
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
            _open_day_grid(page)

            for _ in range(_MAX_PERIODS):
                cells = _read_day_grid(page)
                if not cells:
                    break

                pending = [
                    c
                    for c in cells
                    if c["date"] in target_set
                    and c["mark"] in AVAILABLE_DAY_MARKS
                    and ROOM_KEYWORD in c["room"]
                    and (
                        not facility_filters
                        or any(f in c["building"] or f in c["room"] for f in facility_filters)
                    )
                ]

                if pending:
                    for cell in pending:
                        _click_and_wait(page, f'#{cell["aid"]}')
                    _click_and_wait(page, FOOTER_NEXT)
                    slots.extend(
                        _parse_time_blocks(
                            page.content(), min_consecutive, always_notify_indexes
                        )
                    )
                    _click_and_wait(page, FOOTER_BACK)

                # この期間で対象日を見終わっていれば終了
                if any(c["date"] >= last_target for c in cells):
                    break
                if not _go_to_next_period(page):
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


def _open_day_grid(page: Page) -> None:
    page.goto(TOP_URL, timeout=45000)
    page.wait_for_load_state("networkidle")

    _click_and_wait(page, 'input[value="スポーツ施設"]')
    for building in TARGET_BUILDINGS:
        _click_and_wait(page, f'input[type=submit][value="{building}"]')
    _click_and_wait(page, FOOTER_NEXT)

    # 表示期間=1ヶ月、時間帯=全日、曜日=土日祝
    _click_and_wait(page, "#rbtnMonth")
    _click_and_wait(page, "#rbtnAllday")
    for checkbox in ("#chkSun", "#chkSat", "#chkHol"):
        _click_and_wait(page, checkbox)
    _click_and_wait(page, FOOTER_NEXT)


def _read_day_grid(page: Page) -> List[Dict[str, str]]:
    """施設別空き状況(日単位)を [{building, room, date, mark, aid}] で返す。"""
    return page.evaluate(
        """() => {
      const out = [];
      document.querySelectorAll('table[id$="dgTable"]').forEach(tbl => {
        let building = '';
        const holder = tbl.closest('td');
        const container = holder && holder.parentElement && holder.parentElement.parentElement;
        const link = container && container.querySelector('a[id$="lnkShisetsu"]');
        if (link) building = link.innerText.trim();
        tbl.querySelectorAll('tr').forEach(tr => {
          const nameEl = tr.querySelector('span[id$="lblShitsujou"]');
          if (!nameEl) return;
          const room = nameEl.innerText.trim();
          tr.querySelectorAll('a[id*="_b20"]').forEach(a => {
            const m = a.id.match(/_b(\\d{8})$/);
            if (m) out.push({building, room, date: m[1],
                             mark: a.innerText.replace(/\\s|選択/g, ''), aid: a.id});
          });
        });
      });
      return out;
    }"""
    )


def _go_to_next_period(page: Page) -> bool:
    link = page.locator("text=次の期間を表示")
    if link.count() == 0:
        return False
    _click_and_wait(page, "text=次の期間を表示")
    return True


def _parse_time_blocks(
    html: str,
    min_consecutive: int,
    always_notify_indexes: Iterable[int],
) -> List[Slot]:
    """時間帯別空き状況を解析する。

    施設・日付ごとにテーブルが並び、各行が面(全面 / １／２面Ａ など)、
    各列が時間枠にあたる。
    """
    soup = BeautifulSoup(html, "html.parser")
    slots: List[Slot] = []

    for table in soup.select('table[id$="dgTable"]'):
        header = table.find("tr")
        if not header:
            continue
        date_str = _parse_date(header.get_text(" ", strip=True))
        if not date_str:
            continue

        # 同じ接頭辞(dlRepeat_ctlNN_tpItem_)を持つ要素から館名・室名を引く
        prefix = table.get("id", "")[: -len("dgTable")]
        building = _text_by_id(soup, prefix + "lnkShisetsu")
        room = _text_by_id(soup, prefix + "lblShitsujou")

        for row in table.find_all("tr"):
            name_el = row.find("span", id=re.compile(r"lblMen$"))
            if not name_el:
                continue
            subroom = name_el.get_text(strip=True)
            if SUBROOM_KEYWORD not in subroom:
                continue

            marks = _row_marks(row)
            available = [m == AVAILABLE_BLOCK_MARK for m in marks]

            # 館名と室名が同じ施設(例: 信篤市民体育館)は重複させない
            parts = [building]
            if room and room != building:
                parts.append(room)
            parts.append(subroom)
            facility = " ".join(x for x in parts if x)
            for start_idx, end_idx in find_qualifying_runs(
                available, min_consecutive, always_notify_indexes
            ):
                slots.append(
                    Slot(
                        municipality=MUNICIPALITY,
                        facility=facility,
                        date=date_str,
                        time_label=build_time_label(
                            TIME_BLOCK_STARTS, TIME_BLOCK_ENDS, start_idx, end_idx
                        ),
                    )
                )

    return slots


def _text_by_id(soup: BeautifulSoup, element_id: str) -> str:
    el = soup.find(id=element_id)
    return el.get_text(strip=True) if el else ""


def _row_marks(row) -> List[str]:
    """行内の時間枠セル(span id=..._l0 .. _l5)を枠順に取り出す。"""
    marked = []
    for span in row.find_all("span", id=re.compile(r"_l(\d+)$")):
        index = int(re.search(r"_l(\d+)$", span["id"]).group(1))
        marked.append((index, span.get_text(strip=True)))
    return [text for _, text in sorted(marked)]


def _parse_date(text: str) -> str:
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if not m:
        return ""
    y, mo, d = (int(g) for g in m.groups())
    return f"{y:04d}{mo:02d}{d:02d}"


DEFAULT_MIN_CONSECUTIVE = 2
DEFAULT_ALWAYS_NOTIFY_INDEXES: tuple[int, ...] = ()
