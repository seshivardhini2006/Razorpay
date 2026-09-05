"""Retry policy table for the Revive engine.

Central, explainable, configurable. Every category maps to a distinct recovery
strategy. Hard global bounds live in bound.py and are enforced at execution time
regardless of these defaults.
"""

POLICY_BY_CATEGORY = {
    "insufficient_funds": {
        "should_retry": True,
        "timing": "scheduled",
        "channel": "same_method",
        "max_attempts": 3,
        "cooldown_minutes": 1440,  # next day — near likely funding/salary date
        "reasoning": (
            "Insufficient funds is typically transient (e.g. pre-payroll/funding date). "
            "A scheduled next-day retry near a likely funding moment has high success probability."
        ),
    },
    "bank_server_downtime": {
        "should_retry": True,
        "timing": "short_delay",
        "channel": "same_method",
        "max_attempts": 3,
        "cooldown_minutes": 15,
        "reasoning": (
            "Bank downtime is typically transient; a short-delay retry (10-15 min) succeeds "
            "once the issuer recovers."
        ),
    },
    "otp_timeout": {
        "should_retry": True,
        "timing": "immediate",
        "channel": "same_method",
        "max_attempts": 2,
        "cooldown_minutes": 5,
        "reasoning": (
            "OTP timeout means the customer didn't complete the flow. An immediate, prompted "
            "retry with a fresh OTP usually succeeds."
        ),
    },
    "wrong_cvv_pin": {
        "should_retry": True,
        "timing": "immediate",
        "channel": "same_method",
        "max_attempts": 2,
        "cooldown_minutes": 5,
        "reasoning": (
            "Wrong CVV/PIN is a customer input error; an immediate corrected retry usually succeeds."
        ),
    },
    "expired_card": {
        "should_retry": False,
        "timing": "no_retry",
        "channel": "alternate_method",
        "max_attempts": 1,
        "cooldown_minutes": 0,
        "reasoning": (
            "An expired card can't be recovered by a blind retry — the customer must supply a "
            "new card. Prompt for a new method instead."
        ),
    },
    "network_drop": {
        "should_retry": True,
        "timing": "immediate",
        "channel": "same_method",
        "max_attempts": 2,
        "cooldown_minutes": 5,
        "reasoning": (
            "A network drop is a connectivity issue mid-transaction; an immediate retry over a "
            "stable connection usually succeeds."
        ),
    },
    "risk_fraud_block": {
        "should_retry": False,
        "timing": "no_retry",
        "channel": "none",
        "max_attempts": 0,
        "cooldown_minutes": 0,
        "reasoning": (
            "Risk/fraud blocks are outside recovery scope; auto-retries can look like fraud. "
            "Escalate to human review."
        ),
    },
    "ambiguous": {
        "should_retry": False,
        "timing": "no_retry",
        "channel": "none",
        "max_attempts": 0,
        "cooldown_minutes": 0,
        "reasoning": (
            "Ambiguous failure with no reliable reason. Auto-retry refused; human review decides "
            "whether any further attempt is justified."
        ),
    },
}