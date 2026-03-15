from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

logger = logging.getLogger(__name__)

from .config import EmailConfig
from .schemas import EmailPayload


class EmailClient:
    def __init__(self, cfg: EmailConfig, username: str, password: str):
        self.cfg = cfg
        self.username = username
        self.password = password

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def send(self, payload: EmailPayload) -> None:
        msg = EmailMessage()
        msg["Subject"] = payload.subject
        msg["From"] = self.cfg.from_email
        msg["To"] = ", ".join(self.cfg.to)
        msg.set_content(payload.text_body)
        msg.add_alternative(payload.html_body, subtype="html")

        with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)
