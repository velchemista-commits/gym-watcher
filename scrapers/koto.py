"""江東区スポーツネットのスクレイパー。

実機検証で分かったこと:
  - 標準的なブラウザのUser-Agentでないと "Forbidden" になる(UAチェックあり)
  - サービス提供時間は6:00〜24:00。時間外は「サービス時間外です」となり利用不可
  - 予約にはログインが必要だが、空き状況の確認は「ゲスト」のままログイン不要で可能
  - 検索条件に曜日(土日祝)を直接指定でき、「次へ」で条件に合う日だけを送れる
  - 1日は3枠固定(09:00-12:00 / 13:00-17:00 / 18:00-21:30)
  - 空きセルは <img title="O">、予約済みセルは class="ng" の「Ｘ」

操作の流れ:
  トップ → パソコン版 入口 → 多機能操作 → 空き状況の確認
  → 屋内を選んで確定 → 種目を選んで確定 → 施設を全選択
  → 曜日(土日祝)をチェック → 検索 → 「次へ」で日送り
"""
from __future__ import annotations

import re
import sys
import time
from typing import Iterable, List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

from .base import Slot, build_time_label, find_qualifying_runs

MUNICIPALITY = "江東区"
TOP_URL = "https://yoyaku.koto-sports.net/"
RESERVATION_URL = "https://yoyaku.koto-sports.net/"

# 種目コード(select name="riyosmk")
RIYOSMK_VOLLEYBALL = "2"

# 標準的なブラウザのUAでないとForbiddenになる
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

TIME_BLOCK_STARTS = ["9:00", "13:00", "18:00"]
TIME_BLOCK_ENDS = ["12:00", "17:00", "21:30"]

# 曜日チェックボックスの並び: 日 月 火 水 木 金 土 祝日
WEEKDAY_CHECKBOX_SUNDAY = 0
WEEKDAY_CHECKBOX_SATURDAY = 6
WEEKDAY_CHECKBOX_HOLIDAY = 7

DEFAULT_MIN_CONSECUTIVE = 2
DEFAULT_ALWAYS_NOTIFY_INDEXES = (1,)  # 13:00-17:00 は1枠でも通知

_DATE_RE = re.compile(r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日")


def fetch_all_slots(
    target_dates: Iterable[str],
    riyosmk_code: str = RIYOSMK_VOLLEYBALL,
    min_consecutive: int = DEFAULT_MIN_CONSECUTIVE,
    always_notify_indexes: Iterable[int] = DEFAULT_ALWAYS_NOTIFY_INDEXES,
) -> List[Slot]:
    """target_dates(YYYYMMDD)に含まれる日の、通知対象となる空き区間を返す。"""
    target_set = set(target_dates)
    if not target_set:
        return []
    last_target = max(target_set)

    slots: List[Slot] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_context(user_agent=USER_AGENT, locale="ja-JP").new_page()
            _open_search_form(page)
            _submit_search(page, riyosmk_code)

            current = _read_date(page)
            if current is None:
                raise RuntimeError(
                    "江東区: 検索結果の日付を取得できませんでした"
                    "(サービス時間外か、サイト構造が変わった可能性があります)"
                )

            guard = 0
            while current is not None and current <= last_target and guard < 200:
                if current in target_set:
                    slots.extend(
                        _parse_slots(
                            page.content(), current, min_consecutive, always_notify_indexes
                        )
                    )
                if current == last_target:
                    break

                next_date = _go_to_next_day(page, current)
                if next_date is None or next_date == current:
                    print(
                        f"江東区: 日送りが{current}より進みませんでした。"
                        "表示可能期間の上限に達した可能性があるため打ち切ります。",
                        file=sys.stderr,
                    )
                    break
                current = next_date
                guard += 1
        finally:
            browser.close()

    return slots


def _open_search_form(page: Page) -> None:
    page.goto(TOP_URL, timeout=30000)
    page.wait_for_load_state("networkidle")
    page.click("text=パソコン版 入口")
    page.wait_for_timeout(1500)
    page.click('input[title="多機能操作"]')
    page.wait_for_timeout(2000)
    page.click("text=空き状況の確認")
    page.wait_for_timeout(2500)


def _submit_search(page: Page, riyosmk_code: str) -> None:
    # 屋内を選んで確定(1つ目の確定ボタン)
    page.select_option('select[name="g_bunruicd_1_show"]', value="1")
    page.locator('input[value="確定"]').nth(0).click()
    page.wait_for_timeout(2000)

    # 種目を選んで確定(2つ目の確定ボタン) → 対象施設一覧が絞り込まれる
    page.select_option('select[name="riyosmk"]', value=riyosmk_code)
    page.locator('input[value="確定"]').nth(1).click()
    page.wait_for_timeout(2000)

    page.locator('input[value="全選択"]').first.click()
    page.wait_for_timeout(1000)

    # 曜日を土日祝に限定(サイト側で絞ると「次へ」が該当日だけを送ってくれる)
    checkboxes = page.locator('input[name="chkbox"]')
    for index in (
        WEEKDAY_CHECKBOX_SUNDAY,
        WEEKDAY_CHECKBOX_SATURDAY,
        WEEKDAY_CHECKBOX_HOLIDAY,
    ):
        checkboxes.nth(index).check()

    page.locator('input[title="検索"]').first.click()
    page.wait_for_timeout(3500)


def _go_to_next_day(page: Page, previous: str, timeout_ms: int = 20000) -> Optional[str]:
    """「次へ」を押して次の対象日へ進み、次ページの読み込み完了まで待つ。

    「次へ」は JS 経由のフォーム送信でページ遷移を伴う。日付表示の変化だけを見て
    次の操作に進むと、まだ読み込み途中のページをクリックしてしまい以降の日送りが
    無反応になる(実機検証で確認済み)。そのため遷移の完了を明示的に待つ。
    """
    next_link = page.locator("text=次へ")
    if next_link.count() == 0:
        return None

    try:
        with page.expect_navigation(wait_until="load", timeout=timeout_ms):
            next_link.first.click()
    except Exception:
        # 遷移が検知できなくても、日付が変わっていれば続行できる
        pass

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = _read_date(page)
        if current is not None and current != previous:
            page.wait_for_timeout(300)  # 描画の落ち着きを待つ
            return current
        page.wait_for_timeout(300)
    return _read_date(page)


def _read_date(page: Page) -> Optional[str]:
    try:
        return _parse_date(page.inner_text("body"))
    except Exception:
        # ページ遷移の最中は読み取れないことがある
        return None


def _parse_date(text: str) -> Optional[str]:
    m = _DATE_RE.search(text)
    if not m:
        return None
    reiwa_year, month, day = (int(g) for g in m.groups())
    return f"{2018 + reiwa_year:04d}{month:02d}{day:02d}"


def _parse_slots(
    html: str,
    date_str: str,
    min_consecutive: int,
    always_notify_indexes: Iterable[int],
) -> List[Slot]:
    soup = BeautifulSoup(html, "html.parser")
    slots: List[Slot] = []

    for row in soup.select("tbody tr"):
        name_cell = row.find("th")
        cells = row.select('td[id^="td1"]')
        if not name_cell or not cells:
            continue

        facility = " ".join(name_cell.get_text(separator=" ", strip=True).split())
        available = [cell.find("img", title="O") is not None for cell in cells]

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
