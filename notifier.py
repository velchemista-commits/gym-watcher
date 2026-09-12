"""Gmail SMTP経由でのメール通知。"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from typing import Dict, List

from scrapers.base import Slot

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def send_new_slot_email(slots: List[Slot], reservation_urls: Dict[str, str]) -> None:
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

    msg = MIMEText(body)
    msg["Subject"] = f"[体育館空き通知] 新しい空きが{len(slots)}件見つかりました"
    msg["From"] = address
    msg["To"] = to_addr

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(address, app_password)
        server.sendmail(address, [to_addr], msg.as_string())
