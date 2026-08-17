# Stages

The full algorithm for each of the five primitives in
`slimtoken.prompt_reframe`, with worked before/after examples drawn
from real-world rambling.

---

## 1. `classify_domain(prompt)` → str

Match keywords from a fixed taxonomy into one of six domains:

| Domain         | Example trigger words |
|----------------|------------------------|
| business       | revenue, forecast, KPI, market, budget, plan |
| professional   | report, audit, compliance, policy, study |
| osint          | investigate, search, find, enumerate, monitor |
| cybersecurity  | malware, IOC, breach, phishing, ransomware |
| code           | refactor, debug, branch, patch, test, lint |
| general        | *(fallback)* |

The match is a sum of substring hits per domain. The highest-scoring
domain wins. Ties resolve by the order the domains are defined. Empty
input → `"general"`.

```python
>>> classify_domain("quarterly revenue forecast vs plan")
'business'
>>> classify_domain("find all the indicators of compromise for this breach")
'cybersecurity'
>>> classify_domain("refactor the merge function to use a stable branch")
'code'
```

---

## 2. `reframe_prompt(prompt)` → str

Five-pass transform, all on the regex layer:

1. Drop 30+ filler phrases (`"can you basically just tell me…"`,
   `"in order to"`, `"due to the fact that"`, …) by case-insensitive
   word-boundary match.
2. Drop fragment patterns: runs of `.` (3+), double words (`"the the"`),
   spaces before punctuation, etc.
3. Split on sentence-ending punctuation + newlines.
4. Dedupe sentences by normalized lowercase alphanumeric key (every
   claim survives; only true duplicates collapse).
5. Normalize whitespace; capitalize the first letter; ensure the result
   ends in `.`, `!`, or `?`.

```text
BEFORE:
"Can you basically just tell me what is the answer really kind of
like basically please help me with this. I need you to fix the bug
in the auth flow. I need you to fix the bug in the auth flow. Just
do it."

AFTER:
"I need to fix the bug in the auth flow."
```

The reframe is intentionally *boring* — no LLM, no drift, no loss of
intent beyond the filler it explicitly strips. Sentences that look
"redundant" by eye but say different things are kept separately.

---

## 3. `shrink_prompt(prompt, mode='balanced')` → str

TextRank-lite sentence rank:

1. Run `reframe_prompt` first (cheap; frequently already short enough).
2. For each sentence, score = `2 × (overlap with the prompt's content
   words) + word_count`.
3. Sort descending by score; tie-break by original position.
4. Greedily pack top-scored sentences into the word budget
   (`aggressive` = 20, `balanced` = 50, `preserve` = 150).
5. Re-emit selected sentences in original document order so prose
   flows.

The output is *built from sentences that already appear in the input*,
in the user's own words. There's no semantic rewriter and no chance to
hallucinate. If ranking yields nothing, the highest-scoring sentence
is returned alone so the function never returns `""`.

```text
BEFORE (~110 words, business blurb)
"Our Q3 revenue came in at $4.2M, which was about 12% above the plan.
CFO wanted us to flag this because finance had a wider variance in Q2
and the broader forecast band is now 8% above the original plan.
Customer count grew 14%. Net retention was 104%. Expansion was driven
by two deals in the EMEA region. One churn risk to watch is Atlas
Corp whose usage dropped 31% month-over-month. The next thing for the
team to decide is whether to lock the plan or reforecast."

AFTER (balanced, ~50 words)
"Our Q3 revenue came in at $4.2M, about 12% above the plan, with the
broader forecast band now 8% above the original plan. The team needs
to decide whether to lock the plan or reforecast."
```

If you'd like the model to **paraphrase** (not just pick sentences),
you need a real semantic rewriter; this stage cannot do that. Pair it
with your favorite LLM for paraphrase if you want both.

---

## 4. `minify_prompt(prompt)` → str

Character-level squeeze:

1. Collapse any whitespace run to a single space.
2. Drop runs of `,`, `;`, `:`, `!`, `?` of length 2 or more.

Cosmetic only. No semantic change. Pair it with `shrink_prompt` for a
two-step size reduction (semantic + cosmetic).

```text
BEFORE:
"I need  to fix   ,,, , the bug!!!!"
AFTER:
"I need to fix, the bug!"
```

---

## 5. `build_system(domain, role, style, rules)` → str

Compose a tight, declarative system prompt from a small fixed schema:

- `role` — short role label (e.g. `"generalist"`, `"planner"`,
  `"auditor"`).
- `style` — short style label (e.g. `"terse"`, `"numbered"`,
  `"evidence"`).
- `domain` — picks a one-line domain hint (business → "Metrics →
  trends → recommendation → risk.", etc.).
- `rules` — optional tuple of explicit rules; first 6 are included.

Output is a single short line. Pair with whatever model you're using
instead of a multi-paragraph system prompt.

```python
>>> build_system("business", role="planner", style="numbered",
...              rules=("Lead with the answer.", "Cite the source."))
'Role: planner. Style: numbered. Domain (business): Structured.
Metrics → trends → recommendation → risk. No hype. Rules: Lead with
the answer.; Cite the source. Output: lead with the answer or action.
No filler. Format: tables / lists when they shorten. No code blocks
unless asked. No thinking preamble.'
```

---

## End-to-end (`frame_prompt`)

`frame_prompt` runs all five stages in this order:

```
classify_domain → reframe_prompt → shrink_prompt → minify_prompt
                                                    → build_system
```

Returns `(tight_prompt, system_prompt, domain)`. The system prompt is
either the composed one (if you didn't pass your own) or your original
plus the composed one appended — so any standing instructions stay in
force.
