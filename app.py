import os
import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from detection.stylometric import analyze as stylometric_analyze
from detection.llm_classifier import classify as llm_classify
from audit import log_entry, get_log, add_appeal
from labels import generate_label

load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute; 50 per hour")
def submit():
    data = request.get_json(silent=True)
    if not data or "text" not in data:
        return jsonify({"error": "Request body must be JSON with a 'text' field"}), 400

    text = str(data["text"]).strip()
    creator_id = str(data.get("creator_id", "")).strip() or None

    if not text:
        return jsonify({"error": "text cannot be empty"}), 400
    if len(text) > 10_000:
        return jsonify({"error": "text exceeds maximum of 10,000 characters"}), 400

    content_id = str(uuid.uuid4())

    # ── Run both detection signals ──────────────────────────────────────────
    stylometric_result = stylometric_analyze(text)
    llm_result = llm_classify(text)

    # ── Combine: 40% structural, 60% semantic ──────────────────────────────
    combined_score = round(
        0.40 * stylometric_result.score + 0.60 * llm_result.score, 4
    )

    attribution = "AI" if combined_score >= 0.50 else "Human"
    label = generate_label(combined_score, stylometric_result, llm_result)

    # ── Write to audit log ─────────────────────────────────────────────────
    entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_preview": text[:150] + ("..." if len(text) > 150 else ""),
        "llm_score": llm_result.score,
        "stylometric_score": stylometric_result.score,
        "confidence": combined_score,
        "attribution": attribution,
        "signals": {
            "stylometric": {
                "ai_probability": stylometric_result.score,
                "metrics": stylometric_result.metrics,
            },
            "llm": {
                "ai_probability": llm_result.score,
                "reasoning": llm_result.reasoning,
                "model": llm_result.model,
            },
        },
        "label": label,
        "status": "classified",
        "appeals": [],
    }
    log_entry(content_id, entry)

    return jsonify(
        {
            "content_id": content_id,
            "attribution": attribution,
            "confidence_score": combined_score,
            "signals": {
                "stylometric_ai_probability": stylometric_result.score,
                "llm_ai_probability": llm_result.score,
            },
            "transparency_label": label,
            "status": "classified",
        }
    ), 200


@app.route("/appeal", methods=["POST"])
@limiter.limit("5 per minute")
def appeal():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    content_id = str(data.get("content_id", "")).strip()
    if not content_id:
        return jsonify({"error": "content_id field is required"}), 400

    creator_reasoning = str(data.get("creator_reasoning", "")).strip()
    if not creator_reasoning:
        return jsonify({"error": "creator_reasoning field is required"}), 400

    success = add_appeal(content_id, creator_reasoning)
    if not success:
        return jsonify({"error": f"No submission found with id '{content_id}'"}), 404

    return jsonify(
        {
            "content_id": content_id,
            "status": "under_review",
            "message": (
                "Your appeal has been recorded. The classification is now marked "
                "'under review.' A human reviewer will assess your contest."
            ),
        }
    ), 200


@app.route("/log", methods=["GET"])
def audit_log():
    return jsonify(get_log()), 200


@app.route("/status/<content_id>", methods=["GET"])
def status(content_id):
    log = get_log()
    if content_id not in log:
        return jsonify({"error": f"No submission found with id '{content_id}'"}), 404
    return jsonify(log[content_id]), 200


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(
        {
            "error": "Rate limit exceeded",
            "detail": str(e.description),
            "retry_after": "Please wait before submitting again.",
        }
    ), 429


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
