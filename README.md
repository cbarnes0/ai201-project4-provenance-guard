# Provenance Guard

An HTTP API that classifies text as AI-generated or human-written, returns a calibrated confidence score, and surfaces a transparency label a platform could display to readers. Creators can contest a decision through an appeals endpoint. Every decision is captured in a structured audit log.

---

## Architecture & Design Decisions

### Why two signals?

Single-signal detection has an obvious failure mode: if the signal is wrong, there is no check. The two signals chosen here fail in opposite directions, which is the point.

**Signal 1 — Stylometric heuristics** measures the *statistical structure* of writing: how uniform sentence lengths are, how formal the vocabulary register is, how varied the punctuation is. It runs entirely in Python with no external API call. AI writing is structurally uniform — LLMs optimize for readable, coherent output, which means their sentence lengths cluster, their vocabulary sits in a predictable mid-range, and their punctuation is conventional. Human writing reflects moods and habits.

**Signal 2 — LLM classification via Groq** measures *semantic and stylistic feel*: whether the text uses hedging phrases, has authentic emotional texture, shows personal specificity, or carries the idiosyncratic choices of a real writer. This runs against `llama-3.3-70b-versatile` at temperature 0.1.

These two signals are genuinely independent. Stylometrics reads patterns in character sequences without understanding meaning. The LLM reads meaning without running any statistics. The same text can produce discordant scores — the spec's sun/porch text scored 0.73 on stylometrics (two nearly identical sentence lengths → low CV) but 0.2 on the LLM (the model recognized emotional authenticity). The combined score, 0.41, correctly reflects that the signals disagree, and the UNCERTAIN label fires instead of a false confident accusation.

### Why 60/40 weighting?

LLM classification carries more weight (60%) because it captures properties that statistics cannot: hedging language, authentic voice, personal detail. Stylometrics is fast, interpretable, and works without an API key — but on short creative texts it is a weaker prior. A writer who naturally structures clean paragraphs will look AI-like stylometrically. The LLM is better at correcting that.

The 40% weight for stylometrics is not decorative. When the LLM is unavailable (API failure), stylometrics degrades gracefully to a reasonable fallback score. When the LLM is uncertain (returns ~0.5), stylometrics breaks the tie. And in cases where they agree strongly, the 40% contribution pushes the combined score further from 0.5, increasing displayed confidence.

### Why this threshold design?

The thresholds (≥0.70 = AI, ≤0.30 = Human, else UNCERTAIN) are deliberately asymmetric toward caution. A system that falsely accuses a human writer of plagiarism causes real harm; a system that expresses uncertainty costs nothing. The 0.70 floor means a combined score needs both signals leaning AI before the HIGH_CONFIDENCE_AI label fires. A score of 0.65 — where the LLM says 0.70 and stylometrics says 0.57 — stays UNCERTAIN, which is the right answer.

The displayed confidence percentage (`|score - 0.5| × 200`) is a separate quantity from the raw score. It tells a non-technical reader how far from a coin-flip the system is, not what the underlying probability is. A score of 0.71 maps to "High Confidence (42%)" — which is honest. It crossed the threshold, but barely.

### What I would change for real deployment

Three things:

1. **Persistent storage.** The audit log is in-memory and resets on restart. A production version needs a database (SQLite is sufficient for moderate volume; PostgreSQL for scale). The `audit.py` module is isolated — the dict can be swapped for a DB-backed store with no changes to any other file.

2. **LLM cost controls.** Every `/submit` call hits the Groq API. At scale this becomes expensive. A production version would cache classifications for identical text (hash-keyed), or use a cheaper embedding-based first pass that only escalates to the full LLM for uncertain cases.

3. **Signal calibration with labeled data.** The current thresholds (0.30/0.70) and sub-metric weights were calibrated by hand against a small test set. Real calibration requires a labeled corpus of known AI and human texts and systematic threshold tuning (e.g., ROC analysis). The TTR→average-word-length adaptation for short texts was discovered empirically during testing, not from prior research — a real system would run this analysis before choosing metrics.

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
    "confidence_display": "High Confidence (82%)",
    "body": "Our analysis strongly suggests this content was written by a human...",
    "action_note": null,
    "signals_summary": { ... }
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

---

### GET /log

Returns all audit entries as a JSON object keyed by `content_id`.

---

### GET /status/{content_id}

Returns a single audit entry including any appeals. Use this to check whether a classification has been appealed.

---

## Detection Signals

### Signal 1 — Stylometric Heuristics (structural)

Pure Python, no API required.

| Sub-metric | Applies when | What it measures | AI indicator | Weight |
|---|---|---|---|---|
| **Sentence length CV** | always | Coefficient of variation of words-per-sentence | Low CV → uniform → AI | 60% |
| **Avg word length** | < 100 words | Mean character length of alphabetic words | Longer avg → formal register → AI | 25% |
| **Type-token ratio** | ≥ 100 words | Unique words ÷ total words | Very low TTR → repetitive → AI | 25% |
| **Punctuation variety** | always | Distinct punctuation chars used ÷ charset size | Low variety → AI | 15% |

**Why the vocabulary metric switches at 100 words:** On short texts, TTR saturates — almost every word appears exactly once regardless of authorship. Testing confirmed all four M4 test inputs (43–55 words) produced TTR ≥ 0.87 across every content type. TTR provides no signal. Average word length does not saturate: formal AI writing reliably uses longer Latinate vocabulary (avg ~5.5–7.0 chars); casual human writing uses shorter words (avg ~3.5–4.5 chars). The response includes a `vocab_metric_used` field so callers know which branch ran.

---

### Signal 2 — LLM Classifier (Groq)

Sends text to `llama-3.3-70b-versatile` (temperature 0.1) with a structured prompt requesting `{"ai_probability": float, "reasoning": string}`. Returns P(AI) and one sentence of reasoning.

The LLM carries 60% weight because it captures what statistics cannot: hedging phrases, authentic personal voice, emotional texture, specific concrete detail vs. AI's smooth generalization.

If the API call fails or returns malformed JSON, the signal falls back to 0.5 (uncertain) — the endpoint keeps working, it just loses the stronger signal.

---

### Signal combination

```
combined_score = 0.40 × stylometric + 0.60 × llm
```

---

## Confidence Scoring

`confidence_score` in the response is the raw combined P(AI) score. The displayed confidence percentage in the label is:

```
confidence_pct = |combined_score - 0.5| × 200
```

| Score | Confidence display | Label type |
|---|---|---|
| 0.0895 | High Confidence (82%) | HIGH_CONFIDENCE_HUMAN |
| 0.4126 | Low Confidence (17%) | UNCERTAIN |
| 0.5 | Low Confidence (0%) | UNCERTAIN |
| 0.7516 | High Confidence (50%) | HIGH_CONFIDENCE_AI |
| 0.95 | High Confidence (90%) | HIGH_CONFIDENCE_AI |

### Two real examples showing meaningful variation

**High-confidence human** — casual ramen review (55 words):

> *"ok so i finally tried that new ramen place downtown and honestly? underwhelming..."*

```
stylometric_score : 0.1487  (sentence CV=0.61 — high variation, human-like)
llm_score         : 0.05    ("informal tone, colloquial expressions, specific sensory detail")
combined_score    : 0.0895
label             : HIGH_CONFIDENCE_HUMAN — "High Confidence (82%)"
```

**Lower-confidence UNCERTAIN** — spec's descriptive sunset text (29 words):

> *"The sun dipped below the horizon, painting the sky in hues of amber and rose..."*

```
stylometric_score : 0.7314  (sentence CV=0.03 — only 2 near-identical sentence lengths → AI signal)
llm_score         : 0.2     ("descriptive language and emotional authenticity suggest a human touch")
combined_score    : 0.4126
label             : UNCERTAIN — "Low Confidence (17%)"
```

The signals disagree: stylometrics fires the AI alarm (two structurally identical sentences), but the LLM recognizes the sensory specificity and emotional register of the text as human. Combined score 0.41 sits in the UNCERTAIN band and the label says so directly. A 0.09 and a 0.41 both produce `attribution: "Human"` — but the label text is completely different. This is the design intent.

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

## Appeals Workflow

A creator who disputes a classification sends `POST /appeal` with their `content_id` and `creator_reasoning`. The system:

1. Looks up the `content_id` in the audit log — returns 404 if unknown.
2. Appends `{ timestamp, creator_reasoning }` to `entry["appeals"]`.
3. Sets `entry["status"]` from `"classified"` to `"under_review"`.
4. Returns a confirmation.

No automated re-classification occurs. The appeals endpoint captures the human context (the creator's explanation) that the automated pipeline cannot see. A human reviewer retrieves the full entry via `GET /status/{content_id}` and sees: the original score, both signal scores and reasoning, and the creator's verbatim explanation.

---

## Rate Limiting

| Endpoint | Limit | Reasoning |
|---|---|---|
| `POST /submit` | 10/minute, 50/hour per IP | Each call hits the Groq API — real cost and latency. 10/min is generous for legitimate single-user usage while blocking scripts that loop submissions. The 50/hr cap prevents an attacker from burning significant API quota by staying just under the per-minute limit. |
| `POST /appeal` | 5/minute per IP | No external API call, but spamming appeals would flood the log and make human review unworkable. 5/min is more than enough for a real dispute; low enough to prevent abuse. |

### Rate-limit test output — 12 rapid requests against live server

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

9 of 12 succeeded (the test also submitted once to obtain a `content_id` before the loop, consuming the 10th slot). Requests 10–12 in the loop received `429 Too Many Requests`:

```json
{
  "error": "Rate limit exceeded",
  "detail": "10 per 1 minute",
  "retry_after": "Please wait before submitting again."
}
```

---

## Audit Log

Every decision is stored in a thread-safe in-memory dict keyed by `content_id`. Each entry captures: `timestamp`, `content_id`, `creator_id`, `content_preview` (150 chars), `llm_score`, `stylometric_score`, `confidence` (combined), `attribution`, full `signals` block with sub-metric detail, `label`, `status`, and `appeals` array.

**Production note:** The log resets on restart. Swap the dict in `audit.py` for a database-backed store for persistence. No other files need to change.

### Live log entries (from GET /log)

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
        "reasoning": "Informal tone, colloquial expressions, and specific sensory details (the physical reaction to sodium) are strong indicators of human authorship.",
        "model": "llama-3.3-70b-versatile"
      }
    },
    "label": { "label_type": "HIGH_CONFIDENCE_HUMAN", "headline": "Likely Human-Written", "confidence_display": "High Confidence (82%)" },
    "status": "classified",
    "appeals": []
  }
}
```

---

## Known Limitations

### 1. Non-native English speakers — a fairness problem, not just an accuracy problem

A writer with limited English vocabulary tends to produce shorter sentences, simpler word choices, and more uniform sentence structure. All three fire the AI signal: low sentence CV, low average word length (casual), and a stylometric score that tilts human for the wrong reason (average word length is short like casual prose, not because the writing is colloquial but because the writer's vocabulary is constrained). Meanwhile, the LLM may read the simplified, consistent register as AI-like and score it high.

This is not a generic calibration problem. It is structural: the stylometric signal was calibrated against native English writing where casual = short/varied and formal = long/uniform. Non-native writing breaks that assumption — it can be formal in intent but simple in execution, which neither signal is designed to handle correctly. A false HIGH_CONFIDENCE_AI label on a non-native English writer's genuine work is both an accuracy failure and a fairness failure: the system is systematically harder on writers whose linguistic background differs from the calibration baseline.

The appeals workflow is the only mitigation in the current design.

### 2. Very short texts — insufficient signal, not just low confidence

For texts under about 25 words or 2 sentences, the stylometric signal returns `score=0.5` (the `insufficient_text` fallback). The LLM also has very little to classify and tends toward moderate scores. The result is UNCERTAIN with near-zero confidence — not because the system is being appropriately cautious, but because there is genuinely nothing to measure. A haiku, a tweet, or a one-sentence excerpt will always land UNCERTAIN regardless of actual authorship. This is correct behavior (expressing uncertainty when uncertain) but may mislead users who expect the system to work on any input.

### 3. Formal academic human writing — a structural false positive

Academic and expository human writing has naturally low sentence-length CV (paragraphs are consistently structured) and long average word length (domain vocabulary is Latinate). Both of these fire the AI signal. Live testing confirmed this: a two-sentence monetary-policy excerpt scored 0.74 (HIGH_CONFIDENCE_AI at 47% confidence). The LLM agreed (0.80 AI), probably because "has been extensively studied in the literature" and "face a fundamental tension between" are common AI academic writing patterns. The 47% confidence display and the appeals note on the label are the mitigations — the system doesn't suppress the classification, it signals its uncertainty honestly and tells the author what to do.

### 4. LLM score variance across runs of identical text

`temperature=0.1` reduces but does not eliminate run-to-run variation in the LLM signal. Re-running the project's own descriptive sunset example during the final walkthrough produced `llm_score=0.7` (combined 0.72, HIGH_CONFIDENCE_AI) versus an earlier run's `llm_score=0.2` (combined 0.41, UNCERTAIN) on the identical input text — see the Confidence Scoring examples above for the original capture. The stylometric signal is fully deterministic since it's a pure function of the text; the LLM signal is not. This means the same submission can, in rare cases, receive a different label on a second submission. A production deployment would want either `temperature=0.0`, response caching keyed on a text hash, or multiple-sample averaging to make classifications reproducible.

---

## Spec Reflection

### One way the spec guided implementation

The spec required three specific label variants with different text based on the confidence score — not just a binary label. Writing out the exact text of all three variants in `planning.md` *before* writing any code forced a design decision that would have otherwise been deferred: what are the threshold boundaries? Once the label text was fixed, the thresholds became derivable. "High Confidence" implies the system is willing to make a claim; "Low Confidence" / "Origin Uncertain" implies it isn't. That language pushed the thresholds outward (0.30/0.70 rather than 0.40/0.60) because the word "strongly" in the body text would be dishonest at a score of 0.65.

### One way implementation diverged from spec

The spec specified stylometric heuristics as the second signal and mentioned type-token ratio as a candidate metric. TTR was implemented as specified. During M4 testing we discovered that TTR saturates on short texts: all four test inputs (43–55 words) produced TTR ≥ 0.87 regardless of authorship, making it useless as a discriminator. The spec didn't anticipate this because it described signals conceptually rather than testing them empirically.

The fix — switching to average word length for texts under 100 words — was not in the spec. It was discovered through testing and required a judgment call about which property would remain discriminating at short text lengths. This is an example of why implementation always diverges from spec: the spec describes what the system should do; only testing reveals what properties of inputs break those assumptions.

---

## AI Usage

### Instance 1 — Generating the LLM classifier prompt

**What I directed:** I asked Claude to generate `llm_classifier.py` — specifically a prompt that would reliably return a JSON object `{"ai_probability": float, "reasoning": string}` from the Groq model, with fallback behavior if the API call failed.

**What it produced:** A function matching the signature, a prompt template that covered key discriminating factors (sentence structure uniformity, hedging phrases, emotional authenticity, personal specificity). The prompt structure was correct.

**What I revised:** The initial draft did not set `temperature=0.1`. At default temperature the model's classifications varied noticeably between calls on the same text — which makes the confidence scores meaningless because they aren't reproducible. I added `temperature=0.1` explicitly. I also added handling for markdown code fences: the model occasionally wraps its JSON output in ` ```json ``` ` blocks despite instructions not to, and without stripping those, `json.loads()` fails. The fallback to `score=0.5` on parse error was in the original draft but I added the fence-stripping logic.

### Instance 2 — Generating the stylometric analyzer

**What I directed:** I provided the planning.md Section 1 normalization formulas and sub-metric weights and asked Claude to generate `stylometric.py` with `analyze(text) -> StylometricResult`.

**What it produced:** A function that matched the signatures and implemented all three sub-metrics with the correct weights. TTR was used universally, as specified.

**What I revised:** After running the M4 four-input test with TTR in place, all test inputs returned TTR ≥ 0.87 (saturated), making the vocabulary sub-metric useless. I identified the root cause (short texts always exhaust their vocabulary naturally), researched an alternative (average word length is length-stable), and revised the function to branch on word count: `>= 100` words uses TTR, `< 100` uses average word length. I also calibrated the normalization range for average word length (`(avg_word_len - 3.5) / 3.5`) against the actual test inputs — casual prose averaged ~4.2 chars, formal AI prose averaged ~6.2 chars — and verified the formula produced the right direction before committing the change.

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
