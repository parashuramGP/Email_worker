"""
Rule-based spam detector — no API key or credits needed.
Detects spam using keywords, patterns, and heuristics.
"""

import re
import logging

logger = logging.getLogger("spam_detector")

SPAM_SUBJECT_KEYWORDS = [
    "winner", "won", "lottery", "prize", "claim", "free", "urgent",
    "congratulations", "selected", "lucky", "million", "cash",
    "click here", "act now", "limited time", "offer expires",
    "make money", "earn money", "work from home", "extra income",
    "100% free", "no cost", "guarantee", "risk free",
    "viagra", "cialis", "pharmacy", "pills", "meds",
    "nigerian", "prince", "inheritance", "transfer funds",
    "verify your account", "account suspended", "unusual activity",
    "confirm your", "update your", "validate your",
    "dear friend", "dear customer", "dear user",
    "unsubscribe", "opt out", "remove me",
    "weight loss", "lose weight", "diet", "slim",
    "dating", "singles", "meet women", "meet men",
    "bitcoin", "crypto", "investment opportunity", "double your",
    "pre-approved", "you qualify", "exclusive deal",
]

SPAM_BODY_KEYWORDS = [
    "click here to unsubscribe", "to stop receiving",
    "this is not spam", "this email is not spam",
    "you are receiving this because", "you signed up",
    "nigerian prince", "transfer of funds", "next of kin",
    "wire transfer", "western union", "moneygram",
    "ssn", "social security", "bank account number",
    "routing number", "credit card number",
    "password", "login credentials", "verify identity",
    "won a prize", "selected winner", "claim your prize",
    "bulk email", "mass email", "email marketing",
]

SPAM_SENDER_PATTERNS = [
    r"no.?reply@",
    r"noreply@",
    r"donotreply@",
    r"newsletter@",
    r"promo@",
    r"marketing@",
    r"offers@",
    r"deals@",
    r"discount@",
    r"sale@",
    r"info@.*\.(tk|ml|ga|cf|gq)$",
    r"\d{5,}@",
]

LEGITIMATE_SIGNALS = [
    "github", "google", "microsoft", "amazon", "apple",
    "linkedin", "twitter", "facebook", "instagram",
    "bank", "paypal", "stripe", "university", "college",
    "school", ".edu", ".gov",
]


class SpamDetector:
    def __init__(self):
        logger.info("SpamDetector initialized (rule-based mode — no API needed)")

    def analyze_email(self, email_dict: dict) -> dict:
        subject = (email_dict.get("subject") or "").lower()
        sender = (email_dict.get("sender") or "").lower()
        body = (email_dict.get("body") or "").lower()

        score = 0.0
        reasons = []

        # Trusted senders — immediately mark as not spam
        for signal in LEGITIMATE_SIGNALS:
            if signal in sender:
                return {
                    "is_spam": False,
                    "confidence": 0.05,
                    "reason": f"Trusted sender ({signal})"
                }

        # Spam keywords in subject
        subject_hits = [kw for kw in SPAM_SUBJECT_KEYWORDS if kw in subject]
        if subject_hits:
            score += min(0.3 * len(subject_hits), 0.6)
            reasons.append(f"Spam keywords in subject: {', '.join(subject_hits[:3])}")

        # Spam phrases in body
        body_hits = [kw for kw in SPAM_BODY_KEYWORDS if kw in body]
        if body_hits:
            score += min(0.2 * len(body_hits), 0.4)
            reasons.append(f"Spam phrases in body: {', '.join(body_hits[:2])}")

        # Suspicious sender pattern
        for pattern in SPAM_SENDER_PATTERNS:
            if re.search(pattern, sender):
                score += 0.3
                reasons.append(f"Suspicious sender: {sender}")
                break

        # Excessive exclamation marks
        if subject.count("!") >= 2:
            score += 0.1
            reasons.append("Excessive exclamation marks")

        # Excessive CAPS in subject
        orig_subject = email_dict.get("subject", "")
        caps_ratio = sum(1 for c in orig_subject if c.isupper()) / max(len(orig_subject), 1)
        if caps_ratio > 0.5 and len(orig_subject) > 5:
            score += 0.15
            reasons.append("Excessive CAPS in subject")

        # Too many URLs
        url_count = len(re.findall(r'https?://', body))
        if url_count > 5:
            score += 0.1
            reasons.append(f"Many URLs ({url_count})")

        score = min(score, 0.99)
        is_spam = score >= 0.5

        return {
            "is_spam": is_spam,
            "confidence": round(score, 2),
            "reason": "; ".join(reasons) if reasons else "No spam signals detected"
        }

    def analyze_batch(self, emails: list) -> list:
        results = []
        for email in emails:
            try:
                result = self.analyze_email(email)
            except Exception as e:
                logger.error(f"Failed to analyze email: {e}")
                result = {"is_spam": False, "confidence": 0.0, "reason": "Analysis failed"}
            results.append(result)
        return results
