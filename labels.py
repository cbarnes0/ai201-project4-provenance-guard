from detection.stylometric import StylometricResult
from detection.llm_classifier import LLMResult

# Thresholds for label assignment
HIGH_AI_THRESHOLD = 0.70
HIGH_HUMAN_THRESHOLD = 0.30


def generate_label(
    combined_score: float,
    stylometric: StylometricResult,
    llm: LLMResult,
) -> dict:
    """
    Generates the transparency label shown to readers.

    combined_score is P(AI) from 0 to 1.
    confidence_pct = |score - 0.5| * 200, so:
      score=0.51 → 2%  (nearly uncertain)
      score=0.70 → 40% (moderate confidence at threshold)
      score=0.95 → 90% (very high confidence)
    """
    confidence_pct = int(abs(combined_score - 0.5) * 200)

    if combined_score >= HIGH_AI_THRESHOLD:
        return {
            "label_type": "HIGH_CONFIDENCE_AI",
            "headline": "Likely AI-Generated",
            "confidence_display": f"High Confidence ({confidence_pct}%)",
            "body": (
                "Our analysis strongly suggests this content was written by an AI. "
                "Two independent signals — a language model assessment and structural "
                "writing pattern analysis — both indicated machine authorship."
            ),
            "action_note": (
                "If you are the human author of this work, you may contest this "
                "classification through the appeals process."
            ),
            "signals_summary": _signals_summary(stylometric, llm),
        }

    if combined_score <= HIGH_HUMAN_THRESHOLD:
        return {
            "label_type": "HIGH_CONFIDENCE_HUMAN",
            "headline": "Likely Human-Written",
            "confidence_display": f"High Confidence ({confidence_pct}%)",
            "body": (
                "Our analysis strongly suggests this content was written by a human. "
                "Both the semantic assessment and structural writing patterns are "
                "consistent with human authorship."
            ),
            "action_note": None,
            "signals_summary": _signals_summary(stylometric, llm),
        }

    return {
        "label_type": "UNCERTAIN",
        "headline": "Origin Uncertain",
        "confidence_display": f"Low Confidence ({confidence_pct}%)",
        "body": (
            "Our system could not make a confident determination about the origin "
            "of this content. It shows mixed signals — some characteristics typical "
            "of AI writing and some typical of human writing."
        ),
        "action_note": (
            "If you authored this content, you may provide context through "
            "the appeals process to help clarify its origin."
        ),
        "signals_summary": _signals_summary(stylometric, llm),
    }


def _signals_summary(stylometric: StylometricResult, llm: LLMResult) -> dict:
    metrics = stylometric.metrics
    cv = metrics.get("sentence_length_cv", None)

    if cv is not None:
        if cv < 0.25:
            cv_note = f"Very uniform sentence structure (CV={cv:.2f}) — consistent with AI"
        elif cv < 0.50:
            cv_note = f"Moderately varied sentence structure (CV={cv:.2f})"
        else:
            cv_note = f"Highly varied sentence structure (CV={cv:.2f}) — consistent with human"
    else:
        cv_note = metrics.get("note", "Insufficient text for analysis")

    return {
        "stylometric": {
            "ai_probability": stylometric.score,
            "primary_signal": cv_note,
        },
        "llm_assessment": {
            "ai_probability": llm.score,
            "reasoning": llm.reasoning,
            "model": llm.model,
        },
    }
