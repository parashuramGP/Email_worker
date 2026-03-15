"""
email_client.py
---------------
Handles all email operations: IMAP connection for reading/deleting emails
and SMTP for sending emails. Compatible with Gmail, Outlook, and other
standard IMAP/SMTP providers.

For Gmail users:
  - Enable 2-Factor Authentication in your Google Account.
  - Generate an App Password at: Settings > Security > 2FA > App Passwords.
  - Use that App Password (not your regular password) in .env.
"""

import imaplib
import smtplib
import email
import email.header
import html
import re
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

import imapclient

logger = logging.getLogger(__name__)


def _decode_header_value(value: str) -> str:
    """Decode an RFC 2047-encoded email header value into a plain string."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded_parts = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(part.decode("latin-1", errors="replace"))
        else:
            decoded_parts.append(part)
    return "".join(decoded_parts)


def _strip_html(html_text: str) -> str:
    """Convert HTML to plain text by removing tags and decoding entities."""
    # Remove script and style blocks entirely
    html_text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    # Replace common block-level tags with newlines
    html_text = re.sub(r"<(br|p|div|tr|li)[^>]*>", "\n", html_text, flags=re.IGNORECASE)
    # Remove all remaining tags
    html_text = re.sub(r"<[^>]+>", "", html_text)
    # Decode HTML entities (e.g. &amp; -> &)
    html_text = html.unescape(html_text)
    # Collapse excessive whitespace / blank lines
    html_text = re.sub(r"\n{3,}", "\n\n", html_text)
    html_text = re.sub(r"[ \t]+", " ", html_text)
    return html_text.strip()


def _extract_body(msg: email.message.Message) -> str:
    """
    Walk a (possibly multipart) email message and extract the best available
    plain-text representation of the body.
    Preference order: text/plain > text/html (stripped) > empty string.
    """
    plain_parts = []
    html_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            # Skip attachments
            if "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("latin-1", errors="replace")

            if content_type == "text/plain":
                plain_parts.append(text)
            elif content_type == "text/html":
                html_parts.append(text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("latin-1", errors="replace")
            if msg.get_content_type() == "text/html":
                html_parts.append(text)
            else:
                plain_parts.append(text)

    if plain_parts:
        return "\n".join(plain_parts).strip()
    if html_parts:
        return _strip_html("\n".join(html_parts))
    return ""


class EmailClient:
    """
    High-level email client that wraps IMAPClient for reading/managing emails
    and smtplib for sending emails.

    Usage:
        client = EmailClient(host, port, address, password)
        client.connect()
        emails = client.fetch_emails(limit=20)
        client.delete_email(emails[0]['id'])
        client.disconnect()
    """

    # Common Gmail trash folder name; will be auto-detected if different.
    GMAIL_TRASH = "[Gmail]/Trash"

    def __init__(self, host: str, port: int, email_address: str, password: str,
                 smtp_host: str = "", smtp_port: int = 587):
        self.host = host
        self.port = port
        self.email_address = email_address
        self.password = password
        self.smtp_host = smtp_host or host.replace("imap.", "smtp.")
        self.smtp_port = smtp_port
        self._imap: Optional[imapclient.IMAPClient] = None
        self._trash_folder: Optional[str] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish an SSL-secured IMAP connection and authenticate."""
        logger.info("Connecting to IMAP server %s:%s", self.host, self.port)
        self._imap = imapclient.IMAPClient(self.host, port=self.port, ssl=True)
        self._imap.login(self.email_address, self.password)
        logger.info("IMAP login successful for %s", self.email_address)
        # Detect trash folder once after login
        self._trash_folder = self._detect_trash_folder()

    def disconnect(self) -> None:
        """Gracefully log out and close the IMAP connection."""
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None
            logger.info("IMAP connection closed.")

    def _require_connection(self) -> None:
        if self._imap is None:
            raise RuntimeError("Not connected. Call connect() first.")

    # ------------------------------------------------------------------
    # Folder utilities
    # ------------------------------------------------------------------

    def get_folders(self) -> list[str]:
        """Return a list of all folder names on the server."""
        self._require_connection()
        folders = []
        for flags, delimiter, name in self._imap.list_folders():
            folders.append(name)
        return folders

    def _detect_trash_folder(self) -> str:
        """
        Attempt to find the server's trash folder by inspecting folder flags.
        Falls back to common names, then to the Gmail default.
        """
        try:
            for flags, delimiter, name in self._imap.list_folders():
                # RFC 6154 defines \\Trash flag for trash folders
                flag_strings = [f.decode() if isinstance(f, bytes) else str(f) for f in flags]
                if "\\Trash" in flag_strings or "\\Deleted" in flag_strings:
                    logger.debug("Detected trash folder via flags: %s", name)
                    return name
            # Fallback: look for common names
            all_folders = self.get_folders()
            for candidate in ["[Gmail]/Trash", "Trash", "Deleted Items", "Deleted Messages", "INBOX.Trash"]:
                if candidate in all_folders:
                    logger.debug("Detected trash folder by name: %s", candidate)
                    return candidate
        except Exception as exc:
            logger.warning("Could not auto-detect trash folder: %s", exc)
        return self.GMAIL_TRASH

    # ------------------------------------------------------------------
    # Email fetching
    # ------------------------------------------------------------------

    def fetch_emails(self, folder: str = "INBOX", limit: int = 50,
                     unread_only: bool = False) -> list[dict]:
        """
        Fetch emails from *folder*.

        Args:
            folder:      IMAP folder name (default 'INBOX').
            limit:       Maximum number of emails to return (most recent first).
            unread_only: If True, only return unseen messages.

        Returns:
            List of dicts with keys:
                id, subject, sender, date, body, snippet
        """
        self._require_connection()
        self._imap.select_folder(folder, readonly=True)

        criteria = "UNSEEN" if unread_only else "ALL"
        message_ids = self._imap.search([criteria])

        # Take the most recent `limit` messages (IMAP IDs are ascending)
        message_ids = list(message_ids)[-limit:]
        if not message_ids:
            return []

        # Fetch envelope + body data in one round-trip for efficiency
        fetch_data = self._imap.fetch(
            message_ids,
            ["ENVELOPE", "BODY[]", "FLAGS", "RFC822.SIZE"]
        )

        emails = []
        for uid, data in fetch_data.items():
            try:
                parsed = self._parse_fetch_data(uid, data)
                if parsed:
                    emails.append(parsed)
            except Exception as exc:
                logger.warning("Failed to parse email uid=%s: %s", uid, exc)

        # Return newest first
        emails.sort(key=lambda e: e.get("date") or datetime.min, reverse=True)
        return emails

    def _parse_fetch_data(self, uid: int, data: dict) -> Optional[dict]:
        """Parse raw IMAP fetch data into a clean email dict."""
        raw = data.get(b"BODY[]") or data.get("BODY[]")
        if not raw:
            return None

        msg = email.message_from_bytes(raw)

        subject = _decode_header_value(msg.get("Subject", "(no subject)"))
        sender = _decode_header_value(msg.get("From", ""))
        date_str = msg.get("Date", "")

        # Parse date
        parsed_date: Optional[datetime] = None
        try:
            parsed_tuple = email.utils.parsedate_to_datetime(date_str)
            # Convert to naive UTC for simple comparison
            parsed_date = parsed_tuple.replace(tzinfo=None)
        except Exception:
            pass

        body = _extract_body(msg)
        snippet = body[:200].replace("\n", " ").strip() if body else ""

        return {
            "id": uid,
            "subject": subject,
            "sender": sender,
            "date": parsed_date,
            "date_str": date_str,
            "body": body,
            "snippet": snippet,
        }

    # ------------------------------------------------------------------
    # Email management
    # ------------------------------------------------------------------

    def delete_email(self, email_id: int) -> bool:
        """
        Move *email_id* to the trash folder.

        For Gmail: copies to [Gmail]/Trash, then marks original deleted.
        For other providers: marks as \\Deleted and expunges.

        Returns True on success, False on failure.
        """
        self._require_connection()
        try:
            self._imap.select_folder("INBOX")

            if self._trash_folder and self._trash_folder != "INBOX":
                # Copy to trash then mark original for deletion
                self._imap.copy([email_id], self._trash_folder)
                self._imap.delete_messages([email_id])
                self._imap.expunge()
                logger.info("Moved email %s to %s", email_id, self._trash_folder)
            else:
                # Just mark deleted and expunge
                self._imap.delete_messages([email_id])
                self._imap.expunge()
                logger.info("Deleted email %s", email_id)
            return True
        except Exception as exc:
            logger.error("Failed to delete email %s: %s", email_id, exc)
            return False

    # ------------------------------------------------------------------
    # Sending emails
    # ------------------------------------------------------------------

    def send_email(self, to: str, subject: str, body: str,
                   cc: Optional[str] = None) -> bool:
        """
        Send a plain-text email via SMTP with STARTTLS.

        Args:
            to:      Recipient address (or comma-separated list).
            subject: Email subject.
            body:    Plain-text body.
            cc:      Optional CC address(es).

        Returns True on success, False on failure.
        """
        msg = MIMEMultipart("alternative")
        msg["From"] = self.email_address
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        # Attach both plain-text and a minimal HTML version
        msg.attach(MIMEText(body, "plain", "utf-8"))
        html_body = f"<html><body><pre style='font-family:sans-serif'>{html.escape(body)}</pre></body></html>"
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        recipients = [addr.strip() for addr in to.split(",")]
        if cc:
            recipients += [addr.strip() for addr in cc.split(",")]

        try:
            logger.info("Sending email to %s via %s:%s", to, self.smtp_host, self.smtp_port)
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.email_address, self.password)
                server.sendmail(self.email_address, recipients, msg.as_string())
            logger.info("Email sent successfully to %s", to)
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP authentication failed. Check your email/password (App Password for Gmail).")
            return False
        except Exception as exc:
            logger.error("Failed to send email: %s", exc)
            return False
