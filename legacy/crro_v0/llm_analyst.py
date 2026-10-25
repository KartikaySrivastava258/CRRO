"""
AI Recovery Analyst.

The LLM is a reasoning/explanation layer only. Its output is strictly
schema-constrained and is always validated against the deterministic
ACTION_REGISTRY and the case's eligible actions before it can influence
downstream CRRO decisions.

Providers:
  - groq (preferred when GROQ_API_KEY is configured)
  - anthropic (backward-compatible)
  - mock (deterministic fallback for tests/demo)

The selected provider is reported in `source`; credentials are never
hard-coded or exposed to the frontend.
"""

import json
import os
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from .action_registry import ACTION_REGISTRY
from .evidence import Evidence

DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
DEFAULT_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


class RecommendedAction(BaseModel):
    action: str
    reason: str


class RecoveryAnalysis(BaseModel):
    case_id: str
    diagnosis: str
    recommended_actions: List[RecommendedAction]
    evidence: List[str]
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = "llm"

    @field_validator("confidence")
    @classmethod
    def _clip_confidence(cls, v):
        return max(0.0, min(1.0, v))


SYSTEM_PROMPT = """You are the CRRO AI Recovery Analyst for a payments platform.
You diagnose why a payment failed and recommend which recovery actions are worth considering.
You do NOT make the final decision. A separate deterministic CRRO system validates, prices,
optimizes, authorizes, and executes actions.

Rules:
- Only recommend actions from this exact list: {allowed_actions}
- Never invent an action name.
- Never invent a rupee amount, discount percentage, offer, customer history, payment status,
  or evidence. Use only the supplied case and evidence.
- Do not execute tools or claim an action was executed.
- Recommend 1-3 actions when appropriate.
- Return ONLY JSON matching the supplied schema.
"""

USER_PROMPT_TEMPLATE = """Case: {case_id}
Amount: ₹{amount_inr}
Payment method: {payment_method}
Failure reason: {failure_reason}
Attempt count: {attempt_count}
Minutes since failure: {minutes_since_failure}

Evidence retrieved about this customer:
{evidence_bullets}

Eligible actions for this case: {eligible_actions}

Diagnose the likely recovery problem and recommend 1-3 eligible actions, each with a short
merchant-friendly reason. Cite evidence by using the supplied evidence text in the `evidence`
array; do not create new facts.
"""


def _mock_analysis(case_row, evidence: Evidence, eligible_actions: List[str]) -> RecoveryAnalysis:
    fr = case_row["failure_reason"]
    candidates = [a for a in eligible_actions if a != "NO_ACTION"]
    picks = []
    if fr == "card_expired" and "ALTERNATIVE_PAYMENT_METHOD" in candidates:
        picks.append(("ALTERNATIVE_PAYMENT_METHOD", "Card has expired; an alternate payment method avoids the same failure."))
    if fr == "risk_block" and "PAYMENT_LINK" in candidates:
        picks.append(("PAYMENT_LINK", "Risk block suggests the original channel is unreliable; a payment link offers a cleaner path."))
    if fr == "insufficient_funds" and "RETRY_LATER" in candidates:
        picks.append(("RETRY_LATER", "Insufficient funds may resolve after a short delay before retrying."))
    if not picks and "RETRY_NOW" in candidates:
        picks.append(("RETRY_NOW", "No strong signal against an immediate retry."))
    if not picks:
        picks.append(("NO_ACTION", "No eligible action clearly outperforms doing nothing."))

    diagnosis = f"Payment failed due to {fr} on {case_row['payment_method']}, attempt #{case_row['attempt_count']}."
    return RecoveryAnalysis(
        case_id=case_row["case_id"], diagnosis=diagnosis,
        recommended_actions=[RecommendedAction(action=a, reason=r) for a, r in picks],
        evidence=evidence.bullets, confidence=0.5, source="mock_no_api_key",
    )


def _validate_and_filter(analysis: RecoveryAnalysis, eligible_actions: List[str]) -> RecoveryAnalysis:
    allowed = {a for a in eligible_actions if a in ACTION_REGISTRY}
    filtered = [ra for ra in analysis.recommended_actions if ra.action in allowed]
    if not filtered:
        filtered = [RecommendedAction(action="NO_ACTION", reason="No recommended action passed registry/eligibility validation.")]
    analysis.recommended_actions = filtered
    return analysis


def _build_prompt(case_row, eligible_actions: List[str], evidence: Evidence):
    system = SYSTEM_PROMPT.format(allowed_actions=", ".join(eligible_actions))
    user = USER_PROMPT_TEMPLATE.format(
        case_id=case_row["case_id"], amount_inr=case_row["amount_inr"],
        payment_method=case_row["payment_method"], failure_reason=case_row["failure_reason"],
        attempt_count=case_row["attempt_count"],
        minutes_since_failure=case_row.get("minutes_since_failure", "unknown"),
        evidence_bullets="\n".join(f"- {b}" for b in evidence.bullets),
        eligible_actions=", ".join(eligible_actions),
    )
    return system, user


def _analysis_json_schema():
    return {
        "type": "object",
        "properties": {
            "case_id": {"type": "string"},
            "diagnosis": {"type": "string"},
            "recommended_actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"action": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["action", "reason"],
                    "additionalProperties": False,
                },
            },
            "evidence": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["case_id", "diagnosis", "recommended_actions", "evidence", "confidence"],
        "additionalProperties": False,
    }


def _analyze_with_groq(system: str, user: str) -> RecoveryAnalysis:
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model=DEFAULT_GROQ_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "recovery_analysis",
                "strict": True,
                "schema": _analysis_json_schema(),
            },
        },
    )
    data = json.loads(response.choices[0].message.content or "{}")
    data["source"] = "groq"
    return RecoveryAnalysis(**data)


def _analyze_with_anthropic(system: str, user: str) -> RecoveryAnalysis:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=DEFAULT_ANTHROPIC_MODEL, max_tokens=700,
        system=system, messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    data = json.loads(text)
    data["source"] = "anthropic"
    return RecoveryAnalysis(**data)


def analyze_case(case_row, eligible_actions: List[str], evidence: Optional[Evidence] = None) -> RecoveryAnalysis:
    """Analyze a case using the configured live provider, with safe mock fallback."""
    if evidence is None:
        from .evidence import build_evidence
        evidence = build_evidence(case_row, eligible_actions)

    system, user = _build_prompt(case_row, eligible_actions, evidence)
    provider = os.environ.get("LLM_PROVIDER", "auto").lower()

    if provider == "mock":
        return _validate_and_filter(_mock_analysis(case_row, evidence, eligible_actions), eligible_actions)

    if provider in {"auto", "groq"} and os.environ.get("GROQ_API_KEY"):
        try:
            return _validate_and_filter(_analyze_with_groq(system, user), eligible_actions)
        except Exception as e:
            if provider == "groq":
                fallback = _mock_analysis(case_row, evidence, eligible_actions)
                fallback.source = "llm_error_fallback"
                fallback.diagnosis += f" [Groq call failed, used fallback: {type(e).__name__}]"
                return _validate_and_filter(fallback, eligible_actions)

    if provider in {"auto", "anthropic"} and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _validate_and_filter(_analyze_with_anthropic(system, user), eligible_actions)
        except Exception as e:
            fallback = _mock_analysis(case_row, evidence, eligible_actions)
            fallback.source = "llm_error_fallback"
            fallback.diagnosis += f" [Anthropic call failed, used fallback: {type(e).__name__}]"
            return _validate_and_filter(fallback, eligible_actions)

    return _validate_and_filter(_mock_analysis(case_row, evidence, eligible_actions), eligible_actions)
