# The formal work after the first two passes

> **Status: plan, decided in review of PR #69.** It says what the formal work
> does next, in what order, and what it leaves out. Each step is small and
> stands on its own. Each ends in something usable or in a recorded decision
> to stop, never in half-built infrastructure.

## 1. Where it stands

The two passes were an experiment, and it worked: 38 findings, 35 of them
fixed, each with a regression test. Writing a model also paid off on its own.
It forced exact questions ("which threads touch this dict?"), and those
found F4 and F10 without any search.

What the experiment did not use, read from `formal/` at `a6240b8`:

- **No proofs.** None of the 14 model files contains a `theorem`. Each is an
  executable program (`lean --run`) that searches every state within stated
  bounds. That is model checking with Lean as the programming language, and
  every "holds" in FINDINGS.md means "holds within the bounds".
- **No shared code.** Each file carries its own search loop (14 copies of
  `bfs`, `explore` or `trajectory`). There is no Lake package.
- **Nothing in CI.** `run_all.sh` prints results and never fails. No CI job
  runs the models or the 28 reproduction scripts, so nothing notices when
  the code moves away from a model.
- **No shared statement of what the system promises.** Each model states its
  own properties.

Since then, two open 0.17 PRs change the compaction tick:

- **Locked nodes (#66).** Compaction never archives a locked node, and
  rebalance leaves locked nodes out of its active list. `restore_locked`
  brings back a locked node found archived or orphaned. A graph whose locked
  share is over budget stays over budget by design.
- **F33, option 1 (#67).** Rebalance re-scores a tentative swap and keeps it
  only if the promoted node still wins by the margin. `tick_search.py store`
  over 20,000 graphs per generator: cycling graphs went from 1,015 to 1 (v1)
  and from 1,043 to 0 (v2). The residual, v1 seed 11314, is two interleaved
  swaps that each pass alone, with period 4. It is a counterexample to
  option 1 settling. Whether to move to option 2 (a summed-score potential
  that must rise) is open, and step 5 below is its input.

## 2. Where the value is

1. **Scoring, budgets and the compaction tick.** Retuned in nearly every
   minor release. 0.17 adds locks and the F33 fix. The retention work after
   it replaces the 365-day orphan deletion with a budget-based one and adds
   an activity clock, so both the orphan pass and scoring change again. Tick
   properties are stated over an abstract score for that reason. No
   pluggable scorer is planned; the point is that a property that holds for
   any score survives the next retune.
2. **Delivery, seen-state and stale views.** The next callers are
   programmatic:
   - a compose endpoint that returns a preload without registering an
     interactive session or adding the "call kg_read first" header;
   - project-only sessions;
   - a product-owned headless runner;
   - conversation threads, which carry work state and letters;
   - an experimental background recall partner that pushes picks through
     the seen-ledger.

   They are the first users of the delivery promises (P3, P5). Team memory
   is planned as a separate instance with its own storage root, using locked
   nodes and extension fields, with sync between instances later. That
   weighs on durability (P4) and stale views (P7). Antigravity leaving
   experimental is a loose end; Cursor comes later.
3. **Lifecycle, dispatch, credits, persistence.** Most findings here are
   fixed. Their reproductions run in CI (step 3) so a later change that
   breaks them is noticed. No proofs.
4. **I/O glue: MCP plumbing, REST routing, hooks, installers.** Out of
   scope.

## 3. The plan, in order

### Step 1 — Check the merged 0.17 tick

#66 and #67 both edit `Compactor.rebalance`: #66 the active list, #67 the
swap loop. Nobody has searched the merged tick. This goes before the 0.17
release PR, on its own branch.

- Merge both branches. Rebalance keeps both changes: locked nodes are left
  out of `active`, and each swap is judged after it is made.
- Add a v3 generator to `tick_search.py` with a locked share (none, some,
  over budget). Run `store v1|v2|v3` on the merge through `KG_SERVER=`.
  Check C1, C2 and B1–B5. B1, B2 and B4 take the locked exception
  deliberately: an over-budget locked share is expected, not a breach.
- Add a `locked` flag to `Compaction.lean` and re-run its variants.
- Report as a PR against the merge, or as a comment on #66 and #67 if
  nothing breaks. A real finding gets an F-number and a repro, as usual.

### Step 2 — The promises, prose only

One page, in its own file. Each promise names the model, repro or test that
checks it, and one line names the planned callers or features from section 2
that touch it. The list to start from:

- **P1 Budgets.** After a tick, a level renders within its budget unless
  every remaining active node is locked or in the fresh tier.
- **P2 Settling.** An idle graph reaches a state that further ticks leave
  unchanged.
- **P3 Effects on delivery.** A reply's effects (seen, read, promoted,
  pushed) are recorded only for what a delivered part showed.
- **P4 No silent loss.** An acknowledged write is durable, or the writer is
  told it was not.
- **P5 Each change announced once.** Another session's write reaches a
  session at most once through the push, and `kg_sync` lists anything the
  push dropped.
- **P6 Maintenance is not use.** A maintenance session's reads and writes
  never count as use.
- **P7 Stale views.** A write built on a stale or partial view never drops
  content the writer did not see (F11). Sync between instances will rely on
  this.
- **P8 Locks.** A locked node is never archived, orphaned or deleted, and
  never changed through MCP.

**F38** is decided together with threads, since both live on the hook path.
For that decision the promise file states, for each of F38's options (a)–(e),
which of P3 and P5 it weakens. The claim that no hook-side rule without an
acknowledgement keeps both under a client timeout is checked by a bounded
enumeration of hook-side policies only if that is cheap. Otherwise it stays
prose, marked as inference.

### Step 3 — CI: the Python reproductions first

The reproductions drive the real code, so they are the actual drift guard,
and most are fast and deterministic.

- A `formal` workflow runs the reproductions on PRs that touch
  `knowledge-graph/server/**`.
- The Lean models run only on changes under `formal/**` and on manual
  dispatch.
- Each reproduction and model gets an expected-outcomes file. The job is
  report-only until it has been green twice.

This closes the second item of the README's "Open work".

### Step 4 — Tick properties as a seeded pytest

No exact-state traces of the tick are committed: every legitimate retune
would break them, so they would flag changes rather than bugs. Instead:

- `tick_search.py`'s checks (C1, C2, B1–B5) become a seeded, bounded pytest:
  a few hundred graphs, seconds to run, asserting properties, not states.
- "Render equals charge" (the estimator measures the exact strings
  `kg_read` renders) gets a Python property test of the estimator against
  `render`. No Lean.
- Trace fixtures are kept only for discrete concurrency reproductions that
  are not already regression tests. `test_races_and_guards.py` and others
  already cover many.

### Step 5 — The F33 proof pilot, time-boxed to about two sessions

1. Option 2 terminates over an abstract ordered score: a swap is kept only
   if a bounded measure rises by the margin, so no sequence of swaps can
   cycle.
2. The whole idle tick settles under option 2, sequenced as in
   `_maybe_compact` (compact, refill, rebalance, orphan) and with locks.
   Compaction and refill can lower the measure, so this is the property
   that matters and the one that may fail. A failure is a trace, and the
   trace is a finding.

The theorems work over an abstract score, never Lean's `Float`, which proofs
cannot see into. The result goes to the F33-residual decision, together with
a `tick_search` run of option 2 on the real code.

**Decision point.** If the two sessions are not enough, the line stops at
model checking, and that is recorded here.

### Step 6 — A Lake package, only where step 5 needs one

Shared code is extracted when step 5 needs to import it. The 14 existing
models are not migrated for their own sake.

## 4. Models before designs, not ahead of them

A design that touches a promise gets a short model before its code, as the
editor subscriptions did (F6) and as locking changes already do. Models are
not written ahead of the designs. The next targets, each handed over when its
design exists:

- the compose endpoint's effects: does a header-less compose mark anything
  seen, and for whom?
- the thread scenarios;
- the sync API between instances.

What models can and cannot do for a decision: they eliminate options that
break a stated promise, and occasionally show that no option of a given shape
keeps two promises at once. Choosing among the options that remain stays a
judgement, informed by replays (`python -m eval`) where ranking is involved.

## 5. Decided against

- **Lean in the product.** Lean stays a development tool. The product ships
  as one Python wheel, and Windows and macOS are not fully supported yet. No
  reference implementations in Lean, no compiled core.
- **Rewriting or proving the I/O layer:** MCP, REST, hooks, installers.
- **Proofs about floating-point arithmetic.**
- **Exact-state trace fixtures for the tick** (see step 4).
- **Migrating every model to shared code** (see step 6).
- **Toolchain bumps without a cause.** The models stay on `v4.34.1`. Proofs
  over an abstract ordered score should not need `Std.WP`. The toolchain is
  bumped only when a concrete proof needs it, in a PR of its own.

## 6. Prior art

[lean-zip](https://github.com/kim-em/lean-zip) ships the code its theorems
are about: a verified DEFLATE whose round-trip proof gates every
optimisation. [leanin](https://github.com/scidonia/leanin) writes a
concurrency runtime in Lean and proves its model against stated
specifications of the native operations. This plan takes from them the
habit of stating a property once and letting it gate later change, not
their choice to ship Lean.
