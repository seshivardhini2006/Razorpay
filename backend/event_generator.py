"""Synthetic failure event generator for demo purposes.

Produces realistic failure distributions based on a documented-style failure
taxonomy (see backend/data/razorpay_error_reasons.json). A small share of events
use generic catch-all codes so the triage + human-review path gets exercised too.

All data is synthetic — no real customer PII.
"""

import random
from datetime import datetime, timedelta

from models import PaymentEvent

BANKS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "Punjab National", "Yes Bank", "IndusInd"]
METHODS = ["UPI", "CARD", "UPI", "CARD", "UPI", "NETBANKING"]  # weighted toward UPI/Card
MERCHANTS = [
    ("merch_001", "UrbanKart"),
    ("merch_002", "CloudNest SaaS"),
    ("merch_003", "FitLife Gym"),
    ("merch_004", "GreenGrocer"),
    ("merch_005", "Streamly Media"),
]

# error code + human-readable Razorpay-style failure reason — ALL codes are
# drawn from Razorpay's official documented failure codes
# (docs/errors/payments/list and docs/errors/payments/cards).
ERROR_POOL = {
    "insufficient_funds": [
        ("insufficient_funds", "Insufficient funds in the account"),
        ("transaction_limit_exceeded", "Credit/debit limit exceeded on the card"),
        ("credit_limit_exceeded", "Credit limit for the customer has been exceeded"),
    ],
    "bank_server_downtime": [
        ("bank_technical_error", "Technical error at the issuing bank"),
        ("bank_not_available", "Bank is not available due to a downtime or a technical issue"),
        ("gateway_technical_error", "Downtime on a partner bank"),
        ("server_error", "Technical error at Razorpay's server"),
    ],
    "otp_timeout": [
        ("otp_expired", "OTP expired before verification"),
        ("otp_attempts_exceeded", "OTP attempts have been exceeded"),
    ],
    "wrong_cvv_pin": [
        ("incorrect_cvv", "Incorrect CVV entered"),
        ("incorrect_pin", "Incorrect PIN entered"),
        ("incorrect_otp", "Incorrect OTP entered"),
    ],
    "expired_card": [
        ("card_expired", "Card has expired"),
    ],
    "network_drop": [
        ("request_timed_out", "The request has timed out"),
        ("payment_timed_out", "Payment exceeded the processing time limit"),
    ],
    "risk_fraud_block": [
        ("payment_risk_check_failed", "Transaction declined due to fraud risk checks"),
        ("compliance_violation", "The payment violates compliance requirements"),
        ("debit_instrument_blocked", "The card is blocked by the issuer or customer"),
    ],
    "ambiguous": [
        ("card_declined", "Card declined by the bank"),
        ("payment_failed", "Payment processing failed at the bank or gateway"),
        ("authentication_failed", "Authentication (3DS/OTP) failed"),
    ],
}

# Most common real-world recoverable distribution (weights sum ~1)
REASON_WEIGHTS = {
    "insufficient_funds": 0.22,
    "bank_server_downtime": 0.18,
    "otp_timeout": 0.16,
    "wrong_cvv_pin": 0.13,
    "network_drop": 0.12,
    "expired_card": 0.06,
    "risk_fraud_block": 0.05,
    "ambiguous": 0.08,
}

FIRST_NAMES = ["Aarav", "Diya", "Rohan", "Ananya", "Karthik", "Meera", "Vihaan", "Saanvi",
               "Ishan", "Priya", "Arjun", "Navya", "Kabir", "Ishita", "Dev", "Zara"]
LAST_NAMES = ["Sharma", "Patel", "Iyer", "Reddy", "Gupta", "Nair", "Khan", "Verma",
              "Mehta", "Singh", "Menon", "Das", "Bose", "Chopra"]


def _weighted_reason():
    r = random.random()
    acc = 0
    for reason, w in REASON_WEIGHTS.items():
        acc += w
        if r <= acc:
            return reason
    return "insufficient_funds"


def generate_event(txn_counter, now=None) -> PaymentEvent:
    """Generate a single synthetic failure event."""
    if now is None:
        now = datetime.utcnow()

    reason = _weighted_reason()

    method = random.choice(METHODS)
    if reason == "expired_card":
        method = "CARD"

    merchant_id, merchant_name = random.choice(MERCHANTS)
    bank = random.choice(BANKS)

    # Bank downtime concentrated on a couple of "unreliable" banks for the story
    if reason == "bank_server_downtime" and bank in ("HDFC",):
        bank = random.choice(["SBI", "Punjab National"])

    customer_id = f"cust_{1000 + random.randint(1, 999)}"
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    digit_hash = str(abs(hash(name)) % 100_000_000).zfill(8)
    phone = "91" + digit_hash
    # amounts in rupees -> paise (e.g. 14999 = Rs 149.99). Realistic retail/subscription ranges.
    amount = random.choice([299, 499, 799, 999, 1499, 1999, 2499, 2999, 4999, 7999, 12999]) * random.choice([1, 1, 2, 5])
    is_subscription = random.random() < 0.25

    error_code, reason_text = random.choice(ERROR_POOL[reason])

    return PaymentEvent(
        transaction_id=f"txn_{now:%Y%m%d}_{txn_counter:05d}",
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        customer_id=customer_id,
        customer_name=name,
        customer_email=f"{name.lower().replace(' ', '.')}@example.com",
        phone=phone,
        amount=amount,
        currency="INR",
        payment_method=method,
        bank=bank,
        error_code=error_code,
        reason=reason_text,
        timestamp=now.isoformat() + "Z",
        retry_count=0,
        is_subscription=is_subscription,
    )


def generate_batch(count, now=None) -> list:
    """Generate a batch of synthetic events with sequential timestamps."""
    if now is None:
        now = datetime.utcnow()
    events = []
    base_txn = random.randint(100000, 999999)
    for i in range(count):
        ts = now - timedelta(seconds=random.randint(0, 60))
        events.append(generate_event(base_txn + i, now=ts))
    return events