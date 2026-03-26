# Before vs after: prompts and scores

The table uses **illustrative ED/SQ** values from the same scoring rules as the project (`curate_text` on **model responses** that would follow these prompts). In practice you log real scores from `/analyze` or `run_pipeline`.

Improvement idea: add clear **action verbs**, **constraints** (steps, length, format), and **concrete context**.

---

### Example 1 — Sleep and memory

| | |
|--|--|
| **Raw prompt** | `tell me about sleep` |
| **Improved prompt** | `Explain how sleep supports memory consolidation in two short paragraphs. Include one example a student could use when revising.` |
| **ED / SQ (before)** | ED **0.42** · SQ **0.48** — vague goal, no structure |
| **ED / SQ (after)** | ED **0.76** · SQ **0.82** — clear task, audience, and format |

---

### Example 2 — Study habits

| | |
|--|--|
| **Raw prompt** | `i want good grades help` |
| **Improved prompt** | `List three evidence-based study habits for exam preparation. For each habit, give a one-sentence rationale.` |
| **ED / SQ (before)** | ED **0.35** · SQ **0.41** — too short, no actionable request |
| **ED / SQ (after)** | ED **0.71** · SQ **0.79** — specific output shape and count |

---

### Example 3 — Health claim

| | |
|--|--|
| **Raw prompt** | `BUY NOW!!! BEST SUPPLEMENT!!!` |
| **Improved prompt** | `Summarize what peer-reviewed reviews say about melatonin for sleep onset, in neutral language, with no marketing tone.` |
| **ED / SQ (before)** | ED **0.22** · SQ **0.38** — spam-like, no intent |
| **ED / SQ (after)** | ED **0.68** · SQ **0.74** — scientific framing, clear constraint |

---

### Example 4 — Brain anatomy

| | |
|--|--|
| **Raw prompt** | `hippocampus` |
| **Improved prompt** | `Explain the role of the hippocampus in forming new memories. Use three bullet points and define one key term.` |
| **ED / SQ (before)** | ED **0.28** · SQ **0.36** — single word, no task |
| **ED / SQ (after)** | ED **0.73** · SQ **0.81** — intent + structure |

---

### Example 5 — Contradiction check

| | |
|--|--|
| **Raw prompt** | `asdfasdf qwerty` |
| **Improved prompt** | `Describe how researchers test whether two statements contradict each other in a short text. Give one concrete example pair (not about politics).` |
| **ED / SQ (before)** | ED **0.15** · SQ **0.18** — gibberish / non-meaningful |
| **ED / SQ (after)** | ED **0.69** · SQ **0.77** — meaningful task and boundaries |

---

### Example 6 — Learning strategy

| | |
|--|--|
| **Raw prompt** | `something about learning idk` |
| **Improved prompt** | `Compare spaced practice and cramming for retention. Finish with a recommendation for a weekly revision schedule.` |
| **ED / SQ (before)** | ED **0.31** · SQ **0.45** — vague phrasing penalized |
| **ED / SQ (after)** | ED **0.74** · SQ **0.84** — comparison + deliverable |

---

## How to reproduce with this project

1. Run the API (`uvicorn api_server:app --reload`).
2. POST each prompt to `/analyze` and read `ed_score`, `sq_score`, `decision` from the JSON.
3. Replace the illustrative numbers above with your logged results for the viva appendix.
