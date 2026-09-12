"""Gmail SMTP経由でのメール通知。"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from typing import Dict, List, Optional

from scrapers.base import Slot

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def send_new_slot_email(
    slots: List[Slot],
    reservation_urls: Dict[str, str],
    subject_prefix: Optional[str] = None,
) -> None:
    """新規スロットを通知するメールを送る。

    subject_prefix を指定すると件名の先頭に付ける(例: 自治体名を渡して
    自治体ごとに個別のメールを送る場合)。省略時は全体向けの件名になる。
    """
    address = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_addr = os.environ.get("NOTIFY_TO", address) or address

    lines = [
        s.describe()
        for s in sorted(slots, key=lambda s: (s.date, s.municipality, s.time_label, s.facility))
    ]
    url_lines = [f"  {name}: {url}" for name, url in reservation_urls.items()]
    body = (
        f"体育館の空きが{len(slots)}件見つかりました。\n\n"
        + "\n".join(lines)
        + "\n\n予約サイト:\n"
        + "\n".join(url_lines)
        + "\n\n※このメールは自動送信です。予約は各自でサイトから行ってください。"
    )

    subject = f"新しい空きが{len(slots)}件見つかりました"
    if subject_prefix:
        subject = f"{subject_prefix} {subject}"

    msg = MIMEText(body)
    msg["Subject"] = f"[体育館空き通知] {subject}"
    msg["From"] = address
    msg["To"] = to_addr

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(address, app_password)
        server.sendmail(address, [to_addr], msg.as_string())
