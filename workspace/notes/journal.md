# Journal — what past sessions learned

Persistent memory across sessions. Append-only by convention; read at the start
of a session, appended to before a substantial task ends.

Worth recording: findings, **dead ends** (a route already tried and abandoned is
worth as much as a result), decisions and who made them, and open questions.

Not worth recording: anything the code or the docs already say.

---

## 2026-08-11 — v2 migration

- The agent moved from fourteen bespoke tools to three plus a general tool set.
  Computation is now a scratch script, not a tool. See
  `docs/superpowers/specs/2026-08-11-gtm-deep-agent-v2-design.md` (a spec, NOT
  context — never cite it as fact about the data).
- `SYNAPSE_CONN_STR` is deliberately absent from the environment. A scratch
  script asking for it gets a KeyError. Warehouse access is the `query` tool.
- There are **no golden output numbers** for this model. Verification is
  conformance to the docs plus `pipeline/checks.py`. Label every figure with
  which layer backed it; an unverified number is allowed, an unlabelled one is not.
- `checks.weekly_sums_to_quarter` cannot assert equality mid-quarter: the
  allocator prorates to elapsed days, so Q3 FY26 legitimately allocates
  $91,130,931 of $201,789,918 at 45.2% elapsed. Invariant 4 working, not a bug.
- `pipeline/checks.py` targets the weekly published-target shape
  (`target_created` etc. from `pipe_create.py`/`targets_cli`); it does not apply
  to `waterfall_cli derive` output (Territory×Quarter rows with
  `pipe_create_target`, `binding`, ...). Ran the verifier subagent instead —
  route to it, not `checks.run_all`, for waterfall solves.

## 2026-08-12 — Q3/Q4 FY26 waterfall recompute, verified

- `waterfall_cli derive --quarters "Q3 FY26, Q4 FY26" --grain Territory`
  (run `2026-08-12T174639Z_4cbe8b`) gives DERIVED Q3 FY26 $519,979,827 and
  Q4 FY26 $544,043,410 — +157.7% and +183.0% vs published
  ($201,789,918 / $192,223,413). Verifier subagent independently recomputed
  the row-level closed-form solve from the run's own CSV (no shared code path)
  and returned VERDICT AGREE, 0.0000% delta on totals, floor-driven split, and
  binding labels. Floor-driven is small: $24,293,130 (2.3%, 7 rows); 45 rows
  are gap-driven.
- This is the same unexplained gap flagged as open question 9 in
  `docs/analysis/pipe-create-waterfall.md` — now measured precisely for both
  quarters rather than described qualitatively. All previously-ruled-out terms
  (yield, $0 sales-cycle tail, Pre Q slip/slip inflow roughly cancelling,
  untargeted-pipe exclusion) still apply; nothing new found here. Do not treat
  the size of the gap as a reason to adjust an assumption — per the doc, that's
  the owner's call, not something to close computationally.
- Scenario clamp (run `2026-08-12T203234Z_8b5867`, verified AGREE 0.0% delta):
  clamping SLED plus every territory with `expected_from_existing_pipe == 0.0`
  (AMS Corporate, AMS Sealights, EMEA Core BeNeLux, EMEA Core BeNeLux Nordics,
  EMEA Core Emerging, EMEA Core France, EMEA Core France Emerging, EMEA Core
  MEA South, EMEA Core Middle East Africa, EMEA Corporate — 11 rows total) to
  historic floor drops the Q3 FY26 total from $519,979,827 to $333,981,921
  (-$185,997,906). Still +65.5% over published ($201,789,918). This is a raw
  clamp, not a re-solve — the shortfall created by lowering these targets is
  NOT redistributed elsewhere; if the bookings target still needs to be hit,
  that gap has to land somewhere else in a real re-solve. Pattern for future
  clamp-type scenarios: `pipeline.derive.derive_frame`, overwrite
  `pipe_create_target` with `historic_floor` on the selected rows, record a Run,
  verify. Not expressible via `whatif` (single-assumption only).
