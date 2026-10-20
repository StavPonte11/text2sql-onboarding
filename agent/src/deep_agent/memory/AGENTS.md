# Deep SQL Analyst — Core Memory

Learned conventions for this deployment. The workflow, hard rules and value-resolution policy live in the
system prompt; deep references live in the skills. Keep this file to things learned from real runs.

## Patterns seen in past runs (verify with tools before relying on them)

- Filter values are often stored in **English** even when the question is Hebrew — e.g. "צרפת" was stored as
  `launch_country = 'France'`. Resolve the value; never translate it yourself and filter on the guess.
- Some city columns store composite "name + code" strings (`'Tel Aviv 1'`, `'Tel Aviv 57'`). `search_column_values`
  reports this as `prefix_pattern`; filter the city with `LIKE 'Tel Aviv%'`, not equality on the bare name.
- "How many flights" questions were answered correctly with `COUNT(DISTINCT flight_id)`; plain `COUNT(*)` over-counts
  as soon as a join or repeated rows are involved.

## Failure modes to avoid

- **Categorical filter mismatches are the #1 cause of wrong results.** The user's word is almost never the stored
  string. Resolve every text filter, then let `enrich_sql_filters` double-check the draft.
- **Zero rows almost never means "no data".** Investigate in order: string filter values → join key formats
  (sample both sides) → date range logic and column type → NULLs. Remove conditions one at a time to find the culprit.
- **JOIN fan-out silently corrupts aggregations.** A 1-to-many join multiplies rows; aggregate the child table in a CTE
  first or count distinct keys.
- **`domainIsComplete: true` is definitive.** The listed values are the entire value set; anything else does not exist.
- **Intent drift is a silent correctness killer.** Before answering, translate the SQL back to plain language and
  compare it filter-by-filter with the user's question — nothing missing, nothing added.
