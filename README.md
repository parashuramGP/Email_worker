# CleanMail — AI-Powered Email Spam Cleaner

CleanMail uses **Claude claude-opus-4-6** (with adaptive thinking) to intelligently detect and remove
spam from your email inbox via IMAP. It works with Gmail, Outlook, Yahoo Mail, and
any standard IMAP/SMTP email provider.

---

## Features

- **AI spam detection** — Claude analyzes each email's sender, subject, and body to
  identify spam, phishing, scams, and unwanted bulk mail.
- **Confidence scoring** — Every classification comes with a 0–100% confidence score
  so you control how aggressively spam is removed.
- **Safe dry-run mode** — Preview what would be deleted before actually deleting anything.
- **Interactive review** — Go through suspected spam one email at a time and decide
  yourself what to delete.
- **Email sending** — Send emails directly from the CLI via SMTP.
- **Folder listing** — View all available IMAP folders on your account.
- **Colorful CLI** — Clean terminal output with ANSI colors (no extra dependencies).

---

## Requirements

- Python 3.10+
- An Anthropic API key (get one at [console.anthropic.com](https://console.anthropic.com))
- Email account with IMAP access enabled

---

## Installation

```bash
# 1. Clone / navigate to the project directory
cd clean_mail

# 2. (Recommended) Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
.venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the example env file and fill in your credentials
cp .env.example .env
```

---

## Configuration

Edit `.env` with your credentials:

```ini
ANTHROPIC_API_KEY=sk-ant-...         # Your Anthropic API key
EMAIL_ADDRESS=you@gmail.com          # Your email address
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   # App Password (see below for Gmail)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
```

### Gmail — App Password Setup (Required)

Google blocks "less secure app" access. You **must** use an App Password:

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already enabled.
3. Search for **App Passwords** (or go to Security > How you sign in > App Passwords).
4. Create a new App Password — select "Mail" and your device.
5. Copy the 16-character password into `EMAIL_PASSWORD` in your `.env`.
6. Also make sure **IMAP is enabled**: Gmail Settings > See all settings > Forwarding and POP/IMAP > Enable IMAP.

### Outlook / Hotmail

```ini
IMAP_HOST=outlook.office365.com
IMAP_PORT=993
SMTP_HOST=smtp-mail.outlook.com
SMTP_PORT=587
```

### Yahoo Mail

```ini
IMAP_HOST=imap.mail.yahoo.com
IMAP_PORT=993
SMTP_HOST=smtp.mail.yahoo.com
SMTP_PORT=587
```

Yahoo also requires an App Password: Account Security > Generate app password.

---

## Usage

### Scan inbox (dry run — no deletions)

```bash
python main.py scan
python main.py scan --limit 100 --threshold 0.9
```

### Scan and auto-delete spam

```bash
python main.py scan --auto-delete
python main.py scan --auto-delete --limit 200 --threshold 0.88
```

### Interactive review (decide per email)

```bash
python main.py interactive
python main.py interactive --limit 30 --threshold 0.70
```

### Send an email

```bash
python main.py send --to friend@example.com --subject "Hello!" --body "How are you?"
python main.py send --to boss@work.com --subject "Report" --body "See attached." --cc colleague@work.com
```

### List IMAP folders

```bash
python main.py list-folders
```

### Verbose / debug output

```bash
python main.py --verbose scan --limit 10
```

---

## Command Reference

```
python main.py <command> [options]

Commands:
  scan            Scan inbox for spam
    --limit N         Emails to scan (default: 50)
    --threshold F     Min confidence 0.0–1.0 (default: 0.85)
    --dry-run         Report only, no deletions (default)
    --auto-delete     Actually delete detected spam

  send            Send an email
    --to ADDRESS      Recipient (required)
    --subject TEXT    Subject line (required)
    --body TEXT       Body text (or pipe via stdin)
    --cc ADDRESS      CC recipient(s)

  interactive     Review suspected spam interactively
    --limit N         Emails to scan (default: 20)
    --threshold F     Min confidence to show (default: 0.70)

  list-folders    List all available IMAP folders

Global:
  --verbose / -v  Enable debug logging
```

---

## How the Spam Detection Works

1. CleanMail connects to your inbox via IMAP (SSL/TLS).
2. It fetches the most recent N emails (headers + body).
3. Each email is sent to **Claude claude-opus-4-6** with a detailed system prompt that describes:
   - What constitutes spam (phishing, scams, bulk UCE, lottery fraud, etc.)
   - What should NOT be flagged (transactional emails, personal emails, work emails)
4. Claude returns a JSON verdict: `{ "is_spam": true, "confidence": 0.97, "reason": "..." }`
5. Emails above the confidence threshold are deleted (or reported in dry-run mode).

The `--threshold` option controls sensitivity:
- `0.95` — Only delete extremely obvious spam (safest)
- `0.85` — Default: high confidence required (recommended)
- `0.70` — More aggressive; may catch borderline cases

---

## File Structure

```
clean_mail/
├── .env.example       # Template for credentials
├── .env               # Your actual credentials (never commit this!)
├── requirements.txt   # Python dependencies
├── email_client.py    # IMAP + SMTP client
├── spam_detector.py   # Claude AI spam classifier
├── email_agent.py     # Orchestrator / business logic
├── main.py            # CLI entry point
└── README.md          # This file
```

---

## Security Notes

- **Never commit `.env`** to version control. Add it to `.gitignore`.
- App Passwords have the same access level as your account password — treat them with care.
- The agent only reads email metadata and body text; no attachments are downloaded.
- Email bodies are truncated to 3,000 characters before being sent to the Claude API.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `IMAP login failed` | Check your App Password. For Gmail, make sure IMAP is enabled. |
| `SMTP authentication failed` | Same App Password is used for IMAP and SMTP. |
| `Missing environment variables` | Make sure `.env` exists and all fields are filled in. |
| Colors not showing (Windows) | Use Windows Terminal or set `COLORTERM=truecolor` env var. |
| `anthropic.AuthenticationError` | Check your `ANTHROPIC_API_KEY` in `.env`. |

---

## License

MIT — use freely, modify as needed.
