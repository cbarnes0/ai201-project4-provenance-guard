# Live demo script for the portfolio walkthrough (see WALKTHROUGH.md).
# Run each step interactively, or run the whole file end to end.
# Assumes the server is already running: python app.py

$env:PYTHONIOENCODING = "utf-8"
$base = "http://localhost:5000"

function Show-Submission($resp) {
    Write-Host "  content_id : $($resp.content_id)"
    Write-Host "  attribution: $($resp.attribution)"
    Write-Host "  confidence : $($resp.confidence_score)"
    Write-Host "  stylometric: $($resp.signals.stylometric_ai_probability)"
    Write-Host "  llm        : $($resp.signals.llm_ai_probability)"
    Write-Host "  label      : $($resp.transparency_label.label_type) - $($resp.transparency_label.confidence_display)"
    Write-Host "  headline   : $($resp.transparency_label.headline)"
}

# ── Step 1: clearly human, casual text ───────────────────────────────────────
Write-Host "`n=== Step 1: Submit casual human text ==="
$human = @{
    text = "ok so i finally tried that new ramen place downtown and honestly? underwhelming. broth was fine but way too salty, and they charged extra for an egg which felt like a crime. probably wont go back unless someone else is paying lol"
    creator_id = "demo-human"
} | ConvertTo-Json
$humanResp = Invoke-RestMethod -Uri "$base/submit" -Method Post -ContentType "application/json" -Body $human
Show-Submission $humanResp

# ── Step 2: clearly AI, formal text ──────────────────────────────────────────
Write-Host "`n=== Step 2: Submit formal AI-style text ==="
$ai = @{
    text = "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, organizations must carefully evaluate the long-term consequences of widespread adoption."
    creator_id = "demo-ai-style"
} | ConvertTo-Json
$aiResp = Invoke-RestMethod -Uri "$base/submit" -Method Post -ContentType "application/json" -Body $ai
Show-Submission $aiResp
$aiContentId = $aiResp.content_id

# ── Step 3: the spec's descriptive sunset text — borderline, LLM score varies ─
Write-Host "`n=== Step 3: Submit the spec's descriptive sunset text (borderline case) ==="
$uncertain = @{
    text = "The sun dipped below the horizon, painting the sky in hues of amber and rose. She watched in silence, the warmth of the day fading into a cool evening breeze."
    creator_id = "demo-uncertain"
} | ConvertTo-Json
$uncertainResp = Invoke-RestMethod -Uri "$base/submit" -Method Post -ContentType "application/json" -Body $uncertain
Show-Submission $uncertainResp

$styScore = $uncertainResp.signals.stylometric_ai_probability
$llmScore = $uncertainResp.signals.llm_ai_probability
$gap = [Math]::Abs($styScore - $llmScore)
if ($gap -ge 0.3) {
    Write-Host "  -> signals DISAGREE this run (gap=$([Math]::Round($gap,2))): stylometric=$styScore vs llm=$llmScore"
} else {
    Write-Host "  -> signals AGREE this run (gap=$([Math]::Round($gap,2))): stylometric=$styScore vs llm=$llmScore"
}
Write-Host "  -> this text's LLM score varies between runs (observed 0.2-0.7); see README Known Limitations #4"

# ── Step 4: appeal the AI-flagged submission from Step 2 ─────────────────────
Write-Host "`n=== Step 4: Appeal the formal-text classification ==="
$appeal = @{
    content_id = $aiContentId
    creator_reasoning = "I wrote this myself for a college essay on AI ethics. The formal academic tone is intentional, not machine-generated."
} | ConvertTo-Json
$appealResp = Invoke-RestMethod -Uri "$base/appeal" -Method Post -ContentType "application/json" -Body $appeal
Write-Host "  status : $($appealResp.status)"
Write-Host "  message: $($appealResp.message)"

# ── Step 5: confirm the appeal landed in the record ──────────────────────────
Write-Host "`n=== Step 5: GET /status to confirm appeal recorded ==="
$statusResp = Invoke-RestMethod -Uri "$base/status/$aiContentId" -Method Get
Write-Host "  status         : $($statusResp.status)"
Write-Host "  appeals count  : $($statusResp.appeals.Count)"
Write-Host "  creator_reasoning: $($statusResp.appeals[0].creator_reasoning)"

# ── Step 6: rate limit test — 12 rapid requests ───────────────────────────────
Write-Host "`n=== Step 6: Rate limit test (12 rapid POST /submit requests, limit is 10/min) ==="
$body = '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "demo-ratelimit"}'
$codes = @()
for ($i = 1; $i -le 12; $i++) {
    $code = try {
        $r = Invoke-WebRequest -Uri "$base/submit" -Method Post -ContentType "application/json" -Body $body -UseBasicParsing -ErrorAction Stop
        $r.StatusCode
    } catch {
        $_.Exception.Response.StatusCode.value__
    }
    $codes += $code
    Write-Host "  request $i : $code"
}
$ok  = ($codes | Where-Object { $_ -eq 200 }).Count
$too = ($codes | Where-Object { $_ -eq 429 }).Count
Write-Host "  200 OK: $ok    429 Too Many: $too"

# ── Step 7: full audit log ────────────────────────────────────────────────────
Write-Host "`n=== Step 7: GET /log (full audit trail) ==="
$log = Invoke-RestMethod -Uri "$base/log" -Method Get
$entries = @($log.PSObject.Properties)
Write-Host "  total entries: $($entries.Count)"
Write-Host ""
Write-Host ("  {0,-10} {1,-8} {2,-6} {3,-22} {4,-6}" -f "content_id", "attrib", "conf", "label_type", "appeals")
foreach ($prop in $entries) {
    $e = $prop.Value
    $shortId = $e.content_id.Substring(0, 8)
    Write-Host ("  {0,-10} {1,-8} {2,-6} {3,-22} {4,-6}" -f $shortId, $e.attribution, $e.confidence, $e.label.label_type, $e.appeals.Count)
}
Write-Host ""

$logPath = Join-Path $PSScriptRoot "audit_log_full.json"
$log | ConvertTo-Json -Depth 6 | Out-File -FilePath $logPath -Encoding utf8
Write-Host "  Full JSON written to: $logPath"
