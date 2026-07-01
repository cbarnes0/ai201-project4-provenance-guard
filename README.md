# Provenance Guard

An HTTP API that classifies text as AI-generated or human-written, returns a calibrated confidence score, and surfaces a transparency label a platform could display to readers. Creators can contest a decision through an appeals endpoint. Every decision is captured in a structured audit log.

**[Portfolio walkthrough video (Google Drive)](https://drive.google.com/file/d/1FuazPAMIhde4OtKT5LF2rhbSHiu8KICq/view?usp=sharing)**

---

## Setup

```bash
# 1. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set GROQ_API_KEY to your key from console.groq.com

# 4. Run the server
python app.py
# Server starts on http://localhost:5000
```

---

## Architecture Overview

### Submission path

A `POST /submit` request travels through this pipeline before returning a response:

```
POST /submit
    │
    ├─ Rate limiter (10/min, 50/hr per IP)
    │       └─ OVER LIMIT → 429
    │
    ├─ Input validator (text required, ≤10,000 chars)
    │       └─ INVALID → 400
    │
    ├─ Signal 1: stylometric_analyze(text)        ← pure Python, no network
    │       └─ StylometricResult(score, metrics)
    │
    ├─ Signal 2: llm_classify(text)               ← Groq API call
    │       └─ LLMResult(score, reasoning, model)
    │
    ├─ Score combiner
    │       combined_score = 0.40 × stylometric + 0.60 × llm
    │
    ├─ Label generator
    │       ≥ 0.70 → HIGH_CONFIDENCE_AI
    │       ≤ 0.30 → HIGH_CONFIDENCE_HUMAN
    │        else  → UNCERTAIN
    │
    ├─ Audit logger (thread-safe in-memory dict, keyed by content_id)
    │
    └─ JSON response → client
```

Appeals take a separate path: `POST /appeal` looks up `content_id` in the audit log, appends `creator_reasoning`, and flips `status` to `under_review`. No re-classification occurs.

### Why two signals?

Single-signal detection has an obvious failure mode: if the signal is wrong, there is no check. The two signals chosen here fail in opposite directions, which is the point.

**Signal 1 — Stylometric heuristics** measures the *statistical structure* of writing: how uniform sentence lengths are, how formal the vocabulary register is, how varied the punctuation is. It runs entirely in Python with no external API call. AI writing is structurally uniform — LLMs optimize for readable, coherent output, which means their sentence lengths cluster, their vocabulary sits in a predictable mid-range, and their punctuation is conventional.

**Signal 2 — LLM classification via Groq** measures *semantic and stylistic feel*: whether the text uses hedging phrases, has authentic emotional texture, shows personal specificity, or carries the idiosyncratic choices of a real writer. This runs against `llama-3.3-70b-versatile` at temperature 0.1.

These two signals are genuinely independent — stylometrics reads patterns in character sequences without understanding meaning, and the LLM reads meaning without running any statistics. The same text can produce discordant scores: the spec's sunset text scored 0.73 on stylometrics (two nearly identical sentence lengths → low CV) but 0.2 on the LLM (the model recognized emotional authenticity). Combined: 0.41, landing in UNCERTAIN rather than firing a false confident accusation.

### Why 60/40 weighting?

LLM classification carries more weight (60%) because it captures properties that statistics cannot: hedging language, authentic voice, personal detail. Stylometrics is fast, interpretable, and works without an API key — but on short creative texts it is a weaker prior. Its 40% weight is still not decorative: when the LLM is unavailable it provides a meaningful fallback; when the LLM returns ~0.5 it breaks the tie; and when both signals agree strongly it pushes the combined score further from 0.5.

### Why this threshold design?

The thresholds (≥0.70 = AI, ≤0.30 = Human, else UNCERTAIN) are deliberately asymmetric toward caution. A false accusation causes real harm; expressing uncertainty costs nothing. The displayed confidence percentage (`|score - 0.5| × 200`) tells a non-technical reader how far from a coin-flip the system is — a score of 0.71 maps to "High Confidence (42%)", which is honest: it crossed the threshold, but barely.

### What I would change for real deployment

1. **Persistent storage.** The audit log is in-memory and resets on restart. The `audit.py` module is isolated — the dict can be swapped for a DB-backed store with no changes to any other file.
2. **LLM cost controls.** Every `/submit` hits the Groq API. A production version would cache classifications by text hash, or use a cheaper embedding-based first pass that escalates to the LLM only for uncertain cases.
3. **Signal calibration with labeled data.** The current thresholds and sub-metric weights were calibrated by hand against a small test set. Real calibration requires a labeled corpus and systematic threshold tuning (e.g., ROC analysis).

---

## Detection Signals

### Signal 1 — Stylometric Heuristics

**What it measures:** Statistical properties of writing structure, computed in pure Python with no API call.

| Sub-metric | Applies when | What it measures | AI indicator | Weight |
|---|---|---|---|---|
| **Sentence length CV** | always | Coefficient of variation of words-per-sentence | Low CV → uniform → AI | 60% |
| **Avg word length** | < 100 words | Mean character length of alphabetic words | Longer avg → formal register → AI | 25% |
| **Type-token ratio** | ≥ 100 words | Unique words ÷ total words | Very low TTR → repetitive → AI | 25% |
| **Punctuation variety** | always | Distinct punctuation chars used ÷ charset size | Low variety → AI | 15% |

**Why this signal:** It is fully deterministic (no API dependency), interpretable (each sub-score has a named cause), and fails in a different direction than the LLM. When the API is unavailable, stylometrics alone provides a reasonable fallback score.

**Why the vocabulary metric switches at 100 words:** On short texts, TTR saturates — almost every word appears exactly once regardless of authorship. Testing confirmed all four M4 test inputs (43–55 words) produced TTR ≥ 0.87 across every content type. Average word length does not saturate: formal AI writing reliably uses longer Latinate vocabulary (avg ~5.5–7.0 chars) versus casual human writing (~3.5–4.5 chars). The response includes a `vocab_metric_used` field so callers know which branch ran.

**What it misses:** Non-native English speakers with limited vocabulary produce structurally simple writing that fires the AI signal regardless of authorship — this is a fairness problem, not just an accuracy one (see Known Limitations). Repetition-heavy poetry (refrains, villanelles) drives TTR low, making genuinely human writing look AI-generated. Formal academic human prose has naturally low sentence CV and long average word length — both AI indicators — causing false positives for expository human writing.

---

### Signal 2 — LLM Classifier (Groq)

**What it measures:** Semantic and stylistic feel — whether the text hedges, sounds emotionally authentic, has personal specificity, or carries idiosyncratic human choices. Sends text to `llama-3.3-70b-versatile` (temperature 0.1) with a structured prompt requesting `{"ai_probability": float, "reasoning": string}`.

**Why this signal:** It captures properties that statistics cannot quantify: hedging phrases ("It is worth noting…"), perfectly balanced paragraph structure, absence of concrete detail, uncanny emotional smoothness. These are reliable AI markers the stylometric signal is blind to.

**Fallback behavior:** If the API call fails or returns malformed JSON, the signal returns `score=0.5` (uncertain) — the endpoint keeps working, it just loses the stronger signal.

**What it misses:** LLM score on borderline texts is not fully deterministic even at temperature 0.1. The same input has produced scores ranging from 0.2 to 0.7 across different runs on the project's own test texts (see Known Limitations #4). The LLM also cannot inspect metadata or context outside the text itself — it only sees words.

---

### Signal combination

```
combined_score = 0.40 × stylometric + 0.60 × llm
```

---

## Confidence Scoring

`confidence_score` in the response is the raw combined P(AI) score (0.0 = certainly human, 1.0 = certainly AI). The displayed confidence percentage in the label is a separate quantity:

```
confidence_pct = |combined_score - 0.5| × 200
```

This converts the raw score into "how far from a coin-flip is this?" — a number a non-technical reader can interpret.

| Score | Confidence display | Label type |
|---|---|---|
| 0.09 | High Confidence (81%) | HIGH_CONFIDENCE_HUMAN |
| 0.41 | Low Confidence (17%) | UNCERTAIN |
| 0.50 | Low Confidence (0%) | UNCERTAIN |
| 0.75 | High Confidence (50%) | HIGH_CONFIDENCE_AI |
| 0.95 | High Confidence (90%) | HIGH_CONFIDENCE_AI |

**How the scoring was validated:** After implementing the formula, four test inputs were run — clearly AI, clearly human, and two borderline cases — and the resulting scores were checked for correct direction and sensible magnitude. This is where TTR saturation was discovered (all inputs scored ≥ 0.87 regardless of content), leading to the length-adaptive vocabulary metric. The two example outputs below show the range the system actually produces across genuinely different inputs.

### Two real examples showing meaningful variation

**High-confidence human** — casual ramen review (55 words):

> *"ok so i finally tried that new ramen place downtown and honestly? underwhelming..."*

```
stylometric_score : 0.1574  (sentence CV=0.61 — high variation, human-like)
llm_score         : 0.05    ("informal tone, colloquial expressions, specific sensory detail")
combined_score    : 0.093
label             : HIGH_CONFIDENCE_HUMAN — "High Confidence (81%)"
```

**Lower-confidence UNCERTAIN** — spec's descriptive sunset text (29 words):

> *"The sun dipped below the horizon, painting the sky in hues of amber and rose..."*

```
stylometric_score : 0.7314  (sentence CV=0.03 — two near-identical sentence lengths → AI signal)
llm_score         : 0.20    ("descriptive language and emotional authenticity suggest a human touch")
combined_score    : 0.4126
label             : UNCERTAIN — "Low Confidence (17%)"
```

The signals disagree: stylometrics fires the AI alarm (uniform structure), but the LLM recognizes sensory specificity and emotional register as human. Combined score 0.41 lands in the UNCERTAIN band. A 0.09 and a 0.41 both produce `attribution: "Human"` — but the label text is completely different. This is the design intent.

---

## Transparency Label Variants

All three label types with exact body text as returned by the API.

### HIGH_CONFIDENCE_AI — `combined_score ≥ 0.70`

> **Likely AI-Generated**
> High Confidence (X%)
>
> Our analysis strongly suggests this content was written by an AI. Two independent signals — a language model assessment and structural writing pattern analysis — both indicated machine authorship.
>
> *If you are the human author of this work, you may contest this classification through the appeals process.*

### HIGH_CONFIDENCE_HUMAN — `combined_score ≤ 0.30`

> **Likely Human-Written**
> High Confidence (X%)
>
> Our analysis strongly suggests this content was written by a human. Both the semantic assessment and structural writing patterns are consistent with human authorship.

### UNCERTAIN — `0.30 < combined_score < 0.70`

> **Origin Uncertain**
> Low Confidence (X%)
>
> Our system could not make a confident determination about the origin of this content. It shows mixed signals — some characteristics typical of AI writing and some typical of human writing.
>
> *If you authored this content, you may provide context through the appeals process to help clarify its origin.*

---

## Rate Limiting

| Endpoint | Limit | Reasoning |
|---|---|---|
| `POST /submit` | 10/minute, 50/hour per IP | Each call hits the Groq API — real cost and latency. 10/min is generous for legitimate single-user usage while blocking automated loops. The 50/hr cap prevents an attacker from burning API quota by staying just under the per-minute ceiling. |
| `POST /appeal` | 5/minute per IP | No external API call, but spamming appeals would flood the log and make human review unworkable. 5/min is more than enough for a genuine dispute. |

### Live evidence — 12 rapid requests against running server

```
Request  1 : 200
Request  2 : 200
Request  3 : 200
Request  4 : 200
Request  5 : 200
Request  6 : 200
Request  7 : 200
Request  8 : 200
Request  9 : 200
Request 10 : 429
Request 11 : 429
Request 12 : 429
```

9 of 12 succeeded (one prior submission to obtain a `content_id` consumed the 10th slot). Requests 10–12 received `429 Too Many Requests`:

```json
{
  "error": "Rate limit exceeded",
  "detail": "10 per 1 minute",
  "retry_after": "Please wait before submitting again."
}
```

---

## Known Limitations

### 1. Non-native English speakers — a fairness problem, not just an accuracy problem

Writers with limited English vocabulary produce structurally simple, uniform prose. Low sentence CV, shorter average word length, and consistent register all fire the AI signal — not because the writing resembles AI output, but because the signals were calibrated against native English writing where casual means varied and formal means Latinate. A false HIGH_CONFIDENCE_AI label here is both an accuracy failure and a fairness failure: the system is systematically harder on writers whose linguistic background differs from the calibration baseline. The appeals workflow is the only mitigation in the current design.

### 2. Formal academic human writing — a structural false positive

Academic and expository human writing has naturally low sentence-length CV (paragraphs are consistently structured) and long average word length (domain vocabulary is Latinate). Both fire the AI signal. Live testing confirmed this: a two-sentence monetary-policy excerpt scored 0.74 (HIGH_CONFIDENCE_AI at 47% confidence). The LLM agreed (0.80 AI) because phrasing like "has been extensively studied in the literature" matches AI academic writing patterns. The 47% confidence display and the appeals note on the label are the mitigations — the system signals its uncertainty honestly and tells the author what to do.

### 3. Very short texts — insufficient signal

For texts under about 25 words or 2 sentences, the stylometric signal returns `score=0.5` (the `insufficient_text` fallback) and the LLM has very little to classify. The result is UNCERTAIN with near-zero confidence — not appropriate caution, but genuinely nothing to measure. A haiku, tweet, or one-sentence excerpt will always land UNCERTAIN regardless of actual authorship.

### 4. LLM score variance across runs of identical text

`temperature=0.1` reduces but does not eliminate run-to-run variation in the LLM signal. The project's own sunset test text produced `llm_score=0.7` (combined 0.72, HIGH_CONFIDENCE_AI) in one run and `llm_score=0.2` (combined 0.41, UNCERTAIN) in another — on identical input. The stylometric signal is fully deterministic; the LLM signal is not. A production deployment would want `temperature=0.0`, response caching keyed on a text hash, or multiple-sample averaging.

---

## Spec Reflection

### One way the spec guided implementation

The spec required three specific label variants with different text based on the confidence score. Writing out the exact text of all three variants in `planning.md` *before* writing any code forced a design decision that would have otherwise been deferred: what are the threshold boundaries? Once the label text was fixed, the thresholds became derivable. "High Confidence" implies the system is willing to make a strong claim; "Origin Uncertain" implies it isn't. That language pushed the thresholds outward (0.30/0.70 rather than 0.40/0.60) because the word "strongly" in the body text would be dishonest at a score of 0.65.

### One way implementation diverged from spec

The spec identified type-token ratio as the vocabulary sub-metric for stylometrics. TTR was implemented as specified. During M4 testing, all four test inputs (43–55 words) produced TTR ≥ 0.87 regardless of authorship — the signal was useless at short text lengths. The spec didn't anticipate this because it described signals conceptually rather than testing them empirically. The fix — switching to average word length for texts under 100 words — was not in the spec and required a judgment call that only emerged from testing. This is an example of why implementation always diverges from spec: the spec describes what the system should do; only testing reveals what properties of inputs break those assumptions.

---

## AI Usage

### Instance 1 — Generating the LLM classifier prompt

**What I directed:** Asked Claude to generate `llm_classifier.py` — a function that would reliably return `{"ai_probability": float, "reasoning": string}` from the Groq model, with fallback behavior if the API call failed.

**What it produced:** A function matching the signature, a prompt template covering key discriminating factors (sentence structure uniformity, hedging phrases, emotional authenticity, personal specificity). The structure was correct.

**What I revised:** The initial draft did not set `temperature=0.1`. At default temperature the model's classifications varied noticeably between calls on the same text — making the confidence scores unreproducible. I added `temperature=0.1` explicitly. I also added handling for markdown code fences: the model occasionally wraps its JSON output in ` ```json ``` ` blocks despite instructions not to, causing `json.loads()` to fail. The fence-stripping regex was added after observing this failure mode in testing.

### Instance 2 — Generating the stylometric analyzer

**What I directed:** Provided the planning.md Section 1 normalization formulas and sub-metric weights and asked Claude to generate `stylometric.py` with `analyze(text) -> StylometricResult`.

**What it produced:** A function that matched the signatures and implemented all three sub-metrics with the correct weights. TTR was used universally, as specified.

**What I revised:** After running the M4 four-input test with TTR in place, all test inputs returned TTR ≥ 0.87 (saturated), making the vocabulary sub-metric useless. I identified the root cause (short texts exhaust their vocabulary naturally), researched an alternative (average word length is length-stable), and revised the function to branch on word count: `≥ 100` words uses TTR, `< 100` uses average word length. I also calibrated the normalization range for average word length (`(avg_word_len - 3.5) / 3.5`) against the actual test inputs — casual prose averaged ~4.2 chars, formal AI prose averaged ~6.2 chars — and verified the formula produced the right direction before committing the change.

---

## File Structure

```
provenance-guard/
├── app.py                    # Flask app — all 4 routes + rate limiting
├── audit.py                  # Thread-safe in-memory audit log
├── labels.py                 # Transparency label generation (3 variants)
├── detection/
│   ├── __init__.py
│   ├── stylometric.py        # Signal 1: structural heuristics (length-adaptive)
│   └── llm_classifier.py     # Signal 2: Groq LLM classification
├── planning.md               # Architecture spec, signal design, edge cases
├── requirements.txt
└── .env.example
```

---

## API Reference

### POST /submit

**Request**
```json
{
  "text": "The fog came on little cat feet...",
  "creator_id": "user-123"
}
```

`creator_id` is optional. When provided it appears in the audit log and links an appeal back to the submitter.
Max text length: 10,000 characters.

**Response**
```json
{
  "content_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "attribution": "Human",
  "confidence_score": 0.0895,
  "signals": {
    "stylometric_ai_probability": 0.1487,
    "llm_ai_probability": 0.05
  },
  "transparency_label": {
    "label_type": "HIGH_CONFIDENCE_HUMAN",
    "headline": "Likely Human-Written",
    "confidence_display": "High Confidence (81%)",
    "body": "Our analysis strongly suggests this content was written by a human...",
    "action_note": null,
    "signals_summary": { "..." }
  },
  "status": "classified"
}
```

`confidence_score` is P(AI) from 0.0 to 1.0. Near 0.0 = confident human; near 1.0 = confident AI; near 0.5 = uncertain.

---

### POST /appeal

**Request**
```json
{
  "content_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "creator_reasoning": "I wrote this poem in 2019. My writing style is naturally sparse and structured."
}
```

**Response**
```json
{
  "content_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "under_review",
  "message": "Your appeal has been recorded. The classification is now marked 'under review.' A human reviewer will assess your contest."
}
```

No automated re-classification occurs. A human reviewer retrieves the full entry via `GET /status/{content_id}` to see the original scores, both signal reasoning notes, and the creator's verbatim explanation.

---

### GET /log

Returns all audit entries as a JSON object keyed by `content_id`. Each entry includes `timestamp`, `creator_id`, `content_preview`, `llm_score`, `stylometric_score`, `confidence`, `attribution`, full `signals` block, `label`, `status`, and `appeals` array.

### GET /status/{content_id}

Returns a single audit entry. Use this to check classification details or whether a submission has been appealed.

---

### Live audit log entries (from GET /log)

```json
{
  "116f049b-e4ac-44ff-89ca-16274c41d86f": {
    "content_id": "116f049b-e4ac-44ff-89ca-16274c41d86f",
    "creator_id": "test-user-1",
    "timestamp": "2026-06-30T19:08:30.607455+00:00",
    "content_preview": "The sun dipped below the horizon, painting the sky in hues of amber and rose...",
    "llm_score": 0.2,
    "stylometric_score": 0.7314,
    "confidence": 0.4126,
    "attribution": "Human",
    "signals": {
      "stylometric": {
        "ai_probability": 0.7314,
        "metrics": {
          "sentence_count": 2, "word_count": 29,
          "sentence_length_cv": 0.0345,
          "vocab_metric_used": "avg_word_length", "avg_word_length": 4.2414,
          "punctuation_variety": 0.1111,
          "sub_scores": { "cv_ai_score": 0.9425, "vocab_ai_score": 0.2118, "punct_ai_score": 0.7531 }
        }
      },
      "llm": {
        "ai_probability": 0.2,
        "reasoning": "The text lacks stylistic idiosyncrasies and typos, but its descriptive language and emotional authenticity suggest a human touch.",
        "model": "llama-3.3-70b-versatile"
      }
    },
    "label": { "label_type": "UNCERTAIN", "headline": "Origin Uncertain", "confidence_display": "Low Confidence (17%)" },
    "status": "classified",
    "appeals": []
  },

  "62cb6597-7520-45c9-a67a-4153abae6abd": {
    "content_id": "62cb6597-7520-45c9-a67a-4153abae6abd",
    "creator_id": "test-user-2",
    "timestamp": "2026-06-30T19:08:51.630325+00:00",
    "content_preview": "Artificial intelligence represents a transformative paradigm shift in modern society...",
    "llm_score": 0.9,
    "stylometric_score": 0.5289,
    "confidence": 0.7516,
    "attribution": "AI",
    "signals": {
      "stylometric": {
        "ai_probability": 0.5289,
        "metrics": {
          "sentence_count": 3, "word_count": 43,
          "sentence_length_cv": 0.3793,
          "vocab_metric_used": "avg_word_length", "avg_word_length": 6.2326,
          "punctuation_variety": 0.1111,
          "sub_scores": { "cv_ai_score": 0.3678, "vocab_ai_score": 0.7807, "punct_ai_score": 0.7531 }
        }
      },
      "llm": {
        "ai_probability": 0.9,
        "reasoning": "The text relies heavily on transitional phrases like 'Furthermore' and 'It is important to note' with perfectly consistent paragraph structure.",
        "model": "llama-3.3-70b-versatile"
      }
    },
    "label": { "label_type": "HIGH_CONFIDENCE_AI", "headline": "Likely AI-Generated", "confidence_display": "High Confidence (50%)" },
    "status": "under_review",
    "appeals": [
      {
        "timestamp": "2026-06-30T19:10:14.692904+00:00",
        "creator_reasoning": "I wrote this paragraph as an example for a college essay on AI ethics. The formal academic tone is intentional, not machine-generated."
      }
    ]
  },

  "b0d6bdab-bdfc-4a94-a426-07f6ab648c7f": {
    "content_id": "b0d6bdab-bdfc-4a94-a426-07f6ab648c7f",
    "creator_id": "test-user-3",
    "timestamp": "2026-06-30T19:09:29.361325+00:00",
    "content_preview": "ok so i finally tried that new ramen place downtown and honestly? underwhelming...",
    "llm_score": 0.05,
    "stylometric_score": 0.1487,
    "confidence": 0.0895,
    "attribution": "Human",
    "signals": {
      "stylometric": {
        "ai_probability": 0.1487,
        "metrics": {
          "sentence_count": 5, "word_count": 55,
          "sentence_length_cv": 0.6112,
          "vocab_metric_used": "avg_word_length", "avg_word_length": 4.2593,
          "punctuation_variety": 0.1667,
          "sub_scores": { "cv_ai_score": 0.0, "vocab_ai_score": 0.2169, "punct_ai_score": 0.6296 }
        }
      },
      "llm": {
        "ai_probability": 0.05,
        "reasoning": "Informal tone, colloquial expressions, and specific sensory details are strong indicators of human authorship.",
        "model": "llama-3.3-70b-versatile"
      }
    },
    "label": { "label_type": "HIGH_CONFIDENCE_HUMAN", "headline": "Likely Human-Written", "confidence_display": "High Confidence (81%)" },
    "status": "classified",
    "appeals": []
  }
}
```
