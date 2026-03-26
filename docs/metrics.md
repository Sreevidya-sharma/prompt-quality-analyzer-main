# Metrics: ED, SQ, M1, M2

Short guide for presentations and viva. All scores are in **0–1** unless noted.

---

## ED — Engagement Degree

**What it measures:** How “healthy” the **text under review** looks as a prompt-like message: length in a reasonable band, not spam-heavy, sensible keyword use, sentence count, and similar engagement-style cues.

**High ED example:**  
*“Explain how sleep supports memory in **three bullet points**, with **one** study example.”* — clear length, structure words, not repetitive.

**Low ED example:**  
*`BUY NOW!!! limited offer!!!`* — excessive punctuation, repetition, shouty pattern → penalties.

**In code:** Produced inside **ED** logic in `src/scoring.py` (`compute_ed`), then folded into **`curate_text`**, which outputs `ed.score`.

---

## SQ — Semantic Quality

**What it meshes:** Relevance and coherence relative to the **user’s original prompt** (TF–IDF style overlap), readability-style sub-scores, and **curation rules** (intent words like “explain”, constraints like “steps”, vagueness penalties).

**High SQ example:**  
User asks for steps; response mentions “first / second / example” and stays on topic.

**Low SQ example:**  
User asks about sleep; answer drifts to unrelated products or stays too generic (“something / anything”) without structure.

**In code:** `compute_sq` + adjustments in **`curate_text`** → `sq.score`.

---

## M1 — Structure / reasoning

**What it measures (on the **model response** after a provisional accept):**  
Sentence count in a sensible range, presence of **reasoning** markers (e.g. *because*, *therefore*), and **step-like** structure (*first*, *second*, …).

**High M1 example:**  
*“Sleep helps consolidation **because** the brain replays patterns. **Therefore**, recall improves. **First**, deep sleep matters; **second**, REM supports integration.”*

**Low M1 example:**  
*`Yeah ok`* — one fragment, no reasoning chain.

**Edge — single sentence:**  
One short sentence gets a **capped** structure score (configured in YAML), so M1 stays modest.

**In code:** `compute_m1` in `src/evaluation.py`.

---

## M2 — Consistency

**What it measures:** Whether the **model response** contradicts itself across sentences, and whether repetition is “stable” vs noisy duplicate spam.

**High M2 example:**  
Several sentences on the same topic, **no** paired sentences that negate each other in a contradiction pattern you configured.

**Low M2 example:**  
*`The treatment works. The treatment does not work.`* — negation pattern across overlapping content → **contradiction floor** (low score).

**Repetition-heavy:**  
Copy-pasting the same sentence many times can **lower** M2 via duplicate penalties.

**In code:** `compute_m2` in `src/evaluation.py`. The pipeline can **reject** after accept if **M2 ≤ `m2_threshold`** in `configs/base.yaml`.

---

## How they fit together

1. **ED + SQ** (via **`curate_text`**) decide **accept / reject / review** on the **model response** (with the user prompt as reference for relevance).  
2. If decision is **accept**, **M1** and **M2** are computed on that response.  
3. If **M2** is below the threshold, decision becomes **reject** with reason about consistency.

For a one-page slide, use: **ED/SQ = gatekeeping**, **M1/M2 = deeper structure & consistency**.
