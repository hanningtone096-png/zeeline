"""
Westlake AI Assistant — self-contained module.
Talks to app.py's own REST API internally (via test_client) rather than
importing app.py's functions directly, so it stays decoupled and never
duplicates business logic. To wire it in, app.py needs:

    from ai_assistant import assistant_bp, init_assistant
    app.register_blueprint(assistant_bp)
    init_assistant(app)
"""
import os
import base64
import json
import logging
import time
import uuid
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

_ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
_client = (anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)
           if ANTHROPIC_SDK_AVAILABLE and _ANTHROPIC_API_KEY else None)

if not _client:
    log.warning("ai_assistant: ANTHROPIC_API_KEY not set or anthropic package "
                "not installed — assistant endpoint will return 503.")

# Set by init_assistant(app) at startup — the one dependency this module has
# on app.py, needed to make in-process calls to its own routes.
_flask_app = None


def init_assistant(app):
    global _flask_app
    _flask_app = app


def _assistant_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "unauthorized"}), 401
        if session.get('status') != 'approved' and session.get('role') != 'admin':
            return jsonify({"error": "account pending approval"}), 403
        return f(*args, **kwargs)
    return decorated


_RATE_LIMIT = 30
_RATE_WINDOW = 60
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


# ── In-process calls to app.py's own routes ──────────────────────────────
def _call_internal_api(method, path, session_cookie, csrf_token, json_body=None, query_params=None):
    if _flask_app is None:
        return 500, {"error": "Assistant is not fully initialized (init_assistant not called)."}
    with _flask_app.test_client() as client:
        if session_cookie:
            client.set_cookie('session', session_cookie)
        headers = {'X-CSRFToken': csrf_token} if csrf_token else {}
        try:
            if method == 'GET':
                resp = client.get(path, query_string=query_params or {}, headers=headers)
            elif method == 'POST':
                resp = client.post(path, json=json_body or {}, headers=headers)
            elif method == 'DELETE':
                resp = client.delete(path, headers=headers)
            else:
                return 500, {"error": f"Unsupported method {method}"}
            data = resp.get_json(silent=True)
            return resp.status_code, data
        except Exception as e:
            log.error("Internal API call failed (%s %s): %s", method, path, type(e).__name__)
            return 500, {"error": "Internal call failed."}


# ── Tool catalog: name -> (method, path template, requires_confirmation) ──
TOOL_ROUTES = {
    "dashboard_stats":               ("GET",  "/api/dashboard/stats", False),
    "list_policies":                 ("GET",  "/api/policies/list", False),
    "list_clients":                  ("GET",  "/api/clients/list", False),
    "add_client":                    ("POST", "/api/clients/add", False),
    "check_double_insurance":        ("GET",  "/api/policies/check-double-insurance", False),
    "get_insurer_products":          ("GET",  "/api/insurers/products", False),
    "generate_quotation":            ("POST", "/api/quotations/generate", False),
    "buy_cover":                     ("POST", "/api/quotations/buy", False),
    "list_quotations":               ("GET",  "/api/quotations/list", False),
    "initiate_mpesa_stk":            ("POST", "/api/mpesa/stk", True),
    "check_mpesa_status":            ("POST", "/api/mpesa/query", False),
    "submit_claim":                  ("POST", "/api/claims", False),
    "list_claims":                   ("GET",  "/api/claims", False),
    "update_claim_status":           ("POST", "/api/claims/{claim_id}/status", False),
    "list_agents":                   ("GET",  "/api/admin/agents", False),
    "set_agent_status":              ("POST", "/api/admin/agents/{agent_id}/status", False),
    "delete_agent":                  ("DELETE", "/api/admin/users/{agent_id}", False),
    "unflag_agent":                  ("POST", "/api/admin/agents/{agent_id}/unflag", False),
    "list_renewals":                 ("GET",  "/api/renewals/list", False),
    "renew_policy":                  ("POST", "/api/renewals/renew", False),
    "list_pending_declarations":     ("GET",  "/api/admin/declarations/pending", False),
    "send_declaration":              ("POST", "/api/admin/declarations/send", True),
    "list_dmvic_pending":            ("GET",  "/api/admin/dmvic/pending-confirmations", False),
    "confirm_dmvic_issuance":        ("POST", "/api/dmvic/confirm-issuance", True),
}

CONFIRM_REQUIRED = {name for name, (_, _, confirm) in TOOL_ROUTES.items() if confirm}

# ── Tool schemas for Claude ────────────────────────────────────────────────
TOOLS = [
    {"name": "dashboard_stats", "description": "Get current dashboard KPI stats for this user.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_policies", "description": "List policies (own for agents, all for admin).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_clients", "description": "List clients (own for agents, all for admin).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "add_client", "description": "Create a new client record.",
     "input_schema": {"type": "object", "properties": {
         "first_name": {"type": "string"}, "last_name": {"type": "string"},
         "phone": {"type": "string"}, "id_number": {"type": "string"},
         "kra_pin": {"type": "string"}, "vehicle_reg": {"type": "string"},
         "email": {"type": "string"}}, "required": ["first_name", "last_name", "phone"]}},
    {"name": "check_double_insurance", "description": "Check if a vehicle reg/chassis already has an active policy locally or on DMVIC.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "vehicle_reg": {"type": "string"},
         "chassis_number": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_insurer_products", "description": "Get the catalog of products/covers each insurer offers.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "generate_quotation", "description": "Generate an insurance quotation. Gather ALL required fields from the agent in conversation before calling this — do not guess values.",
     "input_schema": {"type": "object", "properties": {
         "company": {"type": "string", "enum": ["monarch", "directline", "definite"]},
         "type_of_cover": {"type": "string", "enum": ["comprehensive", "third_party_only", "third_party_fire_theft"]},
         "type_of_certificate": {"type": "string", "enum": ["annual", "30_days", "14_days", "7_days", "inst_2", "inst_3"]},
         "product": {"type": "string"}, "sub_type": {"type": "string"},
         "commencing_date": {"type": "string"}, "expiry_date": {"type": "string"},
         "policy_holder_name": {"type": "string"}, "phone": {"type": "string"},
         "email": {"type": "string"}, "kra_pin": {"type": "string"},
         "id_number": {"type": "string"}, "postal_address": {"type": "string"},
         "vehicle_reg": {"type": "string"}, "chassis_number": {"type": "string"},
         "engine_number": {"type": "string"}, "vehicle_make": {"type": "string"},
         "vehicle_model": {"type": "string"}, "vehicle_body_type": {"type": "string"},
         "seats": {"type": "integer"}, "vehicle_value": {"type": "number"},
         "tonnage": {"type": "number"}, "pax": {"type": "integer"},
         "year_of_manufacture": {"type": "integer"}, "year_of_registration": {"type": "integer"},
         "business_type": {"type": "string", "enum": ["new", "extension"]},
         "parent_policy_no": {"type": "string"},
     }, "required": ["company", "type_of_cover", "type_of_certificate", "product",
                      "commencing_date", "expiry_date", "policy_holder_name", "phone",
                      "kra_pin", "vehicle_reg", "chassis_number", "vehicle_body_type", "seats"]}},
    {"name": "buy_cover", "description": "Convert a generated quotation into a pending-payment policy.",
     "input_schema": {"type": "object", "properties": {"quote_id": {"type": "string"}}, "required": ["quote_id"]}},
    {"name": "list_quotations", "description": "List recent quotations.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "initiate_mpesa_stk", "description": "Send an M-Pesa STK push to collect payment for a policy. REQUIRES USER CONFIRMATION — this sends a real payment prompt to the client's phone.",
     "input_schema": {"type": "object", "properties": {
         "policy_no": {"type": "string"}, "phone": {"type": "string"},
         "amount": {"type": "number"}}, "required": ["policy_no", "phone", "amount"]}},
    {"name": "check_mpesa_status", "description": "Check the status of a previously sent M-Pesa STK push.",
     "input_schema": {"type": "object", "properties": {
         "checkout_request_id": {"type": "string"}, "policy_no": {"type": "string"}},
         "required": ["checkout_request_id"]}},
    {"name": "submit_claim", "description": "Lodge a new insurance claim.",
     "input_schema": {"type": "object", "properties": {
         "claim_policy": {"type": "string"}, "incident_date": {"type": "string"},
         "incident_type": {"type": "string"}, "incident_desc": {"type": "string"}},
         "required": ["claim_policy", "incident_date", "incident_type", "incident_desc"]}},
    {"name": "list_claims", "description": "List claims (own for agents, all for admin).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "update_claim_status", "description": "Admin only: approve or reject a claim.",
     "input_schema": {"type": "object", "properties": {
         "claim_id": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "approved", "rejected"]}},
         "required": ["claim_id", "status"]}},
    {"name": "list_agents", "description": "Admin only: list all agent accounts.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_agent_status", "description": "Admin only: approve, reject, or suspend an agent account.",
     "input_schema": {"type": "object", "properties": {
         "agent_id": {"type": "integer"}, "status": {"type": "string", "enum": ["approved", "rejected", "suspended"]}},
         "required": ["agent_id", "status"]}},
    {"name": "delete_agent", "description": "Admin only: PERMANENTLY delete an agent and all their clients, quotations, policies, and claims. Irreversible.",
     "input_schema": {"type": "object", "properties": {"agent_id": {"type": "integer"}}, "required": ["agent_id"]}},
    {"name": "unflag_agent", "description": "Admin only: clear an agent's underpayment flag.",
     "input_schema": {"type": "object", "properties": {"agent_id": {"type": "integer"}}, "required": ["agent_id"]}},
    {"name": "list_renewals", "description": "List active policies eligible for renewal.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "renew_policy", "description": "Renew a policy by one year.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "list_pending_declarations", "description": "Admin only: list DMVIC-issued certificates awaiting declaration to insurers.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_declaration", "description": "Admin only: send a certificate declaration email to an insurer. REQUIRES USER CONFIRMATION — this emails a real external party and records a payment confirmation.",
     "input_schema": {"type": "object", "properties": {
         "company": {"type": "string"}, "policy_nos": {"type": "array", "items": {"type": "string"}},
         "mpesa_message": {"type": "string"}}, "required": ["company", "policy_nos", "mpesa_message"]}},
    {"name": "list_dmvic_pending", "description": "Admin only: list DMVIC policy alerts awaiting manual review.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "confirm_dmvic_issuance", "description": "Confirm or reject a DMVIC policy alert after manual review. REQUIRES USER CONFIRMATION — this is a regulator-facing action.",
     "input_schema": {"type": "object", "properties": {
         "policy_no": {"type": "string"}, "is_approved": {"type": "boolean"},
         "is_logbook_verified": {"type": "boolean"}, "is_vehicle_inspected": {"type": "boolean"},
         "additional_comments": {"type": "string"}},
         "required": ["policy_no", "is_approved", "is_logbook_verified", "is_vehicle_inspected"]}},
]

WESTLAKE_AGENT_SYSTEM_PROMPT = """You are the AI assistant for Westlake Insurance Agency's agent
portal (Kenyan motor insurance). You can directly perform actions in the system using the
tools available to you — generating quotations, buying cover, managing clients and claims,
agent administration, renewals, and DMVIC/declaration workflows.

Rules:
- Before calling generate_quotation, gather every required field from the user in conversation.
  Never invent a KRA PIN, phone number, chassis number, or other identifying detail — ask for it.
- Never state a specific premium figure from memory — always get it from generate_quotation's result.
- Some tools require the user's explicit confirmation before they run (you'll see this reflected
  in how the conversation proceeds — just call the tool normally when you're ready to act; the
  system handles pausing for confirmation on the ones that need it).
- Delete/approve/reject/suspend on agent accounts take effect immediately with no undo — be certain
  before calling them, and if the user's instruction is ambiguous about WHICH agent, ask first.
- Keep replies short and practical. Report tool results plainly (what happened, key numbers/IDs).
- If a tool call fails, tell the user what went wrong in plain language, don't retry blindly."""

LOGBOOK_ASSISTANT_PROMPT = """Read this logbook photo and extract these fields as JSON only,
no other text, no markdown fences:
{"vehicle_reg":"","chassis_number":"","engine_number":"","vehicle_make":"","vehicle_model":"",
 "body_type":"","year_of_manufacture":null,"year_of_registration":null,"seats":null}
Use null for anything not clearly visible. Do not guess."""


def _tool_summary(tool_name, tool_input):
    """Human-readable one-liner for the confirmation prompt."""
    if tool_name == "initiate_mpesa_stk":
        return (f"Send an M-Pesa payment prompt for KES {tool_input.get('amount')} "
                f"to {tool_input.get('phone')} for policy {tool_input.get('policy_no')}?")
    if tool_name == "send_declaration":
        count = len(tool_input.get('policy_nos', []))
        return (f"Send a certificate declaration for {count} certificate(s) to "
                f"{tool_input.get('company', '').title()}? This emails the insurer directly.")
    if tool_name == "confirm_dmvic_issuance":
        action = "APPROVE and issue" if tool_input.get('is_approved') else "REJECT"
        return f"{action} the DMVIC certificate for policy {tool_input.get('policy_no')}?"
    return f"Proceed with {tool_name} ({json.dumps(tool_input)})?"


_pending_confirmations = {}
_PENDING_TTL = 600  # seconds


def _cleanup_pending():
    now = time.time()
    expired = [t for t, v in _pending_confirmations.items() if now - v['created_at'] > _PENDING_TTL]
    for t in expired:
        _pending_confirmations.pop(t, None)


def _execute_tool(tool_name, tool_input, session_cookie, csrf_token):
    route = TOOL_ROUTES.get(tool_name)
    if not route:
        return {"error": f"Unknown tool: {tool_name}"}
    method, path_template, _ = route
    path = path_template.format(**{k: tool_input.get(k, '') for k in tool_input})
    query_params = tool_input if method == 'GET' else None
    json_body = tool_input if method in ('POST',) else None
    status, data = _call_internal_api(method, path, session_cookie, csrf_token,
                                       json_body=json_body, query_params=query_params)
    if data is None:
        data = {"error": f"No response body (HTTP {status})"}
    return data


def _run_agent_loop(messages, session_cookie, csrf_token, max_iterations=6):
    """Runs the tool-use loop until Claude produces a final text answer or
    hits a tool requiring confirmation. Returns either:
      {"done": True, "reply": str, "messages": [...]}
    or
      {"done": False, "pending": {...}, "messages": [...]}
    """
    for _ in range(max_iterations):
        response = _client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            system=WESTLAKE_AGENT_SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            reply_text = "".join(b.text for b in response.content if b.type == "text").strip()
            return {"done": True, "reply": reply_text or "Done.", "messages": messages}

        assistant_content = [b.model_dump() if hasattr(b, 'model_dump') else b for b in response.content]
        tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)
        if not tool_use_block:
            reply_text = "".join(b.text for b in response.content if b.type == "text").strip()
            return {"done": True, "reply": reply_text or "Done.", "messages": messages}

        messages = messages + [{"role": "assistant", "content": assistant_content}]

        if tool_use_block.name in CONFIRM_REQUIRED:
            return {
                "done": False,
                "messages": messages,
                "pending": {
                    "tool_use_id": tool_use_block.id,
                    "tool_name": tool_use_block.name,
                    "tool_input": tool_use_block.input,
                    "summary": _tool_summary(tool_use_block.name, tool_use_block.input),
                },
            }

        result = _execute_tool(tool_use_block.name, tool_use_block.input, session_cookie, csrf_token)
        messages = messages + [{
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_block.id, "content": json.dumps(result)}],
        }]

    return {"done": True, "reply": "This is taking more steps than expected — please try rephrasing your request.", "messages": messages}


@assistant_bp.route('/message', methods=['POST'])
@_assistant_login_required
@_rate_limited
def assistant_message():
    if not _client:
        return jsonify({"error": "The assistant is not configured on this server."}), 503

    _cleanup_pending()

    history_raw = request.form.get('history', '[]')
    try:
        history = json.loads(history_raw)
        if not isinstance(history, list):
            history = []
    except (json.JSONDecodeError, TypeError):
        history = []
    history = history[-20:]

    user_text = (request.form.get('text') or '').strip()
    image_file = request.files.get('image')
    session_cookie = request.cookies.get('session')
    csrf_token = request.headers.get('X-CSRFToken', '')

    if not user_text and not image_file:
        return jsonify({"error": "Type a message or attach a photo."}), 400

    # ── Logbook photo path: pure vision extraction, no tools involved ──
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
        try:
            response = _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                  "data": base64.b64encode(file_bytes).decode('ascii')}},
                    {"type": "text", "text": LOGBOOK_ASSISTANT_PROMPT},
                ]}],
            )
            raw_text = "".join(b.text for b in response.content if b.type == "text").strip()
            cleaned = raw_text.replace("```json", "").replace("```", "").strip()
            extracted = json.loads(cleaned)
            reply_text = "I've read the logbook — check the extracted details below and correct anything that's wrong before using them."
        except json.JSONDecodeError:
            extracted = None
            reply_text = "I couldn't read that logbook clearly. Try a clearer, well-lit photo, or enter the details manually."
        except Exception as e:
            log.error("Logbook extraction error: %s", type(e).__name__)
            return jsonify({"error": "Could not process the image. Please try again."}), 502

        return jsonify({
            "reply": reply_text, "extracted": extracted,
            "user_content": [{"type": "text", "text": "[Logbook photo]"}],
            "assistant_content": [{"type": "text", "text": reply_text}],
        })

    # ── Agentic text path ──
    user_content = [{"type": "text", "text": user_text}]
    messages = history + [{"role": "user", "content": user_content}]

    try:
        result = _run_agent_loop(messages, session_cookie, csrf_token)
    except Exception as e:
        log.error("Assistant agent loop error: %s", type(e).__name__)
        return jsonify({"error": "Assistant is temporarily unavailable. Please try again."}), 502

    if result["done"]:
        return jsonify({
            "reply": result["reply"],
            "extracted": None,
            "user_content": user_content,
            "assistant_content": [{"type": "text", "text": result["reply"]}],
        })

    token = uuid.uuid4().hex
    _pending_confirmations[token] = {
        "messages": result["messages"],
        "pending": result["pending"],
        "session_cookie": session_cookie,
        "csrf_token": csrf_token,
        "created_at": time.time(),
        "user_id": session.get('user_id'),
    }
    return jsonify({
        "reply": None,
        "confirmation_required": {"token": token, "summary": result["pending"]["summary"]},
    })


@assistant_bp.route('/confirm', methods=['POST'])
@_assistant_login_required
@_rate_limited
def assistant_confirm():
    if not _client:
        return jsonify({"error": "The assistant is not configured on this server."}), 503

    body = request.get_json() or {}
    token = body.get('token')
    approved = bool(body.get('approved'))

    pending = _pending_confirmations.pop(token, None)
    if not pending:
        return jsonify({"error": "This confirmation has expired. Please ask again."}), 410
    if pending.get('user_id') != session.get('user_id'):
        return jsonify({"error": "unauthorized"}), 403

    tool_info = pending['pending']
    if approved:
        result_data = _execute_tool(tool_info['tool_name'], tool_info['tool_input'],
                                     pending['session_cookie'], pending['csrf_token'])
    else:
        result_data = {"cancelled": True, "message": "The user declined to proceed. Action was not performed."}

    messages = pending['messages'] + [{
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_info['tool_use_id'], "content": json.dumps(result_data)}],
    }]

    try:
        result = _run_agent_loop(messages, pending['session_cookie'], pending['csrf_token'])
    except Exception as e:
        log.error("Assistant confirm-resume error: %s", type(e).__name__)
        return jsonify({"error": "Assistant is temporarily unavailable. Please try again."}), 502

    if result["done"]:
        return jsonify({
            "reply": result["reply"],
            "history_append": messages[len(pending['messages']):] + [
                {"role": "assistant", "content": [{"type": "text", "text": result["reply"]}]}
            ],
        })

    new_token = uuid.uuid4().hex
    _pending_confirmations[new_token] = {
        "messages": result["messages"], "pending": result["pending"],
        "session_cookie": pending['session_cookie'], "csrf_token": pending['csrf_token'],
        "created_at": time.time(), "user_id": session.get('user_id'),
    }
    return jsonify({
        "reply": None,
        "confirmation_required": {"token": new_token, "summary": result["pending"]["summary"]},
    })