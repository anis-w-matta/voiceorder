import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings
from app.errors import SmtpNotConfigured


class Mailer:
    def send_html(self, to: str, subject: str, html: str) -> None:
        if not (settings.smtp_host and settings.smtp_user
                and settings.smtp_password):
            raise SmtpNotConfigured()

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg.attach(MIMEText(html, "html"))
        self._send(to, msg)

    def send_text(self, to: str, subject: str, body: str) -> None:
        if not (settings.smtp_host and settings.smtp_user
                and settings.smtp_password):
            raise SmtpNotConfigured()

        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        self._send(to, msg)

    def _send(self, to: str, msg) -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                          timeout=30) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, [to], msg.as_string())
