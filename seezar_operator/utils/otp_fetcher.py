import re
import time
import email
import email.message
import email.utils
import imaplib
import logging
from datetime import datetime, timezone, timedelta
from email.header import decode_header
from typing import Optional, List, Tuple

from seezar_operator.config import (
    GMAIL_USER,
    GMAIL_APP_PASSWORD,
    OTP_SENDER_FILTER,
    OTP_TIMEOUT_SECONDS,
    OTP_MAX_AGE_SECONDS
)

logger = logging.getLogger("seezar_operator.otp")


class GmailOTPClient:

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993

    def __init__(
        self,
        user: Optional[str] = None,
        app_password: Optional[str] = None,
        sender_filter: Optional[str] = None
    ):
        self.user = (user or GMAIL_USER).strip()
        self.app_password = (app_password or GMAIL_APP_PASSWORD).strip().replace(" ", "")
        self.sender_filter = (sender_filter or OTP_SENDER_FILTER).strip()
        self._mail: Optional[imaplib.IMAP4_SSL] = None

    def _connect(self) -> imaplib.IMAP4_SSL:
        if not self.user or not self.app_password:
            raise ValueError(
                "Missing Gmail credentials. Please set GMAIL_USER and GMAIL_APP_PASSWORD in your .env file."
            )

        logger.info(f"Connecting to Gmail IMAP ({self.IMAP_HOST}:{self.IMAP_PORT}) for user: {self.user}...")
        try:
            mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
            mail.login(self.user, self.app_password)
            logger.info("Successfully authenticated with Gmail IMAP.")
            return mail
        except imaplib.IMAP4.error as err:
            err_msg = str(err)
            if "AUTHENTICATIONFAILED" in err_msg or "Invalid credentials" in err_msg:
                raise PermissionError(
                    f"Gmail IMAP authentication failed for {self.user}. "
                    "Make sure you are using a 16-character Google App Password (not your personal Google account password) "
                    "and that 2-Step Verification is enabled on your Google Account."
                ) from err
            raise RuntimeError(f"Failed to connect to Gmail IMAP: {err_msg}") from err

    @staticmethod
    def _decode_header_str(header_value: Optional[str]) -> str:
        if not header_value:
            return ""
        decoded_fragments = decode_header(header_value)
        result = []
        for fragment, encoding in decoded_fragments:
            if isinstance(fragment, bytes):
                try:
                    result.append(fragment.decode(encoding or "utf-8", errors="replace"))
                except Exception:
                    result.append(fragment.decode("latin-1", errors="replace"))
            else:
                result.append(str(fragment))
        return "".join(result)

    @staticmethod
    def _extract_body_text(msg: email.message.Message) -> str:
        body_parts = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    continue

                if content_type in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            text = payload.decode(charset, errors="replace")
                        except Exception:
                            text = payload.decode("latin-1", errors="replace")
                        body_parts.append(text)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                try:
                    text = payload.decode(charset, errors="replace")
                except Exception:
                    text = payload.decode("latin-1", errors="replace")
                body_parts.append(text)

        raw_body = "\n".join(body_parts)
        clean_text = re.sub(r"<[^>]+>", " ", raw_body)
        clean_text = re.sub(r"&nbsp;", " ", clean_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        return clean_text

    @staticmethod
    def _extract_otp_code(text: str, subject: str = "") -> Optional[str]:
        # Strategy order matters: subject and labelled codes beat any bare 6-char match.
        if subject:
            subj_match = re.search(r"OTP\s*:\s*([A-Za-z0-9]{4,8})\b", subject, re.IGNORECASE)
            if subj_match:
                code = subj_match.group(1).strip().upper()
                return code

            subj_code_match = re.search(r"(?:code|verification|security)\s*[:is\-–—\s]+([0-9]{4,8})\b", subject, re.IGNORECASE)
            if subj_code_match:
                return subj_code_match.group(1).strip()

        combined = f"{subject}\n{text}"

        seez_body_match = re.search(r"one-time\s+code\s+is\s*([A-Za-z0-9]{4,8})\b", combined, re.IGNORECASE)
        if seez_body_match:
            code = seez_body_match.group(1).strip().upper()
            if not code.lower() in ("valid", "below", "here", "this", "your"):
                return code

        context_patterns = [
            r"(?:verification|security|login|otp|passcode|confirmation)\s*(?:code|number|pin)?\s*[:is\-–—\s]+([0-9]{4,8})\b",
            r"(?:code|pin|otp)\s*(?:is|[:=])\s*([0-9]{4,8})\b",
            r"\b([0-9]{4,8})\s*(?:is your\s*(?:verification|login|security|otp|one-time)?\s*code)",
            r"(?:enter|use)\s*([0-9]{4,8})\s*(?:to\s*log\s*in|to\s*verify|as\s*your\s*code)",
        ]

        for pat in context_patterns:
            match = re.search(pat, combined, re.IGNORECASE)
            if match:
                code = match.group(1).strip()
                if len(code) == 4 and code in ("2023", "2024", "2025", "2026", "2027"):
                    continue
                return code

        six_digit_match = re.search(r"\b([0-9]{6})\b", combined)
        if six_digit_match:
            return six_digit_match.group(1).strip()

        hex_matches = re.findall(r"\b([A-Fa-f0-9]{6})\b", combined)
        for h in hex_matches:
            if any(c.isdigit() for c in h) and any(c.isalpha() for c in h):
                return h.upper()

        return None

    def fetch_latest_otp(
        self,
        sender_filter: Optional[str] = None,
        timeout_seconds: int = OTP_TIMEOUT_SECONDS,
        max_age_seconds: int = OTP_MAX_AGE_SECONDS,
        poll_interval: float = 2.5
    ) -> str:
        filter_str = (sender_filter or self.sender_filter).strip().lower()
        logger.info(
            f"Starting Gmail OTP polling loop: sender_filter='{filter_str}', "
            f"timeout={timeout_seconds}s, max_age={max_age_seconds}s..."
        )

        start_time = time.time()
        attempt = 0

        mail = self._connect()
        try:
            while (time.time() - start_time) < timeout_seconds:
                attempt += 1
                elapsed = int(time.time() - start_time)
                logger.info(f"Polling Gmail inbox (Attempt #{attempt}, elapsed {elapsed}s/{timeout_seconds}s)...")

                try:
                    mail.select("INBOX")

                    status, message_ids = mail.search(None, "ALL")
                    if status != "OK" or not message_ids[0]:
                        time.sleep(poll_interval)
                        continue

                    id_list = message_ids[0].split()
                    recent_ids = id_list[-15:]
                    recent_ids.reverse()

                    now_utc = datetime.now(timezone.utc)

                    for msg_id in recent_ids:
                        status, msg_data = mail.fetch(msg_id, "(RFC822)")
                        if status != "OK" or not msg_data or not msg_data[0]:
                            continue

                        raw_email_bytes = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email_bytes)

                        sender = self._decode_header_str(msg.get("From", ""))
                        subject = self._decode_header_str(msg.get("Subject", ""))
                        date_str = msg.get("Date", "")

                        if filter_str and (filter_str not in sender.lower() and filter_str not in subject.lower()):
                            continue

                        msg_date = None
                        if date_str:
                            try:
                                msg_date = email.utils.parsedate_to_datetime(date_str)
                                if msg_date.tzinfo is None:
                                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                            except Exception:
                                pass

                        if msg_date:
                            age = (now_utc - msg_date).total_seconds()
                            if age > max_age_seconds:
                                continue

                        body_text = self._extract_body_text(msg)
                        otp = self._extract_otp_code(body_text, subject=subject)

                        if otp:
                            masked_otp = f"{otp[:2]}{'*' * (len(otp) - 3)}{otp[-1]}" if len(otp) > 3 else "***"
                            logger.info(
                                f"✅ Successfully extracted OTP '{masked_otp}' from email: "
                                f"From='{sender}', Subject='{subject}'"
                            )
                            return otp

                except imaplib.IMAP4.abort as abort_err:
                    logger.warning(f"IMAP connection was dropped ({abort_err}). Reconnecting...")
                    try:
                        mail.close()
                    except Exception:
                        pass
                    mail = self._connect()
                except Exception as poll_err:
                    logger.debug(f"Error during IMAP poll iteration: {poll_err}")

                time.sleep(poll_interval)

            raise TimeoutError(
                f"Timed out after {timeout_seconds}s waiting for OTP email from '{filter_str}' in Gmail ({self.user}). "
                f"Please ensure Seezar sent the verification code and that '{filter_str}' matches the sender address."
            )

        finally:
            try:
                mail.close()
                mail.logout()
                logger.info("Closed Gmail IMAP session.")
            except Exception:
                pass


def get_otp_from_gmail(
    sender_filter: Optional[str] = None,
    timeout_seconds: int = OTP_TIMEOUT_SECONDS,
    max_age_seconds: int = OTP_MAX_AGE_SECONDS
) -> str:
    client = GmailOTPClient(sender_filter=sender_filter)
    return client.fetch_latest_otp(
        sender_filter=sender_filter,
        timeout_seconds=timeout_seconds,
        max_age_seconds=max_age_seconds
    )
