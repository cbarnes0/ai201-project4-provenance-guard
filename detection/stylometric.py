import re
import math
from dataclasses import dataclass, field

# Below this word count, TTR saturates (every word is unique just from sentence variety).
# Switch to average word length instead, which remains meaningful at any text length.
TTR_MIN_WORDS = 100


@dataclass
class StylometricResult:
    score: float  # P(AI): 0.0 = definitely human, 1.0 = definitely AI
    metrics: dict = field(default_factory=dict)


def analyze(text: str) -> StylometricResult:
    """
    Computes structural writing statistics to estimate P(AI-generated).

    AI writing optimizes for readability, producing statistically uniform text.
    Human writing reflects mood, habits, and idiosyncrasies — it is variable.

    Sub-metrics (weights: CV 60%, vocab 25%, punctuation variety 15%):
      1. Sentence length CV: low CV → uniform sentence structure → AI
      2. Vocabulary metric (length-adaptive):
           >= 100 words: Type-token ratio — very low TTR → repetitive → AI
           <  100 words: Average word length — longer avg → formal register → AI
         TTR is unreliable on short texts because almost every word is unique by
         chance, regardless of authorship. Average word length is length-independent.
      3. Punctuation variety: few distinct punct chars → AI
    """
    sentences = _split_sentences(text)
    words = _tokenize(text)
    word_count = len(words)

    if len(sentences) < 2 or word_count < 15:
        return StylometricResult(score=0.5, metrics={"note": "insufficient_text"})

    # ── Sub-metric 1: Sentence length coefficient of variation ────────────
    sent_lengths = [len(s.split()) for s in sentences if s.strip()]
    mean_len = sum(sent_lengths) / len(sent_lengths)
    if mean_len > 0:
        variance = sum((l - mean_len) ** 2 for l in sent_lengths) / len(sent_lengths)
        cv = math.sqrt(variance) / mean_len
    else:
        cv = 0.0

    # CV < 0.20 → very uniform → strong AI (score → 1.0)
    # CV > 0.60 → highly variable → strong human (score → 0.0)
    cv_ai_score = max(0.0, min(1.0, 1.0 - (cv / 0.60)))

    # ── Sub-metric 2: Vocabulary (length-adaptive) ────────────────────────
    alpha_words = [w for w in words if w.isalpha()]
    unique_words = set(w.lower() for w in words)

    if word_count >= TTR_MIN_WORDS:
        # Type-token ratio: low → repetitive → AI
        ttr = len(unique_words) / word_count
        vocab_ai_score = max(0.0, min(1.0, 1.0 - ((ttr - 0.30) / 0.55)))
        vocab_metric = {"type_token_ratio": round(ttr, 4)}
    else:
        # Average word length: longer → formal/academic register → AI-like
        # Casual human: avg ~3.5–4.5; formal AI: avg ~5.5–7.0
        avg_word_len = (
            sum(len(w) for w in alpha_words) / len(alpha_words)
            if alpha_words else 4.5
        )
        vocab_ai_score = max(0.0, min(1.0, (avg_word_len - 3.5) / 3.5))
        vocab_metric = {"avg_word_length": round(avg_word_len, 4)}

    # ── Sub-metric 3: Punctuation variety ────────────────────────────────
    all_punct = set('.,!?;:—–"\'()[]{}…-')
    used_punct = set(c for c in text if c in all_punct)
    punct_variety = len(used_punct) / len(all_punct) if all_punct else 0.0
    # High variety (dashes, ellipses, parentheses, exclamations) → human
    # Low variety (just periods and commas) → AI
    punct_ai_score = max(0.0, min(1.0, 1.0 - (punct_variety / 0.45)))

    combined = (0.60 * cv_ai_score) + (0.25 * vocab_ai_score) + (0.15 * punct_ai_score)

    return StylometricResult(
        score=round(combined, 4),
        metrics={
            "sentence_count": len(sent_lengths),
            "word_count": word_count,
            "mean_sentence_length_words": round(mean_len, 2),
            "sentence_length_cv": round(cv, 4),
            "vocab_metric_used": "type_token_ratio" if word_count >= TTR_MIN_WORDS else "avg_word_length",
            **vocab_metric,
            "punctuation_variety": round(punct_variety, 4),
            "sub_scores": {
                "cv_ai_score": round(cv_ai_score, 4),
                "vocab_ai_score": round(vocab_ai_score, 4),
                "punct_ai_score": round(punct_ai_score, 4),
            },
        },
    )


def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _tokenize(text: str) -> list:
    return re.findall(r"\b[a-zA-Z']+\b", text)
