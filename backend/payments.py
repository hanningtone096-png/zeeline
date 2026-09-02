"""
Westlake payments module — ArchPay/M-Pesa integration, payment verification,
and policy-purchase settlement, as a self-contained Blueprint.

Like ai_assistant.py, this module never imports functions directly from
app.py (that would create a circular import: app.py -> payments.py ->
app.py). Instead app.py calls init_payments(app, ...) once at startup and
hands over the handful of things this module needs — the DB query function,
mongo_store, the background task queue, DMVIC issuance, cache invalidation,
Monarch policy-numbering, and the underpayment-alert emailer. Everything else
here (ArchPay config, STK push, verify, callback, buy_cover) is self-contained.
"""

import os
import time
import uuid
import hmac as _hmac
import logging
import ipaddress
from datetime import date
from collections import defaultdict, deque
from functools import wraps

import requests
from flask import Blueprint, request, jsonify, session, abort

log = logging.getLogger(__name__)

payments_bp = Blueprint('payments', __name__, url_prefix='/api')

ENV = os.environ.get('FLASK_ENV', 'production').lower()
IS_PRODUCTION = ENV == 'production'


def require_env(name, allow_dev_fallback=None):
    val = os.environ.get(name)
    if val:
        return val
    if not IS_PRODUCTION and allow_dev_fallback is not None:
        log.warning("%s not set — using LOCAL DEV placeholder. Never do this in production.", name)
        return allow_dev_fallback
    raise RuntimeError(
        f"Required environment variable '{name}' is not set. Refusing to start "
        f"with a hardcoded credential. Set it in your environment/.env before running."
    )


_query = None
_mongo_store = None
_enqueue = None
_issue_dmvic_certificate = None
_cache_delete_prefix = None
_safe_error_response = None
_monarch_policy_class = None
_next_monarch_policy_no = None
_notify_underpayment_attempt = None
_push_notification = None


def init_payments(app, *, csrf, query, mongo_store, enqueue, issue_dmvic_certificate,
                   cache_delete_prefix, safe_error_response, monarch_policy_class,
                   next_monarch_policy_no, notify_underpayment_attempt, push_notification):
    global _query, _mongo_store, _enqueue, _issue_dmvic_certificate
    global _cache_delete_prefix, _safe_error_response, _monarch_policy_class
    global _next_monarch_policy_no, _notify_underpayment_attempt, _push_notification
    _query = query
    _mongo_store = mongo_store
    _enqueue = enqueue
    _issue_dmvic_certificate = issue_dmvic_certificate
    _cache_delete_prefix = cache_delete_prefix
    _safe_error_response = safe_error_response
    _monarch_policy_class = monarch_policy_class
    _next_monarch_policy_no = next_monarch_policy_no
    _notify_underpayment_attempt = notify_underpayment_attempt
    _push_notification = push_notification

    csrf.exempt(mpesa_callback)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def approved_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('status') != 'approved' and session.get('role') != 'admin':
            return jsonify({"error": "account pending approval"}), 403
        return f(*args, **kwargs)
    return decorated


_request_log = defaultdict(deque)


def rate_limited(limit, window=60):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            key = (f.__name__, session.get('user_id', request.remote_addr))
            now = time.time()
            q = _request_log[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return jsonify({"error": "Too many requests. Please slow down."}), 429
            q.append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator


ARCHPAY_MODE            = os.environ.get('ARCHPAY_MODE', 'live')
ARCHPAY_BASE_URL        = os.environ.get('ARCHPAY_BASE_URL', 'https://pay.archietech.app/api/v1').rstrip('/')
ARCHPAY_API_KEY         = require_env('ARCHPAY_API_KEY', allow_dev_fallback='')
ARCHPAY_CHANNEL_ID      = os.environ.get('ARCHPAY_CHANNEL_ID', '').strip()
ARCHPAY_CALLBACK_SECRET = require_env('ARCHPAY_CALLBACK_SECRET', allow_dev_fallback=os.urandom(16).hex())
ARCHPAY_WEBHOOK_URL     = os.environ.get('ARCHPAY_WEBHOOK_URL', '').strip()

MPESA_ENV = ARCHPAY_MODE
MPESA_CALLBACK_SECRET = ARCHPAY_CALLBACK_SECRET

MAX_UNDERPAYMENT_ALLOWED = 800


def mpesa_format_phone(phone):
    phone = str(phone).strip().replace(' ', '').replace('-', '').replace('+', '')
    if phone.startswith('0') and len(phone) == 10:
        phone = '254' + phone[1:]
    if phone.startswith('254') and len(phone) == 12:
        return phone
    return None


def mpesa_stk_push(phone, amount, account_ref, description='Insurance Premium'):
    if not ARCHPAY_API_KEY:
        return {'success': False, 'error': 'ArchPay API key is not configured.'}
    phone_fmt = mpesa_format_phone(phone)
    if not phone_fmt:
        return {'success': False, 'error': f'Invalid phone number: {phone}'}
    amount_int = max(1, int(round(float(amount))))
    payload = {
        "phone": phone_fmt,
        "amount": amount_int,
        "accountReference": str(account_ref)[:12],
        "description": str(description)[:20],
    }
    if ARCHPAY_CHANNEL_ID:
        payload["channelId"] = ARCHPAY_CHANNEL_ID
    if ARCHPAY_WEBHOOK_URL:
        payload["callbackUrl"] = ARCHPAY_WEBHOOK_URL

    try:
        res = requests.post(
            f"{ARCHPAY_BASE_URL}/stkpush",
            json=payload,
            headers={"x-api-key": ARCHPAY_API_KEY, "Content-Type": "application/json"},
            timeout=20,
        )
        data = res.json()
        log.info("ArchPay STK push status=%s success=%s", res.status_code, data.get('success'))
        if res.ok and data.get('success'):
            return {
                'success':             True,
                'checkout_request_id': data.get('checkoutRequestId'),
                'merchant_request_id': data.get('merchantRequestId'),
                'customer_message':    data.get('message') or 'Check your phone for the M-Pesa prompt.',
                'credits_remaining':   data.get('creditsRemaining'),
                'channel_id':          data.get('channelId'),
            }
        return {
            'success': False,
            'error': data.get('error') or data.get('message') or 'STK push failed',
            'code': data.get('code'),
        }
    except Exception as e:
        log.error("ArchPay STK push error: %s", type(e).__name__)
        return {'success': False, 'error': 'Could not reach ArchPay. Please try again.'}


def mpesa_query_status(checkout_request_id):
    if not ARCHPAY_API_KEY:
        return {'success': False, 'error': 'ArchPay API key is not configured.'}
    try:
        res = requests.post(
            f"{ARCHPAY_BASE_URL}/verify",
            json={"checkoutRequestId": checkout_request_id},
            headers={"x-api-key": ARCHPAY_API_KEY, "Content-Type": "application/json"},
            timeout=15,
        )
        data = res.json()
        status = (data.get('status') or '').strip().lower()
        receipt = data.get('mpesaReceiptNumber')
        terminal_failures = {'cancelled', 'canceled', 'timeout', 'reversed'}
        paid = bool(res.ok and (
            (data.get('success') and status in {'completed', 'success', 'successful', 'paid'})
            or (receipt and status not in terminal_failures)
        ))
        if paid and receipt and status == 'failed':
            log.warning("ArchPay verify returned a receipt with failed status; accepting receipt as settled")
        log.info("ArchPay verify status=%s payment_status=%s", res.status_code, status)
        return {
            'success': paid,
            'status': status,
            'mpesa_receipt_number': receipt,
            'amount': data.get('amount'),
            'phone': data.get('phone'),
            'transaction_date': data.get('transactionDate'),
            'result_desc': data.get('error') or status or 'Payment not completed',
            'code': data.get('code'),
        }
    except Exception as e:
        log.error("ArchPay verify error: %s", type(e).__name__)
        return {'success': False, 'error': 'Could not verify payment status. Please try again.'}


def hmac_compare(a, b):
    return _hmac.compare_digest(str(a), str(b))


def _request_ip():
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or ''


def _is_safaricom_ip(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return False


def activate_paid_policy_and_enqueue_dmvic(policy_no, *, source, reference, user_id=None):
    policy = _mongo_store.activate_policy_after_payment(policy_no)
    if not policy:
        return False

    # TEMP(payment-bypass): while the payment gate is lifted, buy_cover
    # enqueues DMVIC issuance the moment the policy is created, so by the
    # time payment settles the certificate has often already been issued.
    # Re-enqueueing here would ask DMVIC for a duplicate certificate.
    settled = {'issued', 'failed', 'pending_manual', 'pending_confirmation', 'unsupported'}
    if (policy.get('dmvic_status') or '') in settled:
        log.info("Skipping post-payment DMVIC enqueue for %s (dmvic_status=%s already settled)",
                 policy_no, policy.get('dmvic_status'))
        return True

    quote = _query("SELECT * FROM quotations WHERE id=%s", (policy.get('quote_id'),), fetchone=True)
    if quote:
        _enqueue("dmvic_issue_certificate", _issue_dmvic_certificate, policy_no, quote)
    else:
        log.error("Payment settled for %s but quotation %s was not found", policy_no, policy.get('quote_id'))
        _query("""UPDATE policies
                  SET dmvic_status='failed', dmvic_error=%s
                  WHERE policy_no=%s""",
               ("Payment confirmed, but the quotation required for DMVIC issuance is unavailable.", policy_no),
               commit=True)

    if user_id is None:
        _query("INSERT INTO audit_log (action, detail) VALUES (%s,%s)",
               (source, f"policy={policy_no} ref={reference}"), commit=True)
    else:
        _query("INSERT INTO audit_log (user_id, action, detail) VALUES (%s,%s,%s)",
               (user_id, source, f"policy={policy_no} ref={reference}"), commit=True)
    _cache_delete_prefix("cache:dashboard")
    _cache_delete_prefix("cache:reports_summary")
    return True


def _policy_fully_paid(policy_no):
    """Return True when the sum of completed payments for `policy_no` covers
    its total_payable. Used to gate policy activation so that a single partial
    M-Pesa payment cannot activate a policy that has not been fully settled."""
    policy = _query("SELECT total_payable FROM policies WHERE policy_no=%s",
                    (policy_no,), fetchone=True)
    if not policy:
        return False
    quoted = round(float(policy.get('total_payable') or 0), 2)
    if quoted <= 0:
        return False
    paid_row = _query("""SELECT COALESCE(SUM(amount),0) AS paid
                         FROM payments
                         WHERE policy_no=%s AND status='completed'""",
                      (policy_no,), fetchone=True) or {}
    paid = round(float(paid_row.get('paid', 0) or 0), 2)
    return paid >= quoted


def _settle_mpesa_payment(ref, policy_no, *, source, reference, user_id=None):
    """Mark the M-Pesa payment for `ref` completed, then activate the policy
    only once it is fully paid.

    A single (possibly partial) successful payment must NOT activate a policy.
    Partial payments accumulate (each as its own completed `payments` row) and
    the policy is activated only when the quoted premium is covered. Returns
    True if the policy transitioned to active on this call, False otherwise.
    """
    # `reference` (the ArchPay checkout request id) is unique per STK push, so
    # this touches exactly one row. Policy activation below is itself idempotent
    # (activate_policy_after_payment only transitions pending_payment->active
    # once), so a duplicate settlement call is a no-op rather than double-issue.
    _query("UPDATE payments SET status='completed', paid_at=NOW() WHERE reference=%s",
           (ref,), commit=True)

    if not _policy_fully_paid(policy_no):
        log.info("Payment %s settled but policy %s is not fully paid; not activating.",
                 ref, policy_no)
        _query("INSERT INTO audit_log (action, detail) VALUES (%s,%s)",
               (source + '_partial', f"policy={policy_no} ref={reference}"), commit=True)
        return False
    return activate_paid_policy_and_enqueue_dmvic(
        policy_no, source=source, reference=reference, user_id=user_id,
    )


@payments_bp.route('/quotations/buy', methods=['POST'])
@login_required
@approved_required
def buy_cover():
    d = request.get_json() or {}
    quote_id = d.get('quote_id', '').strip()
    if not quote_id:
        return jsonify({"error": "quote_id required"}), 400

    q = _query("SELECT * FROM quotations WHERE id=%s", (quote_id,), fetchone=True)
    if not q:
        return jsonify({"error": "Quotation not found"}), 404
    if session['role'] != 'admin' and q['agent_id'] != session['user_id']:
        return jsonify({"error": "You do not have access to this quotation"}), 403
    if q['status'] == 'converted':
        return jsonify({"error": "Policy already created for this quotation"}), 409

    client = _query("""
        SELECT id FROM clients
        WHERE  phone=%s AND agent_id=%s
    """, (q['phone'], q['agent_id']), fetchone=True)

    if not client:
        client_id = _query("""
            INSERT INTO clients
                (agent_id, first_name, last_name, phone, kra_pin, vehicle_reg, email)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            q['agent_id'],
            q['policy_holder_name'], '',
            q['phone'], q.get('kra_pin', ''),
            q.get('vehicle_reg', ''), q.get('email', ''),
        ), commit=True)
    else:
        client_id = client['id']

    if (q.get('company') or '').lower() == 'monarch':
        m_class = _monarch_policy_class(q.get('product'))
        if m_class:
            policy_no = _next_monarch_policy_no(m_class)
        else:
            policy_no = f"POL-{date.today().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"
            log.warning("Monarch product '%s' has no assigned policy-number series "
                        "(private/commercial only) — falling back to internal format for %s",
                        q.get('product'), policy_no)
    else:
        policy_no = f"POL-{date.today().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"

    _query("""
        INSERT INTO policies
            (policy_no, quote_id, agent_id, client_id,
             vehicle_reg, type_of_cover, commencing_date, expiry_date,
             total_payable, business_type, parent_policy_no,
             original_commencing_date, installment_plan,
             installment_number, installment_total, status, dmvic_status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending_payment','awaiting_payment')
    """, (
        policy_no, quote_id, q['agent_id'], client_id,
        q.get('vehicle_reg', ''), q['type_of_cover'],
        q['commencing_date'], q['expiry_date'],
        q['total_payable'],
        q.get('business_type') or 'new',
        q.get('parent_policy_no'),
        q.get('original_commencing_date') or q.get('commencing_date'),
        q.get('installment_plan'),
        q.get('installment_number') or 1,
        q.get('installment_total') or 1,
    ), commit=True)

    _query("UPDATE quotations SET status='converted' WHERE id=%s",
           (quote_id,), commit=True)

    _query("""
        INSERT INTO payments (policy_no, amount, status, method)
        VALUES (%s, %s, 'pending', 'manual')
    """, (policy_no, q['total_payable']), commit=True)

    # TEMP(payment-bypass): issue the DMVIC certificate immediately instead of
    # waiting for payment to settle. Revert this block (and the matching TEMP
    # blocks in activate_paid_policy_and_enqueue_dmvic, mongo_store
    # .activate_policy_after_payment and _issue_dmvic_certificate_impl) once
    # the ER002 issue is resolved.
    _enqueue("dmvic_issue_certificate", _issue_dmvic_certificate, policy_no, q)

    _cache_delete_prefix("cache:dashboard")
    _cache_delete_prefix("cache:reports_summary")

    return jsonify({
        "success":       True,
        "policy_no":     policy_no,
        "total_payable": float(q['total_payable']),
        "message":       "Policy created. Payment pending confirmation."
    })


@payments_bp.route('/mpesa/stk', methods=['POST'])
@login_required
@approved_required
@rate_limited(6, window=60)
def mpesa_stk():
    d         = request.get_json() or {}
    policy_no = d.get('policy_no', '').strip()
    phone     = d.get('phone', '').strip()
    amount    = d.get('amount', 0)

    if not policy_no or not phone or not amount:
        return jsonify({"error": "policy_no, phone and amount are required"}), 400

    policy = _query("SELECT * FROM policies WHERE policy_no=%s", (policy_no,), fetchone=True)
    if not policy:
        return jsonify({"error": "Policy not found"}), 404
    if session['role'] != 'admin' and policy['agent_id'] != session['user_id']:
        return jsonify({"error": "You do not have access to this policy"}), 403

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid amount"}), 400

    if amount <= 0:
        return jsonify({"error": "Amount must be greater than zero"}), 400

    quoted_amount = float(policy['total_payable'])
    paid_row = _query("""
        SELECT COALESCE(SUM(amount),0) AS paid FROM payments
        WHERE policy_no=%s AND status='completed'
    """, (policy_no,), fetchone=True) or {}
    already_paid = float(paid_row.get('paid', 0) or 0)
    balance = round(quoted_amount - already_paid, 2)

    if balance <= 0:
        return jsonify({"error": "This policy has already been paid in full."}), 400

    if amount < balance:
        agent = {
            'name':  session.get('name', session.get('username')),
            'email': session.get('email', ''),
        }
        shortfall = balance - amount
        flag_reason = (f"Paid KES {amount:,.0f} against policy {policy_no}, "
                       f"KES {shortfall:,.0f} below the outstanding balance of KES {balance:,.0f} "
                       f"(on {time.strftime('%Y-%m-%d %H:%M')}).")

        try:
            _query("""UPDATE users
                     SET flagged=1, flagged_reason=%s, flagged_at=NOW(),
                         underpayment_attempts = underpayment_attempts + 1
                     WHERE id=%s""",
                   (flag_reason, session['user_id']), commit=True)
        except Exception as e:
            log.error("Could not flag account for underpayment (has "
                      "migrations_add_underpayment_flag.sql been run?): %s", type(e).__name__)

        _enqueue("underpayment_alert", _notify_underpayment_attempt,
                 agent, policy_no, balance, amount, phone)

        _query("INSERT INTO audit_log (user_id, action, detail) VALUES (%s,%s,%s)",
               (session['user_id'], 'underpayment_warning',
                f"policy={policy_no} balance={balance} attempted={amount}"), commit=True)

    result = mpesa_stk_push(phone=phone, amount=amount,
                             account_ref=policy_no, description='Insurance Premium')
    if not result['success']:
        return jsonify({"error": result.get('error', 'STK push failed')}), 400

    # Record this STK push. A previously *completed* M-Pesa payment (e.g. a
    # partial installment that already settled) must be preserved so completed
    # amounts accumulate toward the balance; only an in-flight *pending* row
    # is replaced (the previous prompt was abandoned). Otherwise insert a new
    # row. This keeps the one-pending-row-per-policy invariant for
    # reconciliation without losing completed partial payments.
    pending = _query("""SELECT id FROM payments
                       WHERE policy_no=%s AND method='mpesa' AND status='pending'""",
                     (policy_no,), fetchone=True)
    if pending:
        _query("""UPDATE payments SET amount=%s, reference=%s, paid_at=NULL
                 WHERE id=%s""",
               (amount, result['checkout_request_id'], pending['id']), commit=True)
    else:
        _query("INSERT INTO payments (policy_no, amount, status, method, reference) VALUES (%s,%s,'pending','mpesa',%s)",
               (policy_no, amount, result['checkout_request_id']), commit=True)

    return jsonify({
        "success":             True,
        "checkout_request_id": result['checkout_request_id'],
        "customer_message":    result.get('customer_message', 'Check your phone for the M-Pesa prompt.'),
    })


@payments_bp.route('/mpesa/query', methods=['POST'])
@login_required
@approved_required
def mpesa_query():
    d                   = request.get_json() or {}
    checkout_request_id = d.get('checkout_request_id', '').strip()
    policy_no           = d.get('policy_no', '').strip()
    if not checkout_request_id:
        return jsonify({"error": "checkout_request_id required"}), 400

    if policy_no:
        policy = _query("SELECT agent_id FROM policies WHERE policy_no=%s", (policy_no,), fetchone=True)
        if not policy:
            return jsonify({"error": "Policy not found"}), 404
        if session['role'] != 'admin' and policy['agent_id'] != session['user_id']:
            return jsonify({"error": "You do not have access to this policy"}), 403
        payment = _query("SELECT policy_no FROM payments WHERE reference=%s", (checkout_request_id,), fetchone=True)
        if not payment or payment.get('policy_no') != policy_no:
            return jsonify({"error": "Checkout request does not belong to this policy"}), 404
    elif session['role'] != 'admin':
        return jsonify({"error": "policy_no required"}), 400

    result = mpesa_query_status(checkout_request_id)
    if result.get('success') and policy_no:
        receipt = result.get('mpesa_receipt_number') or checkout_request_id
        _settle_mpesa_payment(
            checkout_request_id, policy_no,
            source='mpesa_payment_confirmed',
            reference=receipt,
            user_id=session.get('user_id'),
        )
    return jsonify(result)


@payments_bp.route('/mpesa/callback/<secret>', methods=['POST'])
def mpesa_callback(secret):
    if not hmac_compare(secret, MPESA_CALLBACK_SECRET):
        log.warning("ArchPay callback rejected: bad secret path from %s", _request_ip())
        abort(404)

    try:
        data = request.get_json(force=True) or {}
        callback_data = data.get('data') if isinstance(data.get('data'), dict) else data
        ref = (callback_data.get('checkoutRequestId')
               or callback_data.get('checkout_request_id')
               or callback_data.get('CheckoutRequestID')
               or '').strip()
        if not ref:
            return jsonify({"received": False, "error": "Missing checkoutRequestId"}), 400

        pmt = _query("SELECT policy_no FROM payments WHERE reference=%s", (ref,), fetchone=True)
        if not pmt:
            log.warning("ArchPay callback ignored: unknown checkoutRequestId=%s", ref)
            return jsonify({"received": True})

        # SECURITY: do NOT trust the callback payload's status/receipt. The
        # only thing the path secret proves is that the caller knew the URL.
        # Re-confirm with ArchPay's own authenticated /verify endpoint (gated
        # by our private API key) before marking anything paid, so a forged
        # callback can never activate a policy on its own. If the verify API
        # has not yet reflected the settlement, leave the payment pending;
        # the client's /mpesa/query polling (which also re-verifies) will
        # complete the settlement once ArchPay confirms.
        verify = mpesa_query_status(ref)
        status = (verify.get('status') or '').strip().lower()

        if verify.get('success'):
            receipt = verify.get('mpesa_receipt_number') or ref
            _settle_mpesa_payment(
                ref, pmt['policy_no'],
                source='archpay_callback_confirmed',
                reference=receipt,
            )
        elif status in ('failed', 'cancelled', 'canceled', 'timeout', 'reversed'):
            _query("UPDATE payments SET status=%s WHERE reference=%s AND status='pending'",
                   (status, ref), commit=True)
            _query("INSERT INTO audit_log (action, detail) VALUES (%s,%s)",
                   ('archpay_callback_not_completed',
                    f"policy={pmt['policy_no']} ref={ref} status={status}"), commit=True)
            # Bell notification: admin (emailed) + owning agent (in-app only).
            if _push_notification:
                owner = _query("SELECT agent_id FROM policies WHERE policy_no=%s",
                               (pmt['policy_no'],), fetchone=True)
                agent_id = owner.get('agent_id') if owner else None
                human = {'cancelled': 'cancelled', 'canceled': 'cancelled'}.get(status, status)
                _push_notification(
                    'mpesa_failed', 'M-Pesa payment failed',
                    f"Payment for policy {pmt['policy_no']} {human}. Ref {ref}.",
                    link=f"/renewals?policy={pmt['policy_no']}",
                    user_id=None, email_admin=True)
                if agent_id:
                    _push_notification(
                        'mpesa_failed', 'M-Pesa payment failed',
                        f"Your M-Pesa payment for policy {pmt['policy_no']} {human}.",
                        link=f"/renewals?policy={pmt['policy_no']}",
                        user_id=agent_id)
        else:
            log.info("ArchPay callback non-final status=%s ref=%s (left pending)", status, ref)
    except Exception as e:
        log.error("ArchPay callback processing error: %s", type(e).__name__)
    return jsonify({"received": True})


@payments_bp.route('/mpesa/status')
@login_required
def mpesa_config_status():
    configured = bool(ARCHPAY_API_KEY)
    return jsonify({
        "configured": configured,
        "provider": "archpay",
        "env": ARCHPAY_MODE,
        "channel_id": ARCHPAY_CHANNEL_ID or None,
    })