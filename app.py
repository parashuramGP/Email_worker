"""
app.py
------
Flask web server — ChatGPT-like email assistant powered by Gemini.
"""

import json
import os

from google import genai
from google.genai import types
from dotenv import load_dotenv
from flask import (Flask, Response, jsonify, render_template,
                   request, session, stream_with_context)

from email_client import EmailClient
from spam_detector import SpamDetector

load_dotenv()

app = Flask(__name__)

_secret = os.environ.get("FLASK_SECRET_KEY")
if not _secret:
    raise RuntimeError("FLASK_SECRET_KEY environment variable is not set. "
                       "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")
app.secret_key = _secret
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Enable secure cookies when running behind HTTPS (set HTTPS=1 in production)
if os.environ.get("HTTPS"):
    app.config["SESSION_COOKIE_SECURE"] = True

# per-session email cache  { session_id -> [email_dict, ...] }
_caches: dict[str, list] = {}

# ---------------------------------------------------------------------------
# Gemini tools  (same as chat.py)
# ---------------------------------------------------------------------------

TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="list_emails",
            description="List recent emails from the inbox or a specific folder.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "folder": types.Schema(type=types.Type.STRING,  description="IMAP folder (default: INBOX)"),
                    "limit":  types.Schema(type=types.Type.INTEGER, description="Max emails to fetch (default 10, max 50)"),
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
            description="Permanently delete one or more emails by their numbers.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "numbers": types.Schema(
                        type=types.Type.ARRAY,
                        description="List of email numbers to delete e.g. [1,3,5]",
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
                    "to":      types.Schema(type=types.Type.STRING, description="Recipient email address"),
                    "subject": types.Schema(type=types.Type.STRING, description="Subject line"),
                    "body":    types.Schema(type=types.Type.STRING, description="Email body text"),
                    "cc":      types.Schema(type=types.Type.STRING, description="CC recipients (optional)"),
                },
                required=["to", "subject", "body"],
            ),
        ),
        types.FunctionDeclaration(
            name="scan_spam",
            description="Scan inbox for spam. Set delete_spam=true to also delete them.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "limit":       types.Schema(type=types.Type.INTEGER, description="Emails to scan (default 20, max 50)"),
                    "delete_spam": types.Schema(type=types.Type.BOOLEAN,  description="Delete detected spam? (default false)"),
                },
            ),
        ),
    ]
)

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _safe(text) -> str:
    return str(text or "").encode("ascii", errors="replace").decode("ascii")


def execute_tool(name: str, params: dict, creds: dict, sid: str) -> str:
    cache = _caches.get(sid, [])

    def make_client():
        return EmailClient(
            host=creds["imap_host"],
            port=int(creds["imap_port"]),
            email_address=creds["email"],
            password=creds["password"],
            smtp_host=creds["smtp_host"],
            smtp_port=int(creds["smtp_port"]),
        )

    try:
        if name == "list_emails":
            folder = params.get("folder", "INBOX")
            limit  = min(int(params.get("limit", 10)), 50)
            c = make_client(); c.connect()
            try:    emails = c.fetch_emails(folder=folder, limit=limit)
            finally: c.disconnect()
            _caches[sid] = emails
            if not emails:
                return f"No emails in {folder}."
            lines = [f"Found {len(emails)} emails in {folder}:\n"]
            for i, e in enumerate(emails, 1):
                subj   = _safe((e.get("subject") or "(no subject)")[:55])
                sender = _safe((e.get("sender")  or "?")[:35])
                d = e.get("date")
                date = d.strftime("%b %d, %H:%M") if hasattr(d, "strftime") else ""
                lines.append(f"[{i}] {subj}\n     From: {sender}  {date}")
            return "\n".join(lines)

        elif name == "read_email":
            number = int(params.get("number", 1))
            if not cache:
                return "No emails loaded. List emails first."
            idx = number - 1
            if not (0 <= idx < len(cache)):
                return f"Email #{number} not found. I have {len(cache)} loaded."
            e = cache[idx]
            body  = _safe(e.get("body") or "(empty)")
            lines = "\n".join(f"  {l}" for l in body.splitlines()[:80])
            return f"Subject: {_safe(e.get('subject'))}\nFrom: {_safe(e.get('sender'))}\n\n{lines}"

        elif name == "delete_email":
            numbers = params.get("numbers", [])
            if not isinstance(numbers, list):
                numbers = [numbers]
            if not cache:
                return "No emails loaded. List emails first."
            indices = sorted([int(n) - 1 for n in numbers], reverse=True)
            deleted, errors = [], []
            for idx in indices:
                if not (0 <= idx < len(cache)):
                    errors.append(f"#{idx+1} not found"); continue
                e = cache[idx]
                subj = _safe(e.get("subject") or "(no subject)")
                c = make_client(); c.connect()
                try:    ok = c.delete_email(e.get("id"))
                finally: c.disconnect()
                if ok:
                    _caches[sid].pop(idx)
                    deleted.append(f'"{subj}"')
                else:
                    errors.append(f'Failed to delete "{subj}"')
            result = f"Deleted {len(deleted)}: {', '.join(deleted)}" if deleted else ""
            if errors: result += " | Errors: " + ", ".join(errors)
            return result or "Nothing deleted."

        elif name == "send_email":
            c = make_client(); c.connect()
            try:    ok = c.send_email(to=params["to"], subject=params["subject"], body=params["body"], cc=params.get("cc"))
            finally: c.disconnect()
            return f'Sent to {params["to"]} — "{params["subject"]}"' if ok else "Failed to send."

        elif name == "scan_spam":
            limit       = min(int(params.get("limit", 20)), 50)
            delete_spam = bool(params.get("delete_spam", False))
            c = make_client(); c.connect()
            try:    emails = c.fetch_emails(folder="INBOX", limit=limit)
            finally: c.disconnect()
            _caches[sid] = emails
            detector = SpamDetector()
            spam_items = []
            for i, e in enumerate(emails, 1):
                r = detector.analyze_email(e)
                if r["is_spam"] and r["confidence"] >= 0.7:
                    spam_items.append({"idx": i, "email": e,
                                       "subj": _safe((e.get("subject") or "")[:55]),
                                       "conf": r["confidence"],
                                       "reason": _safe(r.get("reason", "")[:80])})
            if not spam_items:
                return f"Scanned {len(emails)} emails. Inbox is clean!"
            lines = [f"Scanned {len(emails)} emails. Found {len(spam_items)} spam:\n"]
            deleted = 0
            for s in spam_items:
                tag = ""
                if delete_spam:
                    c2 = make_client(); c2.connect()
                    try:
                        if c2.delete_email(s["email"].get("id")):
                            deleted += 1; tag = " [DELETED]"
                    finally: c2.disconnect()
                lines.append(f"[{s['idx']}] {s['subj']} ({s['conf']:.0%}){tag}\n     {s['reason']}")
            if delete_spam and deleted:
                lines.append(f"\nDeleted {deleted} spam email(s).")
            elif spam_items and not delete_spam:
                lines.append('\nSay "delete the spam" to remove them.')
            return "\n".join(lines)

        return f"Unknown tool: {name}"

    except Exception as ex:
        return f"Error in {name}: {type(ex).__name__}: {ex}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    return jsonify({"logged_in": bool(session.get("logged_in")),
                    "email": session.get("email", "")})


@app.route("/api/login", methods=["POST"])
def login():
    data      = request.json or {}
    email     = data.get("email", "").strip()
    password  = data.get("password", "").strip()
    imap_host = data.get("imap_host", "imap.gmail.com").strip()
    imap_port = int(data.get("imap_port", 993))
    smtp_host = data.get("smtp_host", "smtp.gmail.com").strip()
    smtp_port = int(data.get("smtp_port", 587))

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required."})

    try:
        client = EmailClient(host=imap_host, port=imap_port,
                             email_address=email, password=password,
                             smtp_host=smtp_host, smtp_port=smtp_port)
        client.connect()
        client.disconnect()
    except Exception as ex:
        err = str(ex)
        if any(k in err.lower() for k in ["authentication", "invalid credentials", "username and password"]):
            err += ("\n\nGmail tip: Use an App Password, not your regular password.\n"
                    "Google Account → Security → 2-Step Verification → App Passwords")
        return jsonify({"success": False, "error": err})

    session["email"]     = email
    session["password"]  = password
    session["imap_host"] = imap_host
    session["imap_port"] = imap_port
    session["smtp_host"] = smtp_host
    session["smtp_port"] = smtp_port
    session["logged_in"] = True
    return jsonify({"success": True, "email": email})


@app.route("/api/logout", methods=["POST"])
def logout():
    _caches.pop(request.cookies.get("session", ""), None)
    session.clear()
    return jsonify({"success": True})


@app.route("/api/chat", methods=["POST"])
def chat():
    if not session.get("logged_in"):
        return jsonify({"error": "Not logged in"}), 401

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY not set in .env"}), 500

    data     = request.json or {}
    messages = data.get("messages", [])
    creds    = {k: session.get(k) for k in ("email", "password", "imap_host", "imap_port", "smtp_host", "smtp_port")}
    creds["imap_port"] = creds.get("imap_port") or 993
    creds["smtp_port"] = creds.get("smtp_port") or 587
    user_email = creds["email"]
    sid        = request.cookies.get("session", "default")

    def generate():
        client = genai.Client(api_key=api_key)
        system = (f"You are a helpful email assistant for {user_email}. "
                  "Help manage emails: list, read, send, delete, scan spam. "
                  "Use numbered format when listing. Be concise and friendly.")

        history = []
        for m in messages[:-1]:
            role = "model" if m["role"] == "assistant" else "user"
            history.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))

        last_msg = messages[-1]["content"] if messages else ""

        try:
            chat_session = client.chats.create(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    tools=[TOOLS],
                ),
                history=history,
            )
            response = chat_session.send_message(last_msg)

            while True:
                text_parts, fc_list = [], []
                if response.candidates:
                    for part in response.candidates[0].content.parts:
                        if part.text:                                      text_parts.append(part.text)
                        if part.function_call and part.function_call.name: fc_list.append(part.function_call)

                if text_parts:
                    yield f"data: {json.dumps({'type':'text','content':' '.join(text_parts)})}\n\n"

                if not fc_list:
                    yield f"data: {json.dumps({'type':'done'})}\n\n"
                    break

                result_parts = []
                for fc in fc_list:
                    yield f"data: {json.dumps({'type':'action','name':fc.name})}\n\n"
                    result = execute_tool(fc.name, dict(fc.args), creds, sid)
                    result_parts.append(types.Part.from_function_response(name=fc.name, response={"result": result}))

                response = chat_session.send_message(result_parts)

        except Exception as ex:
            yield f"data: {json.dumps({'type':'error','content':str(ex)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Clean Mail — Web UI")
    print(f"  Open: http://0.0.0.0:{port}\n")
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
