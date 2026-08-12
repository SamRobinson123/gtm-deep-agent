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
