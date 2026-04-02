"""Alerting module — sends notifications via webhook and/or email (SMTP).

Events are filtered by the configured event list in [alerting] config section.
Alerts are sent asynchronously to avoid blocking the main loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional
from urllib.request import Request, urlopen

from serwebs.config import get_config

logger = logging.getLogger("serwebs.alerting")

_alerter: Optional["Alerter"] = None


def init_alerter() -> Optional["Alerter"]:
    global _alerter
    cfg = get_config().alerting
    if not cfg.enabled:
        return None
    _alerter = Alerter(cfg)
    return _alerter


def get_alerter() -> Optional["Alerter"]:
    return _alerter


class Alerter:
    def __init__(self, cfg):
        self._cfg = cfg
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _should_alert(self, event: str) -> bool:
        return event in self._cfg.events

    def send(self, event: str, **kwargs) -> None:
        """Fire-and-forget alert. Safe to call from sync or async context."""
        if not self._should_alert(event):
            return
        payload = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send_async(event, payload))
        except RuntimeError:
            # No running loop — send synchronously in thread
            import threading
            threading.Thread(target=self._send_sync, args=(event, payload), daemon=True).start()

    async def _send_async(self, event: str, payload: dict) -> None:
        loop = asyncio.get_running_loop()
        if self._cfg.webhook_url:
            await loop.run_in_executor(None, self._send_webhook, payload)
        if self._cfg.smtp_host and self._cfg.smtp_to:
            await loop.run_in_executor(None, self._send_email, event, payload)

    def _send_sync(self, event: str, payload: dict) -> None:
        if self._cfg.webhook_url:
            self._send_webhook(payload)
        if self._cfg.smtp_host and self._cfg.smtp_to:
            self._send_email(event, payload)

    def _send_webhook(self, payload: dict) -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json", **self._cfg.webhook_headers}
            req = Request(self._cfg.webhook_url, data=data, headers=headers, method="POST")
            ctx = ssl.create_default_context()
            urlopen(req, timeout=10, context=ctx)
            logger.debug("Webhook sent: %s", payload.get("event"))
        except Exception as e:
            logger.warning("Webhook failed: %s", e)

    def _send_email(self, event: str, payload: dict) -> None:
        try:
            body = f"SerWebs Alert: {event}\n\n"
            for k, v in payload.items():
                body += f"  {k}: {v}\n"

            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[SerWebs] {event}"
            msg["From"] = self._cfg.smtp_from or self._cfg.smtp_username
            msg["To"] = ", ".join(self._cfg.smtp_to)

            if self._cfg.smtp_use_tls:
                server = smtplib.SMTP(self._cfg.smtp_host, self._cfg.smtp_port)
                server.starttls(context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(self._cfg.smtp_host, self._cfg.smtp_port)

            if self._cfg.smtp_username:
                server.login(self._cfg.smtp_username, self._cfg.smtp_password)

            server.sendmail(msg["From"], self._cfg.smtp_to, msg.as_string())
            server.quit()
            logger.debug("Email sent: %s -> %s", event, self._cfg.smtp_to)
        except Exception as e:
            logger.warning("Email alert failed: %s", e)
