"""
Westlake AI Assistant — self-contained module.
Owns its own login check, rate limiting, and file validation so it never
needs to import anything from app.py. To wire it in, app.py only needs:

    from ai_assistant import assistant_bp
    app.register_blueprint(assistant_bp)
"""
import os
import base64
import json
import logging
import time
from functools import wraps
from collections import defaultdict, deque

from flask import Blueprint, request, jsonify, session

try:
    import anthropic
    ANTHROPIC_SDK_AVAILABLE = True
except ImportError:
    ANTHROPIC_SDK_AVAILABLE = False

try:
    import filetype
    FILETYPE_AVAILABLE = True
except ImportError:
    FILETYPE_AVAILABLE = False

log = logging.getLogger(__name__)

assistant_bp = Blueprint('assistant', __name__, url_prefix='/api/assistant')

# ── Its own client, reading the env var directly ─────────────────────────
_ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
_client = (anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)
           if ANTHROPIC_SDK_AVAILABLE and _ANTHROPIC_API_KEY else None)

if not _client:
    log.warning("ai_assistant: ANTHROPIC_API_KEY not set or anthropic package "
                "not installed — assistant endpoint will return 503.")


# ── Its own auth check (mirrors app.py's, kept independent on purpose) ───
def _assistant_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "unauthorized"}), 401
        if session.get('status') != 'approved' and session.get('role') != 'admin':
            return jsonify({"error": "account pending approval"}), 403
        return f(*args, **kwargs)
    return decorated


# ── Its own in-memory rate limiter (no flask-limiter dependency) ─────────
_RATE_LIMIT = 30          # requests
_RATE_WINDOW = 60         # seconds
_request_log = defaultdict(deque)

def _rate_limited(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = session.get('user_id', request.remote_addr)
        now = time.time()
        q = _request_log[key]
        while q and now - q[0] > _RATE_WINDOW:
            q.popleft()
        if len(q) >= _RATE_LIMIT:
            return jsonify({"error": "Too many requests. Please slow down."}), 429
        q.append(now)
        return f(*args, **kwargs)
    return decorated


# ── Its own lightweight file-content check ────────────────────────────────
def _content_matches_extension(file_bytes, ext):
    ext = ext.lower()
    allowed = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'}
    if ext not in allowed:
        return False
    if FILETYPE_AVAILABLE:
        kind = filetype.guess(file_bytes)
        return bool(kind) and kind.mime == allowed[ext]
    if file_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return ext == 'png'
    if file_bytes.startswith(b'\xff\xd8\xff'):
        return ext in ('jpg', 'jpeg')
    return False


WESTLAKE_ASSISTANT_SYSTEM_PROMPT = """You are the in-app assistant for Westlake Insurance Agency's
agent portal (Kenyan motor insurance). You help agents:
- Read vehicle details out of a logbook photo they upload, when they attach one
- Answer questions about how to use the system (quotations, claims, renewals, payments)
- Explain general terms (comprehensive vs third-party, installment plans, DMVIC certificates)

You do NOT have access to live client/policy/premium data through this chat — if asked to
look something up in the database, tell the agent to check the relevant page (Clients,
Policies, Renewals) instead of guessing an answer.

Never state a specific premium figure — always tell the agent to use the New Quotation
page's calculator, since rates vary by insurer/product/cover and this chat cannot compute them.

Keep answers short and practical. This is a working agent mid-task, not a customer."""

LOGBOOK_ASSISTANT_PROMPT = """Read this logbook photo and extract these fields as JSON only,
no other text, no markdown fences:
{"vehicle_reg":"","chassis_number":"","engine_number":"","vehicle_make":"","vehicle_model":"",
 "body_type":"","year_of_manufacture":null,"year_of_registration":null,"seats":null}
Use null for anything not clearly visible. Do not guess."""


@assistant_bp.route('/message', methods=['POST'])
@_assistant_login_required
@_rate_limited
def assistant_message():
    if not _client:
        return jsonify({"error": "The assistant is not configured on this server."}), 503

    history_raw = request.form.get('history', '[]')
    try:
        history = json.loads(history_raw)
        if not isinstance(history, list):
            history = []
    except (json.JSONDecodeError, TypeError):
        history = []
    history = history[-12:]

    user_text = (request.form.get('text') or '').strip()
    image_file = request.files.get('image')

    if not user_text and not image_file:
        return jsonify({"error": "Type a message or attach a photo."}), 400

    content_blocks = []
    is_logbook_mode = False

    if image_file and image_file.filename:
        ext = image_file.filename.rsplit('.', 1)[-1].lower() if '.' in image_file.filename else ''
        if ext not in ('png', 'jpg', 'jpeg'):
            return jsonify({"error": "Attach a PNG or JPEG photo."}), 400
        file_bytes = image_file.read()
        if len(file_bytes) > 10 * 1024 * 1024:
            return jsonify({"error": "Image too large (max 10MB)."}), 400
        if not _content_matches_extension(file_bytes[:2048], ext):
            return jsonify({"error": "File content doesn't match its extension."}), 400
        media_type = 'image/png' if ext == 'png' else 'image/jpeg'
        content_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type,
                      "data": base64.b64encode(file_bytes).decode('ascii')},
        })
        is_logbook_mode = True
        content_blocks.append({"type": "text", "text": LOGBOOK_ASSISTANT_PROMPT})
    else:
        content_blocks.append({"type": "text", "text": user_text})

    messages = history + [{"role": "user", "content": content_blocks}]

    try:
        if is_logbook_mode:
            response = _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=messages,
            )
        else:
            response = _client.messages.create(
                model="claude-sonnet-5",
                max_tokens=600,
                system=WESTLAKE_ASSISTANT_SYSTEM_PROMPT,
                messages=messages,
            )
        reply_text = "".join(block.text for block in response.content if block.type == "text").strip()
    except Exception as e:
        log.error("Assistant chat error: %s", type(e).__name__)
        return jsonify({"error": "Assistant is temporarily unavailable. Please try again."}), 502

    extracted = None
    if is_logbook_mode:
        cleaned = reply_text.replace("```json", "").replace("```", "").strip()
        try:
            extracted = json.loads(cleaned)
            reply_text = "I've read the logbook — check the extracted details below and correct anything that's wrong before using them."
        except json.JSONDecodeError:
            extracted = None
            reply_text = "I couldn't read that logbook clearly. Try a clearer, well-lit photo, or enter the details manually."

    return jsonify({
        "reply": reply_text,
        "extracted": extracted,
        "user_content": content_blocks if not is_logbook_mode else [{"type": "text", "text": "[Logbook photo]"}],
        "assistant_content": [{"type": "text", "text": reply_text}],
    })