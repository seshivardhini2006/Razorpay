"""Synthetic failure event generator for demo purposes.

Produces realistic failure distributions based on a publicly-known payment
failure taxonomy. All data is synthetic - no real customer PII.
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

# (reason_code, bank_failure_rate_weight) — banks aren't all equally reliable
# Error codes with their underlying failure reason
ERROR_POOL = {
    "insufficient_funds": ["INSUFFICIENT_FUNDS", "INSUFFICIENT_BALANCE", "LOW_BALANCE"],
    "bank_server_downtime": ["BANK_TIMEOUT", "GATEWAY_ERROR", "HOST_TIME_OUT", "BANK_UNAVAILABLE"],
    "otp_timeout": ["OTP_EXPIRED", "OTP_TIMEOUT"],
    "expired_card": ["CARD_EXPIRED", "EXPIRED_CARD"],
    "wrong_cvv_pin": ["INVALID_CVV", "INVALID_PIN", "WRONG_CVV"],
    "network_drop": ["CONNECTION_ERROR", "NETWORK_ERROR", "CONNECTION_TIMEOUT"],
    "risk_fraud_block": ["RISK_BLOCKED", "TECHNICAL_DECLINE"],
}

# Most common real-world recoverable distribution (weights sum ~1)
REASON_WEIGHTS = {
    "insufficient_funds": 0.25,
    "bank_server_downtime": 0.20,
    "otp_timeout": 0.18,
    "wrong_cvv_pin": 0.14,
    "network_drop": 0.12,
    "expired_card": 0.07,
    "risk_fraud_block": 0.04,
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
    if reason == "bank_server_downtime" and "HDFC" in bank:
        bank = random.choice(["SBI", "Punjab National"])

    customer_id = f"cust_{1000 + random.randint(1, 999)}"
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    # amounts in rupees -> paise (e.g. 14999 = Rs 149.99). Realistic retail/subscription ranges.
    amount = random.choice([299, 499, 799, 999, 1499, 1999, 2499, 2999, 4999, 7999, 12999]) * random.choice([1, 1, 2, 5])
    is_subscription = random.random() < 0.25

    error_code = random.choice(ERROR_POOL[reason])

    return PaymentEvent(
        transaction_id=f"txn_{now:%Y%m%d}_{txn_counter:05d}",
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        customer_id=customer_id,
        customer_name=name,
        customer_email=f"{name.lower().replace(' ', '.')}@example.com",
        amount=amount,
        currency="INR",
        payment_method=method,
        bank=bank,
        error_code=error_code,
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
