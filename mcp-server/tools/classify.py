"""Ticket classification: Ollama (default model: tinyllama) + deterministic business rules."""
import json
import logging
import re
import sys

from .errors import TriageError
from .providers import chat_completion

logger = logging.getLogger("triage.classify")

VALID_CATEGORIES = {
    "account_access",
    "billing",
    "technical_issue",
    "feature_request",
    "bug_report",
    "general_inquiry",
}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
FALLBACK = {"category": "general_inquiry", "priority": "normal"}

SYSTEM_PROMPT = """You are a support ticket classifier.
Respond with ONLY a single JSON object. No explanation, no markdown, no extra text.

Format:
{"category": "<category>", "priority": "<priority>", "reasoning": "<one short sentence>"}

Valid categories: account_access, billing, technical_issue, feature_request, bug_report, general_inquiry
Valid priorities: low, normal, high, urgent

Priority rules:
- urgent: security issue, data loss, complete service outage, payment failure blocking business
- high: user blocked from a core action, cannot log in, cannot reset password
- normal: issue impacts user but a workaround exists, or a normal question
- low: cosmetic issue, minor request, general feedback"""


# --------------------------------------------------------------------------- LLM output parsing
def _extract_json_object(text: str):
    """Return the first JSON object found in text (handles prose and ```json fences), else None."""
    if not text:
        return None
    text = text.strip()
    # Whole text is JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    # Try to decode an object starting at each '{' (works with prose, fences, nested braces)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _norm(value) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_") if value is not None else ""


def parse_model_output(text: str) -> dict:
    """Turn raw model output into a safe {category, priority, reasoning} dict. Never raises."""
    obj = _extract_json_object(text)
    if obj is None:
        logger.warning("Model output was not parseable JSON; using fallback")
        return {**FALLBACK, "reasoning": "Could not parse classifier output; used default classification."}

    category = _norm(obj.get("category"))
    priority = _norm(obj.get("priority"))
    reasoning = obj.get("reasoning")
    valid = True

    if category not in VALID_CATEGORIES:
        category, valid = FALLBACK["category"], False
    if priority not in VALID_PRIORITIES:
        priority, valid = FALLBACK["priority"], False

    if not valid:
        logger.warning("Model returned invalid/missing category or priority; fallback applied")
    if not isinstance(reasoning, str) or not reasoning.strip():
        reasoning = "" if valid else "Classifier output was incomplete; defaults applied where needed."
    return {"category": category, "priority": priority, "reasoning": reasoning.strip()[:500]}


# --------------------------------------------------------------------------- business rules
def _rx(*patterns):
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# A negation word within ~3 words before a match cancels it ("no data loss", "not a complete outage").
_NEGATION = re.compile(
    r"\b(?:no|not|never|without|neither|nor|isn'?t|aren'?t|wasn'?t|weren'?t|didn'?t|hasn'?t|haven'?t)\b"
    r"(?:\W+\w+){0,3}\W*$",
    re.IGNORECASE,
)

# ---- urgent
PAYMENT_FAILURE = _rx(
    r"\bpayments?\s+(?:processing\s+)?(?:fail(?:ed|ing|ure|ures|s)?|is\s+failing|are\s+failing|is\s+down|are\s+down)\b",
    r"\bpayment\s+failures?\b",
    r"\b(?:cannot|can'?t|unable\s+to)\s+(?:process|accept|take)\s+(?:any\s+)?payments?\b",
)
PAYMENT_BLOCKS_BUSINESS = _rx(
    r"\bblock(?:ing|s|ed)?\b",
    r"\b(?:halt(?:ed|s|ing)?|stopp(?:ed|ing))\b",
    r"\bcheckout\b",
    r"\brevenue\b",
    r"\b(?:cannot|can'?t|unable\s+to)\s+(?:sell|operate|do\s+business|process\s+orders)\b",
    r"\ball\s+(?:of\s+our\s+|our\s+)?(?:customers|orders|transactions|payments)\b",
)
SECURITY_INCIDENT = _rx(
    r"\bsecurity\s+(?:incident|breach|issue|vulnerability|compromise)\b",
    r"\bdata\s+breach\b",
    r"\b(?:hacked|breached|ransomware|malware)\b",
    r"\bunauthori[sz]ed\s+(?:access|transactions?|charges?|login)\b",
    r"\b(?:credentials|passwords?|api\s+keys?)\s+(?:were\s+|was\s+|got\s+)?(?:leaked|exposed|stolen)\b",
    r"\baccount\s+(?:was\s+|has\s+been\s+)?compromised\b",
)
DATA_LOSS = _rx(
    r"\bdata\s+(?:loss|corruption)\b",
    r"\bdata\s+(?:is|was|has\s+been|got)\s+(?:lost|gone|missing|deleted|wiped|corrupted)\b",
    r"\b(?:lost|deleted|wiped)\s+(?:all\s+)?(?:of\s+)?(?:our\s+|my\s+|the\s+)?(?:customer\s+|production\s+)?(?:data|records|database)\b",
)
COMPLETE_OUTAGE = _rx(
    r"\b(?:complete|total|full|major)\s+(?:service\s+|system\s+|production\s+|platform\s+)?outage\b",
    r"\bproduction\s+(?:service\s+|system\s+|environment\s+|site\s+|platform\s+)?(?:outage|is\s+down|are\s+down|down)\b",
    r"\b(?:entire|whole|complete)\s+(?:production\s+)?(?:service|system|platform|site|website|application|app)\s+(?:is\s+|are\s+)?(?:down|offline|unavailable|unreachable)\b",
    r"\b(?:service|system|platform|site|website|application|app)\s+(?:is\s+|are\s+)?(?:completely|totally|entirely)\s+(?:down|offline|unavailable|unreachable)\b",
    r"\ball\s+(?:of\s+)?(?:our\s+|the\s+)?(?:customers|users|clients)\s+(?:are\s+|is\s+|were\s+)?(?:affected|impacted)\b",
)

# ---- account access (must describe being BLOCKED, not just mention a password)
ACCOUNT_BLOCKED = _rx(
    r"\blocked\s+out\b",
    r"\baccount\s+(?:is\s+|has\s+been\s+|was\s+|got\s+)?(?:locked|suspended|disabled|blocked)\b",
    r"\b(?:can'?t|cannot|unable\s+to|not\s+able\s+to|couldn'?t|could\s+not|failed\s+to|won'?t\s+let\s+me|doesn'?t\s+let\s+me)"
    r"\s+(?:\w+\s+){0,3}?(?:log\s?in(?:to)?|sign\s?in(?:to)?|reset\s+(?:my\s+|the\s+|our\s+)?password|access\s+(?:my\s+|the\s+|our\s+)?account)\b",
    r"\b(?:log\s?in|sign\s?in|password\s+reset|reset\s+link|reset\s+email)\s+(?:is\s+|are\s+|keeps\s+)?"
    r"(?:not\s+working|broken|failing|fails|doesn'?t\s+work|does\s+not\s+work)\b",
)

# ---- lower-priority categories
COSMETIC = _rx(
    r"\bcosmetic\b",
    r"\b(?:colou?rs?|fonts?|typos?|typography|spacing|padding|alignment|icons?|logo|wording|styling)\b",
    r"\blooks?\s+(?:slightly\s+|a\s+bit\s+|a\s+little\s+|somewhat\s+)?(?:outdated|old|ugly|dated|off|odd|cluttered)\b",
    r"\bminor\s+(?:request|suggestion|tweak|ui|visual|issue)\b",
    r"\b(?:general\s+)?feedback\b",
    r"\bnitpick\b",
)
BLOCKING_TERMS = _rx(
    r"\b(?:can'?t|cannot|unable\s+to|not\s+able\s+to|won'?t|doesn'?t\s+work|does\s+not\s+work|not\s+working|isn'?t\s+working|broken|fails?|failed|failing|errors?)\b",
)
BILLING = _rx(
    r"\binvoices?\b",
    r"\bbilling\b",
    r"\brefunds?\b",
    r"\b(?:overcharged|double[- ]charged|charged\s+twice)\b",
    r"\bpayments?\b",
    r"\bsubscription\b",
    r"\bcredit\s+card\b",
    r"\breceipts?\b",
)
FEATURE_REQUEST = _rx(
    r"\bfeature\s+requests?\b",
    r"\bplease\s+add\b",
    r"\bcan\s+you\s+add\b",
    r"\badd\s+(?:support\s+for|an?\s+option|the\s+ability)\b",
    r"\bwould\s+(?:like|love)\s+(?:to\s+see|it\s+if|to\s+be\s+able)\b",
    r"\bit\s+would\s+be\s+(?:nice|great|helpful|useful|good)\b",
    r"\bwish\s+(?:you|it|there)\b",
    r"\b(?:new|additional)\s+feature\b",
    r"\benhancement\b",
)
BUG = _rx(
    r"\bbugs?\b",
    r"\bdefects?\b",
    r"\bcrash(?:es|ed|ing)?\b",
    r"\bregression\b",
    r"\bglitch(?:es)?\b",
    r"\bstack\s?trace\b",
    r"\bexceptions?\b",
    r"\bnot\s+working\s+as\s+(?:expected|intended)\b",
)
TECH_OUTAGE = _rx(
    r"\boutage\b",
    r"\b(?:system|service|server|site|website|platform|app|application|api)\s+(?:is\s+|are\s+)?(?:currently\s+)?(?:down|unavailable|unreachable|offline|not\s+responding)\b",
    r"\bunavailable\b",
    r"\btime[sd]?\s?out\b|\btimeouts?\b",
)
GENERAL_INFO = _rx(
    r"\bbusiness\s+hours?\b",
    r"\bworking\s+hours?\b",
    r"\bwhen\s+are\s+you\s+open\b",
    r"\bwhat\s+time\s+does\s+support\s+open\b",
)

# Anything suggesting a real problem: the cosmetic/feedback rule must NOT fire when these are present.
DEFECT_SIGNALS = BLOCKING_TERMS + BILLING + BUG + TECH_OUTAGE

# (name, category, priority or None = keep the model's validated priority, any_of, also, unless)
# ORDER = PRECEDENCE. First matching rule wins.
RULES = [
    # 1-2. critical rules override the LLM
    ("payment_failure_blocking_business", "billing", "urgent", PAYMENT_FAILURE, PAYMENT_BLOCKS_BUSINESS, []),
    ("security_incident", "technical_issue", "urgent", SECURITY_INCIDENT, [], []),
    ("data_loss", "technical_issue", "urgent", DATA_LOSS, [], []),
    ("complete_outage", "technical_issue", "urgent", COMPLETE_OUTAGE, [], []),
    # 3. core account access blockage
    ("account_access_blocked", "account_access", "high", ACCOUNT_BLOCKED, [], []),
    # 4. clear general-information inquiries
    ("general_information", "general_inquiry", "normal", GENERAL_INFO, [], []),
    # 5. cosmetic / minor / general feedback (an explicit "feature request" is not cosmetic)
    ("cosmetic_or_feedback", "general_inquiry", "low", COSMETIC, [], DEFECT_SIGNALS + _rx(r"\bfeature\s+requests?\b")),
    # 6-9. category rules (priority stays whatever the model said)
    ("billing", "billing", None, BILLING, [], []),
    ("feature_request", "feature_request", None, FEATURE_REQUEST, [], []),
    ("bug_report", "bug_report", None, BUG, [], []),
    ("technical_outage", "technical_issue", None, TECH_OUTAGE, [], []),
]


def _hit(patterns, text: str) -> bool:
    """True if any pattern matches and is not negated ('no data loss', 'not an outage')."""
    for rx in patterns:
        for m in rx.finditer(text):
            before = re.split(r"[.!?;\n]", text[max(0, m.start() - 40) : m.start()])[-1]
            if not _NEGATION.search(before):
                return True
    return False


def apply_business_rules(subject: str, body: str, result: dict) -> dict:
    """Deterministic post-classification layer. Never raises; returns a new dict."""
    text = f"{subject}\n{body}".replace("\u2019", "'").lower()
    for name, category, priority, any_of, also, unless in RULES:
        if not _hit(any_of, text):
            continue
        if also and not _hit(also, text):
            continue
        if unless and _hit(unless, text):
            continue
        new_priority = priority or result["priority"]
        if (category, new_priority) == (result["category"], result["priority"]):
            return result  # rule agrees with the model; keep the model's reasoning
        logger.info(
            "Business rule '%s' overrode model: %s/%s -> %s/%s",
            name, result["category"], result["priority"], category, new_priority,
        )
        return {
            "category": category,
            "priority": new_priority,
            "reasoning": f"Business rule '{name}' applied (model suggested {result['category']}/{result['priority']}).",
        }
    return result  # 9. no rule matched: keep the validated model output


# --------------------------------------------------------------------------- main entry point
def classify_ticket(subject: str, body: str) -> dict:
    try:
        content = chat_completion(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Subject: {subject}\n\nBody: {body}"},
            ]
        )
    except Exception:
        logger.exception("Classification provider request failed")
        raise TriageError("Classification service unavailable", 502)
    return apply_business_rules(subject, body, parse_model_output(content))


if __name__ == "__main__":
    # Run from the mcp-server folder:  python -m tools.classify "subject" "body"
    logging.basicConfig(level=logging.INFO)
    subj = sys.argv[1] if len(sys.argv) > 1 else "Cannot log in"
    bod = sys.argv[2] if len(sys.argv) > 2 else "I reset my password but still cannot log in."
    print(json.dumps(classify_ticket(subj, bod), indent=2))