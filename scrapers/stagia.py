"""江東区・荒川区などで使われている施設予約システム(同一ベンダー)の共通エンジン。

両区でフォームの項目名(g_sessionid / g_bunruicd_N_show / riyosmk / g_heyacd /
chkbox など)や画面遷移(かんたん操作・多機能操作 → 空き状況の確認)が共通のため、
サイトごとの差分を StagiaSite にまとめて同じ処理で扱う。

実機検証で分かった共通の注意点:
  - 標準的なブラウザのUser-Agentでないと Forbidden になる
  - 予約にはログインが必要だが、空き状況の確認はゲストのまま可能
  - 検索条件に曜日(土日祝)を指定でき、「次へ」で該当日だけを送れる
  - 空きセルは <img title="O">、予約済みセルは class="ng" の「Ｘ」
  - 各ボタンはページ遷移を伴うため、遷移完了を待たずに次を操作すると無反応になる

サイトごとに違う点:
  - 入口(江東区はトップページから「パソコン版 入口」を経由、荒川区は直接)
  - 分類の階層数(江東区は1段、荒川区は2段)
  - 施設の絞り込み段数(江東区は部屋のみ、荒川区は施設→部屋)
  - 1日の時間枠
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup
from playwright.sync_api import Locator, Page, sync_playwright

from .base import Slot, build_time_label, find_qualifying_runs

# 標準的なブラウザのUAでないとForbiddenになる
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# 曜日チェックボックスの並び: 日 月 火 水 木 金 土 祝日
WEEKDAY_CHECKBOX_SUNDAY = 0
WEEKDAY_CHECKBOX_SATURDAY = 6
WEEKDAY_CHECKBOX_HOLIDAY = 7

_DATE_RE = re.compile(r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日")


@dataclass(frozen=True)
class StagiaSite:
    municipality: str
    entry_url: str
    reservation_url: str
    # 分類の選択(上位から順に): [(selectのname, 選ぶ値), ...]
    category_selections: Sequence[Tuple[str, str]]
    # 目的選択のあと、全件選択して確定していくselectのname(上位から順に)
    facility_select_names: Sequence[str]
    time_block_starts: Sequence[str]
    time_block_ends: Sequence[str]
    # トップページから入口リンクを踏む必要がある場合はそのテキスト
    entry_link_text: Optional[str] = None


def fetch_all_slots(
    site: StagiaSite,
    target_dates: Iterable[str],
    sport_code: str,
    min_consecutive: int,
    always_notify_indexes: Iterable[int] = (),
    facility_filters: Sequence[str] = (),
) -> List[Slot]:
    """target_dates(YYYYMMDD)に含まれる日の、通知対象となる空き区間を返す。

    facility_filters を指定すると、施設名にそのいずれかを含む部屋だけを対象にする。
    """
    target_set = set(target_dates)
    if not target_set:
        return []
    last_target = max(target_set)

    slots: List[Slot] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_context(user_agent=USER_AGENT, locale="ja-JP").new_page()
            _open_search_form(page, site)
            _submit_search(page, site, sport_code, facility_filters)

            current = _read_date(page)
            if current is None:
                raise RuntimeError(
                    f"{site.municipality}: 検索結果の日付を取得できませんでした"
                    "(サービス時間外か、サイト構造が変わった可能性があります)"
                )

            guard = 0
            while current is not None and current <= last_target and guard < 200:
                if current in target_set:
                    slots.extend(
                        _parse_slots(
                            page.content(),
                            site,
                            current,
                            min_consecutive,
                            always_notify_indexes,
                            facility_filters,
                        )
                    )
                if current == last_target:
                    break

                next_date = _go_to_next_day(page, current)
                if next_date is None or next_date == current:
                    print(
                        f"{site.municipality}: 日送りが{current}より進みませんでした。"
                        "表示可能期間の上限に達した可能性があるため打ち切ります。",
                        file=sys.stderr,
                    )
                    break
                current = next_date
                guard += 1
        finally:
            browser.close()

    return slots


def _click_and_wait(page: Page, locator: Locator, timeout_ms: int = 20000) -> None:
    """ページ遷移を伴うクリック。遷移の完了まで待つ。

    遷移完了を待たずに次の操作をすると、読み込み途中のページを操作してしまい
    以降の操作が無反応になる(実機検証で確認済み)。
    """
    try:
        with page.expect_navigation(wait_until="load", timeout=timeout_ms):
            locator.click()
    except Exception:
        # 遷移が起きない操作だった場合も後続の描画待ちで吸収する
        pass
    page.wait_for_timeout(500)


def _confirm_after(page: Page, select_name: str) -> None:
    """指定selectの直後にある「確定」ボタンを押す(ボタンの並び順に依存しない)。"""
    locator = page.locator(
        f'xpath=//select[@name="{select_name}"]/following::input[@value="確定"][1]'
    )
    if locator.count():
        _click_and_wait(page, locator.first)


def _select_all_and_confirm(
    page: Page, select_name: str, name_filters: Sequence[str] = ()
) -> None:
    options = page.eval_on_selector_all(
        f'select[name="{select_name}"] option',
        "els => els.map(e => [e.value, e.innerText.trim()])",
    )
    if not options:
        raise RuntimeError(f"選択肢が空です: {select_name}")

    if name_filters:
        values = [v for v, text in options if any(f in text for f in name_filters)]
        if not values:
            raise RuntimeError(
                f"facility_filters に一致する施設がありません: {name_filters} "
                f"(候補: {[t for _, t in options]})"
            )
    else:
        values = [v for v, _ in options]

    page.select_option(f'select[name="{select_name}"]', value=values)
    page.wait_for_timeout(300)
    _confirm_after(page, select_name)


def _open_search_form(page: Page, site: StagiaSite) -> None:
    page.goto(site.entry_url, timeout=40000)
    page.wait_for_load_state("networkidle")

    if site.entry_link_text:
        _click_and_wait(page, page.locator(f"text={site.entry_link_text}").first)

    _click_and_wait(page, page.locator('input[title="多機能操作"]').first)
    _click_and_wait(page, page.locator("text=空き状況の確認").first)


def _submit_search(
    page: Page, site: StagiaSite, sport_code: str, facility_filters: Sequence[str] = ()
) -> None:
    for select_name, value in site.category_selections:
        page.select_option(f'select[name="{select_name}"]', value=value)
        _confirm_after(page, select_name)

    page.select_option('select[name="riyosmk"]', value=sport_code)
    _confirm_after(page, "riyosmk")

    # 絞り込みは最後の選択(部屋)に対してのみ適用する。上位の施設選択は全件選んで、
    # 対象の部屋が候補から漏れないようにする。
    last_index = len(site.facility_select_names) - 1
    for i, select_name in enumerate(site.facility_select_names):
        _select_all_and_confirm(
            page, select_name, facility_filters if i == last_index else ()
        )

    # 曜日を土日祝に限定(サイト側で絞ると「次へ」が該当日だけを送ってくれる)
    checkboxes = page.locator('input[name="chkbox"]')
    for index in (
        WEEKDAY_CHECKBOX_SUNDAY,
        WEEKDAY_CHECKBOX_SATURDAY,
        WEEKDAY_CHECKBOX_HOLIDAY,
    ):
        checkboxes.nth(index).check()

    _click_and_wait(page, page.locator('input[title="検索"]').first)


def _go_to_next_day(page: Page, previous: str, timeout_ms: int = 20000) -> Optional[str]:
    next_link = page.locator("text=次へ")
    if next_link.count() == 0:
        return None

    _click_and_wait(page, next_link.first, timeout_ms)

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = _read_date(page)
        if current is not None and current != previous:
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
    site: StagiaSite,
    date_str: str,
    min_consecutive: int,
    always_notify_indexes: Iterable[int],
    facility_filters: Sequence[str] = (),
) -> List[Slot]:
    soup = BeautifulSoup(html, "html.parser")
    slots: List[Slot] = []

    for row in soup.select("tbody tr"):
        name_cell = row.find("th")
        cells = row.select('td[id^="td1"]')
        if not name_cell or not cells:
            continue

        facility = " ".join(name_cell.get_text(separator=" ", strip=True).split())
        if facility_filters and not any(f in facility for f in facility_filters):
            continue
        available = [cell.find("img", title="O") is not None for cell in cells]

        for start_idx, end_idx in find_qualifying_runs(
            available, min_consecutive, always_notify_indexes
        ):
            slots.append(
                Slot(
                    municipality=site.municipality,
                    facility=facility,
                    date=date_str,
                    time_label=build_time_label(
                        site.time_block_starts, site.time_block_ends, start_idx, end_idx
                    ),
                )
            )

    return slots
