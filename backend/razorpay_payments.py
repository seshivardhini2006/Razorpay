"""Real Razorpay test-mode Payment Links API.

Events stay synthetic; the EXECUTION step can hit Razorpay's real API.

Which flows use it: the customer-completion flows ("prompt for a new card",
one-tap retry, add-funds-and-pay). When a Payment Link is created we put its
short URL into the recovery message so the customer has a real, payable link.

Credentials (test keys) come from the environment:
    RAZORPAY_TEST_KEY_ID / RAZORPAY_TEST_KEY_SECRET

Behaviour:
- No credentials configured  -> offline mock link (source="offline") so the
  pipeline stays fully observable in demos and tests.
- Credentials configured     -> real POST to /v1/payment_links with Basic auth.
- Credentials configured but the call fails -> returns None; the engine logs the
  failure and falls back to the plain template message (no fake link).

References: https://razorpay.com/docs/payment-links/
"""

import base64
import os
import sys
import uuid

import httpx

API_BASE = "https://api.razorpay.com/v1/payment_links"
TIMEOUT_SECONDS = 15


def _credentials():
    key_id = os.environ.get("RAZORPAY_TEST_KEY_ID", "").strip()
    key_secret = os.environ.get("RAZORPAY_TEST_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        return None
    return key_id, key_secret


def _mock_link(amount: int, description: str) -> dict:
    """Offline stand-in so the pipeline is observable without test keys."""
    uid = uuid.uuid4().hex[:10]
    return {
        "id": f"link_mock_{uid}",
        "short_url": f"https://rzp.io/i/{uid}",
        "status": "created",
        "source": "offline",
    }


def create_payment_link(
    *,
    amount: int,
    description: str,
    customer: dict,
    notes: dict,
) -> dict | None:
    """Create a Razorpay Payment Link (test mode). Returns dict or None on failure."""
    creds = _credentials()
    if creds is None:
        return _mock_link(amount, description)

    token = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
    body = {
        "amount": amount,  # smallest currency unit (paise)
        "currency": "INR",
        "accept_partial": False,
        "description": (description or "")[:90],
        "customer": {
            "name": customer.get("name", "")[:90],
            "email": customer.get("email", "")[:90],
            "contact": customer.get("contact", "")[:15],
        },
        "notify": {"sms": True, "email": False},
        "reminder_enable": False,
        "notes": notes or {},
        "callback_method": "get",
    }
    try:
        resp = httpx.post(
            API_BASE,
            json=body,
            headers={"Authorization": f"Basic {token}"},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "id": data.get("id", ""),
            "short_url": data.get("short_url", ""),
            "status": data.get("status", "created"),
            "source": "razorpay",
        }
    except Exception as exc:
        print(
            f"[Revive] Razorpay Payment Link creation failed ({exc}); "
            "falling back to template message without a link.",
            file=sys.stderr,
        )
        return None


def create_link_for_event(event) -> dict | None:
    """Convenience: build the Payment Link payload from a failure event."""
    contact = getattr(event, "phone", None) or ""
    return create_payment_link(
        amount=event.amount,
        description=f"Complete your payment to {event.merchant_name or event.merchant_id}",
        customer={
            "name": event.customer_name or "Customer",
            "email": event.customer_email or "",
            "contact": contact,
        },
        notes={
            "revive_transaction_id": event.transaction_id,
            "revive_merchant_id": event.merchant_id,
            "revive_iso": "1",
        },
    )