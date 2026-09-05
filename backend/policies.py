"""
Policy definitions for the Reclaim retry engine.

Central place to tweak retry behavior. Every decision cites the reason
it was chosen so the system stays explainable.
"""

# reason -> policy
POLICIES = {
    "insufficient_funds": {
        "should_retry": True,
        "timing": "scheduled",
        "channel": "same_method",
        "max_attempts": 3,
        "cooldown_minutes": 1440,  # ~24h, near likely salary/credit date
        "reasoning": (
            "Insufficient funds is typically transient (e.g. pre-payroll). "
            "A delayed retry near a likely funding date has a high success probability."
        ),
    },
    "bank_server_downtime": {
        "should_retry": True,
        "timing": "short_delay",
        "channel": "same_method",
        "max_attempts": 3,
        "cooldown_minutes": 15,
        "reasoning": (
            "Bank downtime is typically transient. A short-delay retry (10-15 min) "
            "has a high success probability after the bank recovers."
        ),
    },
    "otp_timeout": {
        "should_retry": True,
        "timing": "immediate",
        "channel": "same_method",
        "max_attempts": 2,
        "cooldown_minutes": 5,
        "reasoning": (
            "OTP timeout means the customer simply didn't complete the flow. "
            "An immediate prompted retry usually succeeds once the customer is engaged."
        ),
    },
    "expired_card": {
        "should_retry": False,
        "timing": "no_retry",
        "channel": "alternate_method",
        "max_attempts": 1,
        "cooldown_minutes": 0,
        "reasoning": (
            "An expired card cannot be recovered by a blind retry. "
            "The customer must supply a new card, so we prompt for a new method, not retry."
        ),
    },
    "wrong_cvv_pin": {
        "should_retry": True,
        "timing": "immediate",
        "channel": "same_method",
        "max_attempts": 2,
        "cooldown_minutes": 5,
        "reasoning": (
            "Wrong CVV/PIN is a user input error. An immediate retry with a correction "
            "prompt usually succeeds once the customer re-enters correct details."
        ),
    },
    "network_drop": {
        "should_retry": True,
        "timing": "immediate",
        "channel": "same_method",
        "max_attempts": 2,
        "cooldown_minutes": 5,
        "reasoning": (
            "A network drop is a connectivity issue mid-transaction. "
            "An immediate retry over a stable connection typically succeeds."
        ),
    },
    "risk_fraud_block": {
        "should_retry": False,
        "timing": "no_retry",
        "channel": "none",
        "max_attempts": 0,
        "cooldown_minutes": 0,
        "reasoning": (
            "Risk/fraud blocks are outside the recovery scope. "
            "Automatic retries could look like fraud; escalate to manual review instead."
        ),
    },
    "unknown": {
        "should_retry": False,
        "timing": "no_retry",
        "channel": "none",
        "max_attempts": 0,
        "cooldown_minutes": 0,
        "reasoning": (
            "Unknown failure reason with low confidence. Conservative: no automatic retry "
            "until the reason can be confirmed."
        ),
    },
}

# Hard compliance caps (industry-aggressive but safe defaults for demo)
GLOBAL_MAX_ATTEMPTS_PER_TRANSACTION = 4
MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS = 5
MISSING_CARD_THRESHOLD_DAYS = 1
