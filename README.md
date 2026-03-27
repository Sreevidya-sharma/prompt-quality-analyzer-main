# Prompt Quality Helper

Prompt Quality Helper is a production-ready Chrome Extension that evaluates prompt quality in real time and provides actionable feedback directly on supported AI chat platforms.  
It is powered by a deployed FastAPI backend and shared scoring pipeline, with analytics surfaced in a web dashboard.

## Problem Statement

Large language models respond to whatever users type. Vague, spam-like, or unstructured prompts lead to weak answers and poor learning outcomes. Teams need **consistent, explainable signals**—not a black box—so users can improve prompts before they send them.

## Solution Overview

This project ties together four pieces, with the extension as the primary user interface:

1. **Chrome extension** – Captures prompts on supported AI sites and shows real-time overlay feedback.
2. **FastAPI service (deployed)** – One **`run_pipeline`** path: model inference → curation (**ED** / **SQ**) → **M1** / **M2** when accepted → SQLite logging.
3. **SQLite storage** – Persists runs for analytics.
4. **Dashboard** – Filters, charts, and recent runs over stored data.

The same pipeline logic runs everywhere (API and scripts), so behavior stays consistent.

## Features

- Real-time prompt feedback overlay on supported AI chat interfaces.
- **Accept / Reject / Review** decision for each analyzed prompt.
- Clarity, Structure, and Actionability scoring for every prompt.
- Improved prompt suggestions to help users refine inputs before submitting.
- **Dashboard analytics** with live stats, filters, trend charts, and recent runs.
- Privacy-focused operation: runs only on supported AI sites.
- **Unified pipeline**: infer → `curate_text` (on model response) → M2 gate → metrics.
- **Tests**: `pytest` for scoring, evaluation, and API (`tests/`).

## Architecture

High-level data flow:

![Architecture](docs/architecture.png)

1. The **extension** captures prompt text on supported sites.  
2. It sends `{ "text": "..." }` to the deployed API: `https://prompt-quality-analyzer.onrender.com/analyze`.  
3. **FastAPI** runs **`run_pipeline`** to score and evaluate prompt quality.  
4. Results flow to **SQLite** and are surfaced in the **dashboard** (`/dashboard`).

More detail: [docs/metrics.md](docs/metrics.md), [docs/comparison.md](docs/comparison.md).

## Extension Details

- The **content script** detects and captures prompt input from supported AI chat pages.
- The **background service worker** sends analysis requests to the backend API.
- The **overlay UI** renders decision, scores, reason, and prompt-improvement suggestions in real time.

## Tech Stack

| Layer        | Technology                          |
|-------------|--------------------------------------|
| Extension   | Chrome Extension (Manifest V3), JavaScript (content + background scripts) |
| API         | FastAPI (deployed), Uvicorn          |
| Deployment  | Render                               |
| Pipeline    | Python 3, shared `pipeline.py`       |
| Scoring     | scikit-learn (TF–IDF), custom rules  |
| Storage     | SQLite (`storage.py`)                |
| Dashboard   | Static HTML + Chart.js               |
| Tests       | pytest, FastAPI `TestClient`        |
| Config      | PyYAML                               |

## Setup Instructions

### 1. Clone and environment

```bash
cd "Cognitive Health Pipeline"
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configuration

- Edit **`configs/base.yaml`** for keywords, thresholds, and `model` (e.g. `gpt-4o-mini`).
- For live model calls, set **`OPENAI_API_KEY`** in your environment.

### 3. Chrome extension

1. Open `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** → select the **`chrome-extension`** folder.
3. In the extension popup, confirm the **API base URL** is set to `https://prompt-quality-analyzer.onrender.com`.

## How to Run

### API + dashboard

```bash
source .venv/bin/activate
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

- API root: `https://prompt-quality-analyzer.onrender.com`
- Dashboard: `https://prompt-quality-analyzer.onrender.com/dashboard`
- Stats: `GET /stats`, recent runs: `GET /recent`

### Extension

Open a supported AI chat page, type a prompt, and pause briefly. The extension overlay will show the latest decision, scores, and suggestion.

### Tests

```bash
pytest
```

## Example Usage

**Analyze via HTTP:**

```bash
curl -s -X POST https://prompt-quality-analyzer.onrender.com/analyze \
  -H "Content-Type: application/json" \
  -d '{"text":"Explain how sleep helps memory in two steps with one example."}'
```

**Batch-style demo data:** see [data/demo_inputs.json](data/demo_inputs.json) and [data/demo_results.json](data/demo_results.json).

**Programmatic:**

```bash
python pipeline.py
```

Runs one sample through `run_pipeline` with `persist=False`.

## Screenshots

<!-- add screenshot here: extension UI — popup (API URL) and on-page overlay with decision, ED, SQ, reason, suggestion -->

<!-- add screenshot here: dashboard — header, filters, summary cards, trend charts, recent prompts list -->

## Future Improvements

- Per-domain extension UX presets; richer accessibility.
- Calibration studies for ED/SQ vs human ratings.
- Export dashboard reports (PDF/CSV).

## Project layout

```
docs/           # architecture diagram, metrics, before/after comparison
data/           # demo inputs and example results
tests/          # pytest suite
configs/        # YAML configuration
chrome-extension/
src/            # scoring & evaluation modules
```

## License / usage

Use and adapt with attribution according to your organization’s requirements.

## Privacy Policy

Privacy Policy for Prompt Quality Helper

Effective Date: March 27, 2026

This extension is designed to improve prompt quality on supported AI platforms while respecting user privacy.

Data Collection:
- Email address (used for user identification and dashboard access)
- User prompts (processed temporarily for analysis)

Data Usage:
- Prompts are sent securely to a backend API for analysis
- Email is used to associate activity with a dashboard
- No prompt data is permanently stored

Data Storage:
- Email may be stored securely
- Prompt data is NOT stored locally or permanently

Data Sharing:
- No user data is sold or shared with third parties

Security:
- All communication is over HTTPS
- Backend APIs are secured

User Control:
- Users can stop using the extension anytime
- No tracking outside supported sites

Contact:
vidyasreethotapalli@gmail.com
