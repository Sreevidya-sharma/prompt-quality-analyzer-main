# Scoring formulas (aligned with `src/scoring.py` and `configs/base.yaml`)

All numeric weights and thresholds are loaded from `configs/base.yaml`. Symbols below use the names used in code.

---

## Engagement Degree (ED)

Let \(N\) = token count, \(S\) = sentence count, \(h\) = count of tokens that appear in any configured keyword phrase, \(d = h / \max(N,1)\).

**Keyword band score \(f_{kw}\)** (bounds `ed.kw_density_min` … `ed.kw_density_max`, scale `ed.kw_penalty_divisor`):

- If \(d_{min} \le d \le d_{max}\): \(f_{kw} = 1\).
- Else: \(f_{kw} = \max(0,\ 1 - \min(|d-d_{min}|, |d-d_{max}|) / d_{div})\).

**Length factor \(f_{len}\)** (targets `ed.token_len_ideal_min` … `ed.token_len_ideal_max`):

- If inside band: \(1\).
- If \(N < N_{min}\): \(N / \text{token\_len\_under\_scale}\).
- If \(N > N_{max}\): \(\max(0,\ 1 - (N-N_{max}) / \text{token\_len\_over\_scale})\).

**Repetition factor \(f_{rep}\)**: unique tokens \(/ N\) (or \(0\) if \(N=0\)).

**Sentence factor \(f_{sent}\)** (targets `ed.sent_ideal_min` … `ed.sent_ideal_max`):

- If inside band: \(1\).
- If \(S < S_{min}\): \(S / \text{sent\_under\_scale}\).
- If \(S > S_{max}\): \(\max(0,\ 1 - (S-S_{max}) / \text{sent\_over\_scale})\).

**Spam penalties** (added, then clamped to \([0,1]\)):

- `+ spam_penalty_exclaim` if `"!!!"` in text or all-uppercase (length \(>3\)).
- `+ spam_penalty_repeat_ratio` if unique/total \(< 0.5\).
- `+ spam_heavy_penalty` if unique/total \(<\) `spam_heavy_unique_ratio`.

**ED aggregate** (weights `ed.weight_len`, `ed.weight_rep`, `ed.weight_kw`, `ed.weight_sent`):

\[
ED = \mathrm{clamp01}\bigl(
  w_{len}\,\mathrm{clamp01}(f_{len})
  + w_{rep}\,\mathrm{clamp01}(f_{rep})
  + w_{kw}\,f_{kw}
  + w_{sent}\,\mathrm{clamp01}(f_{sent})
  - \text{spam\_penalty}
\bigr)
\]

**Pass / threshold**: `thresholds.ed_threshold` — `passes` iff \(ED \ge T_{ed}\).

---

## Semantic Quality (SQ)

Let `info_density` = unique tokens \(/\) total tokens. Penalties `sq.low_info_penalty_density` if density \(<\) `sq.low_info_density_threshold`, plus `sq.low_info_penalty_short` if total tokens \(<\) `sq.min_tokens_low_info`.

**Relevance** \(f_{rel}\): cosine similarity of TF–IDF vectors of `prompt` and `text` (via a single expanded corpus row as implemented).

**Readability** \(f_{read}\) from mean tokens per sentence and mean characters per word, using `sq.read_sent_weight` / `sq.read_word_weight` and the length targets `sq.avg_sent_len_*`, `sq.avg_word_len_*`.

**Coherence** \(f_{coh}\): for two or more sentences, TF–IDF cosine between consecutive sentences; \(c_{raw} = \max(\text{coherence\_floor},\ \text{mean of consecutive sims})\); \(f_{coh} = \mathrm{clamp01}(c_{raw})\). For one sentence, \(c_{raw}=f_{red}=1\) in the branch that skips multi-sentence matrices.

**Redundancy** \(f_{red}\): for two or more sentences, \(1 - \text{mean of all pairwise sentence similarities}\); for one sentence, \(1\).

**SQ aggregate** (weights `sq.weight_rel`, `sq.weight_coh`, `sq.weight_read`, `sq.weight_red`, `sq.weight_info_density`):

\[
SQ = \mathrm{clamp01}\bigl(
  w_{rel}\,\mathrm{clamp01}(f_{rel})
  + w_{coh}\,f_{coh}
  + w_{read}\,\mathrm{clamp01}(f_{read})
  + w_{red}\,\mathrm{clamp01}(f_{red})
  + w_{id}\,\text{info\_density}
  - \text{low\_info\_penalty}
\bigr)
\]

**Pass / threshold**: `thresholds.sq_threshold` — `passes` iff \(SQ \ge T_{sq}\).

---

## Curation (`curate_text`)

After ED/SQ, SQ is adjusted: `+ curate.intent_boost` / `+ curate.constraint_boost` when intent/constraint heuristics match; `- curate.vague_penalty` for vague phrases without constraints; then clamped to \([0,1]\).

**Decision rules** (non-exhaustive order as in code):

- If tokens \(<\) `curate.min_words` → reject, reason `curate.reason_too_short`.
- Gibberish heuristic → reject, `curate.reason_gibberish`.
- If \(SQ \ge\) `curate.accept_sq_gate` and intent and constraint → accept.
- Else branches for missing intent, missing constraint, dual-low ED/SQ (`curate.dual_low_ed`, `curate.dual_low_sq`), else review.

---

## M1 (reasoning) — `src/evaluation.py`

Let \(S_c\) = sentence count, \(R_h\) = count of distinct tokens in `REASONING_WORDS`, \(Z\) = count of `STEP_MARKERS` present as whole words.

**Sentence score** (`evaluation.m1.*`):

- If \(S_c = 1\): `single_sentence_score`.
- If \(2 \le S_c \le 5\): \(1\).
- Else: \(\max(0,\ 1 - |S_c - \text{ideal\_sentences}| / \text{sentence\_decay\_divisor})\).

**Reasoning score**: \(\min(1,\ (R_h + Z) / \text{reasoning\_norm})\).

**Structure score**: start at \(0\); add `structure_two_sent` if \(S_c \ge 2\); add `structure_three_sent` if \(S_c \ge 3\); add `structure_step_weight` \(\cdot \min(1,\ Z / \text{structure\_step\_divisor})\); then \(\mathrm{clamp01}\).

**Blend**: `weights.sentence` \(\cdot\) sentence + `weights.reasoning` \(\cdot\) reasoning + `weights.structure` \(\cdot\) structure; if multi-sentence thought-skipping detected, multiply by `skip_penalty_factor`. Final output is \(\mathrm{clamp01}\).

---

## M2 (consistency) — `src/evaluation.py`

Repetition stability from sentence overlap (`evaluation.m2.overlap_norm`, Jaccard on content tokens, duplicate penalty). Contradiction: sentence-internal always/never; pairwise sentences with shared content and mismatched negation parity (`evaluation.m2.negation_words`, `pair_min_shared_tokens`, `pair_jaccard_min`). If any contradiction hit, return `evaluation.m2.contradiction_floor`. Otherwise:

\[
M2 = w_{rep}\cdot \text{repetition\_stability} + w_{con}\cdot 1
\]

with weights `evaluation.m2.weights.repetition` and `evaluation.m2.weights.contradiction`.
