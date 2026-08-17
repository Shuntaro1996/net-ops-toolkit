"""
core/notifier.py — アラート通知モジュール

対応チャネル:
  - Slack（Incoming Webhook）
  - Email（SMTP / Gmail App Password）

通知ルール:
  - ステータス変化時（Online→Offline, Offline→Online）
  - クールダウン時間（config.yaml: notifications.cooldown_seconds）
  - 同一機器の連続通知を抑制
"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, config: dict):
        notif_cfg = config.get("notifications", {})
        slack_cfg = notif_cfg.get("slack", {})
        email_cfg = notif_cfg.get("email", {})

        self.slack_enabled      = bool(slack_cfg.get("enabled", False))
        self.slack_webhook_url  = str(slack_cfg.get("webhook_url", ""))

        self.email_enabled      = bool(email_cfg.get("enabled", False))
        self.smtp_host          = str(email_cfg.get("smtp_host", "smtp.gmail.com"))
        self.smtp_port          = int(email_cfg.get("smtp_port", 587))
        self.email_sender       = str(email_cfg.get("sender", ""))
        self.email_password     = str(email_cfg.get("password", ""))
        self.email_recipients   = email_cfg.get("recipients", [])

        self.notify_recovery    = bool(notif_cfg.get("notify_on_recovery", True))
        self.cooldown_seconds   = int(notif_cfg.get("cooldown_seconds", 300))

    # ─────────────────────────────────────────────
    # 公開インターフェース
    # ─────────────────────────────────────────────

    def notify_offline(self, host: str, ip: str, error_type: Optional[str] = None) -> list[str]:
        """
        機器がOfflineになったことを通知する。
        Returns: 送信結果メッセージのリスト
        """
        results = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_label = self._error_label(error_type)

        slack_msg = (
            f":red_circle: *[OFFLINE] {host}* (`{ip}`)\n"
            f">時刻: {timestamp}\n"
            f">原因: {error_label}"
        )
        email_subject = f"[障害検知] {host} ({ip}) が応答していません"
        email_body = (
            f"監視対象機器がOfflineになりました。\n\n"
            f"  ホスト名 : {host}\n"
            f"  IPアドレス: {ip}\n"
            f"  検知時刻 : {timestamp}\n"
            f"  エラー種別: {error_label}\n\n"
            f"速やかに確認してください。"
        )

        if self.slack_enabled and self.slack_webhook_url:
            ok, msg = self._send_slack(slack_msg)
            results.append(f"Slack: {'✅ 送信' if ok else f'❌ 失敗 ({msg})'}")

        if self.email_enabled and self.email_recipients:
            ok, msg = self._send_email(email_subject, email_body)
            results.append(f"Email: {'✅ 送信' if ok else f'❌ 失敗 ({msg})'}")

        return results

    def notify_recovery(self, host: str, ip: str, latency_ms: Optional[float] = None) -> list[str]:
        """
        機器がOnlineに復旧したことを通知する。
        """
        if not self.notify_recovery:
            return []

        results = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lat_str = f"{latency_ms:.1f} ms" if latency_ms is not None else "—"

        slack_msg = (
            f":large_green_circle: *[復旧] {host}* (`{ip}`)\n"
            f">時刻: {timestamp}\n"
            f">レイテンシ: {lat_str}"
        )
        email_subject = f"[復旧] {host} ({ip}) が応答を再開しました"
        email_body = (
            f"監視対象機器がOnlineに復旧しました。\n\n"
            f"  ホスト名  : {host}\n"
            f"  IPアドレス: {ip}\n"
            f"  復旧時刻  : {timestamp}\n"
            f"  レイテンシ: {lat_str}\n"
        )

        if self.slack_enabled and self.slack_webhook_url:
            ok, msg = self._send_slack(slack_msg)
            results.append(f"Slack: {'✅ 送信' if ok else f'❌ 失敗 ({msg})'}")

        if self.email_enabled and self.email_recipients:
            ok, msg = self._send_email(email_subject, email_body)
            results.append(f"Email: {'✅ 送信' if ok else f'❌ 失敗 ({msg})'}")

        return results

    def send_test(self) -> list[str]:
        """設定テスト用の通知を送る。"""
        results = []
        msg = ":white_check_mark: net-ops-toolkit の通知テストです。設定が正しく機能しています。"
        if self.slack_enabled and self.slack_webhook_url:
            ok, err = self._send_slack(msg)
            results.append(f"Slack: {'✅ 成功' if ok else f'❌ 失敗 ({err})'}")
        else:
            results.append("Slack: 無効または未設定")

        if self.email_enabled and self.email_recipients:
            ok, err = self._send_email("【テスト】net-ops-toolkit 通知テスト", msg.replace(":", ""))
            results.append(f"Email: {'✅ 成功' if ok else f'❌ 失敗 ({err})'}")
        else:
            results.append("Email: 無効または未設定")

        return results

    # ─────────────────────────────────────────────
    # 内部実装
    # ─────────────────────────────────────────────

    def _send_slack(self, text: str) -> tuple[bool, str]:
        try:
            resp = requests.post(
                self.slack_webhook_url,
                json={"text": text},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, ""
            return False, f"HTTP {resp.status_code}: {resp.text}"
        except requests.exceptions.RequestException as e:
            logger.error("Slack send error: %s", e)
            return False, str(e)

    def _send_email(self, subject: str, body: str) -> tuple[bool, str]:
        try:
            msg = MIMEMultipart()
            msg["From"]    = self.email_sender
            msg["To"]      = ", ".join(self.email_recipients)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(self.email_sender, self.email_password)
                server.sendmail(self.email_sender, self.email_recipients, msg.as_string())
            return True, ""
        except Exception as e:
            logger.error("Email send error: %s", e)
            return False, str(e)

    @staticmethod
    def _error_label(error_type: Optional[str]) -> str:
        labels = {
            "timeout":          "タイムアウト（応答なし）",
            "nxdomain":         "名前解決失敗（DNS未解決）",
            "permission_denied": "権限エラー（ICMP禁止またはFW遮断）",
            "unknown":          "原因不明",
        }
        return labels.get(error_type or "unknown", error_type or "原因不明")
