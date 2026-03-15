"""
chat.py
-------
Conversational email agent using Google Gemini AI.
Uses Gemini's native function calling for email management.

Usage:
    python chat.py
    python chat.py --model gemini-2.5-flash
"""

import os
import sys
import argparse
from dotenv import load_dotenv
from google import genai
from google.genai import types
from email_client import EmailClient
from spam_detector import SpamDetector

load_dotenv()

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def safe(text):
    return str(text).encode("ascii", errors="replace").decode("ascii")

# ---------------------------------------------------------------------------
# Email client
# ---------------------------------------------------------------------------
_client = None
_email_cache = []

def get_client():
    global _client
    if _client is None:
        _client = EmailClient(
            host=os.getenv("IMAP_HOST", "imap.gmail.com"),
            port=int(os.getenv("IMAP_PORT", 993)),
            email_address=os.getenv("EMAIL_ADDRESS"),
            password=os.getenv("EMAIL_PASSWORD"),
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(os.getenv("SMTP_PORT", 587)),
        )
        _client.connect()
    return _client

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def action_list(folder="INBOX", limit=10):
    global _email_cache
    emails = get_client().fetch_emails(folder=folder, limit=int(limit))
    _email_cache = emails
    if not emails:
        return "No emails found."
    lines = [f"Here are your {len(emails)} most recent emails in {folder}:\n"]
    for i, e in enumerate(emails, 1):
        subj   = safe((e.get("subject") or "(no subject)")[:50])
        sender = safe((e.get("sender")  or "?")[:30])
        raw_d  = e.get("date")
        date   = raw_d.strftime("%b %d %H:%M") if hasattr(raw_d, "strftime") else ""
        lines.append(f"  [{i}] {subj}\n       {sender}  {date}")
    return "\n".join(lines)

def action_read(number):
    if not _email_cache:
        return action_list() + "\n\n" + action_read(number)
    idx = int(number) - 1
    if idx < 0 or idx >= len(_email_cache):
        return f"No email #{number}. I have {len(_email_cache)} emails loaded."
    e = _email_cache[idx]
    subj   = safe(e.get("subject") or "(no subject)")
    sender = safe(e.get("sender")  or "?")
    body   = safe(e.get("body")    or "(empty)")
    lines  = "\n".join(f"  {l}" for l in body.splitlines()[:50])
    return f"Subject: {subj}\nFrom: {sender}\n\n{lines}"

def action_delete(numbers):
    if not _email_cache:
        return "No emails loaded. Say 'show emails' first."
    if not isinstance(numbers, list):
        numbers = [numbers]
    # Sort descending so popping by index doesn't shift other indices
    indices = sorted([int(n) - 1 for n in numbers], reverse=True)
    deleted = []
    errors = []
    for idx in indices:
        if idx < 0 or idx >= len(_email_cache):
            errors.append(f"#{idx + 1} not found")
            continue
        e = _email_cache[idx]
        subj = safe(e.get("subject") or "(no subject)")
        get_client().delete_email(e.get("id"))
        _email_cache.pop(idx)
        deleted.append(f'"{subj}"')
    result = f"Deleted {len(deleted)} email(s): {', '.join(deleted)}" if deleted else ""
    if errors:
        result += (" | Errors: " + ", ".join(errors))
    return result or "Nothing deleted."

def action_send(to, subject, body):
    get_client().send_email(to=to, subject=subject, body=body)
    return f'Email sent to {to} with subject "{subject}".'

def action_scan(folder="INBOX", limit=20):
    global _email_cache
    emails = get_client().fetch_emails(folder=folder, limit=int(limit))
    _email_cache = emails
    detector = SpamDetector()
    spam = []
    for i, e in enumerate(emails, 1):
        r = detector.analyze_email(e)
        if r["is_spam"]:
            spam.append(f"  [{i}] {safe(e.get('subject','')[:45])}")
    if not spam:
        return f"Scanned {len(emails)} emails. No spam found!"
    return f"Scanned {len(emails)} emails. Spam found:\n" + "\n".join(spam)

def dispatch_action(name: str, args: dict) -> str:
    try:
        if name == "list_emails":
            return action_list(args.get("folder", "INBOX"), args.get("limit", 10))
        elif name == "read_email":
            return action_read(args.get("number", 1))
        elif name == "delete_email":
            return action_delete(args.get("numbers", []))
        elif name == "send_email":
            return action_send(args["to"], args["subject"], args["body"])
        elif name == "scan_spam":
            return action_scan(args.get("folder", "INBOX"), args.get("limit", 20))
        else:
            return f"Unknown action: {name}"
    except Exception as ex:
        return f"Error: {ex}"

# ---------------------------------------------------------------------------
# Tool definitions for Gemini
# ---------------------------------------------------------------------------

TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="list_emails",
            description="List recent emails from the inbox or a specific folder.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "folder": types.Schema(type=types.Type.STRING, description="IMAP folder (default: INBOX)"),
                    "limit": types.Schema(type=types.Type.INTEGER, description="Max emails to fetch (default: 10)"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="read_email",
            description="Read the full content of a specific email by its number.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "number": types.Schema(type=types.Type.INTEGER, description="Email number from the list (1-based)"),
                },
                required=["number"],
            ),
        ),
        types.FunctionDeclaration(
            name="delete_email",
            description="Permanently delete one or more emails by their numbers from the last list.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "numbers": types.Schema(
                        type=types.Type.ARRAY,
                        description="List of email numbers to delete (1-based). E.g. [1, 3, 5]",
                        items=types.Schema(type=types.Type.INTEGER),
                    ),
                },
                required=["numbers"],
            ),
        ),
        types.FunctionDeclaration(
            name="send_email",
            description="Send an email to someone.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "to": types.Schema(type=types.Type.STRING, description="Recipient email address"),
                    "subject": types.Schema(type=types.Type.STRING, description="Email subject line"),
                    "body": types.Schema(type=types.Type.STRING, description="Email body text"),
                },
                required=["to", "subject", "body"],
            ),
        ),
        types.FunctionDeclaration(
            name="scan_spam",
            description="Scan inbox for spam emails.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "folder": types.Schema(type=types.Type.STRING, description="IMAP folder (default: INBOX)"),
                    "limit": types.Schema(type=types.Type.INTEGER, description="Number of emails to scan (default: 20)"),
                },
            ),
        ),
    ]
)

# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

def run_chat(model_name: str):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print(f"{RED}Missing GEMINI_API_KEY in .env{RESET}")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    print(f"\n{BOLD}Email Assistant{RESET} using {CYAN}{model_name}{RESET} (Google Gemini)")
    print(f"{DIM}Say things like:{RESET}")
    print(f"{DIM}  'show my recent emails'   'read email 3'   'delete email 2'{RESET}")
    print(f"{DIM}  'send email to x@y.com saying Hello'   'scan for spam'{RESET}")
    print(f"{DIM}  Type 'quit' to exit.{RESET}\n")

    system = (
        "You are a helpful email assistant. Help the user manage their emails.\n"
        "Use the provided tools to list, read, send, delete emails and scan for spam.\n"
        "Be concise and friendly."
    )

    chat_session = client.chats.create(
        model=model_name,
        config=types.GenerateContentConfig(
            system_instruction=system,
            tools=[TOOLS],
        ),
    )

    while True:
        try:
            user_input = input(f"{GREEN}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "bye"):
            print("Bye!")
            break

        try:
            response = chat_session.send_message(user_input)

            # Handle function call loop
            while True:
                fc_list = []
                text_parts = []

                if response.candidates:
                    for part in response.candidates[0].content.parts:
                        if part.text:
                            text_parts.append(part.text)
                        if part.function_call and part.function_call.name:
                            fc_list.append(part.function_call)

                if text_parts:
                    print(f"\n{CYAN}Assistant:{RESET}\n{''.join(text_parts)}\n")

                if not fc_list:
                    break

                # Execute function calls and send results back
                result_parts = []
                for fc in fc_list:
                    print(f"{DIM}> Calling: {fc.name}{RESET}")
                    result = dispatch_action(fc.name, dict(fc.args))
                    print(f"\n{CYAN}Result:{RESET}\n{safe(result)}\n")
                    result_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"result": result},
                        )
                    )

                response = chat_session.send_message(result_parts)

        except Exception as ex:
            print(f"{RED}Error: {ex}{RESET}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Conversational email agent using Google Gemini.")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model (default: gemini-2.5-flash)")
    args = parser.parse_args()

    missing = [v for v in ["EMAIL_ADDRESS", "EMAIL_PASSWORD", "IMAP_HOST"] if not os.getenv(v)]
    if missing:
        print(f"{RED}Missing .env vars: {', '.join(missing)}{RESET}")
        sys.exit(1)

    run_chat(args.model)
