# Proposal: from bounded models to a checked core

> **Status: proposal, for review.** Nothing here changes the server. It
> proposes how the formal work continues after the first two passes: what to
> build first, where it pays off, and which decisions are the maintainer's.
> Each step is small and stands on its own; a step that does not pay off can
> stop the line without leaving half-built work behind. Open questions are
> numbered (Q1–Q7) so a review can answer them by number.

## 1. Where the formal work stands

The two passes were an experiment, and it worked: 38 findings, 35 of them
fixed, each with a regression test. Writing a
model also paid off on its own. It forced exact questions ("which threads touch
this dict?"), and those found F4 and F10 without any search.

What the experiment did not use, read from `formal/` at `a6240b8`:

- **No proofs.** None of the 14 model files contains a `theorem`. Each is an
  executable program (`lean --run`) that searches every state within stated
  bounds. That is model checking, the job TLA+ or Alloy usually do, with Lean
  as the programming language. Every "holds" in FINDINGS.md means "holds
  within the bounds", as the README's honesty notes say.
- **No shared code.** Each file carries its own search loop (14 copies of
  `bfs`, `explore` or `trajectory`) and its own report printer. There is no
  Lake package, so nothing can be imported between models.
- **No link to the code.** A model cites the lines it mirrors, but nothing
  notices when those lines change. `run_all.sh` prints results and never
  fails, and no CI job runs the models or the 28 reproduction scripts.
- **No shared statement of what the system promises.** Each model states its
  own properties. Nothing checks that two models assume the same thing about
  each other.

Those four gaps are the foundations this proposal lays, in that order of
cost: shared code and CI first, then the link to the code, then proofs and a
spec where they earn their place.

Prior art worth reading: [lean-zip](https://github.com/kim-em/lean-zip)
ships the code its theorems are about (a verified DEFLATE whose round-trip
proof gates every optimisation), and [leanin](https://github.com/scidonia/leanin)
writes a concurrency runtime in Lean and proves its model against stated
specifications of the native operations.

## 2. Where the value is

The project's history says where defects come from and where change keeps
coming.

1. **Scoring, budgets and the compaction tick.** Retuned in almost every
   minor release (0.13.0: fresh tier, rebalance; 0.14.0: budgets, recency;
   0.15.0: refill in the same tick). The tick produced F7, F33 and F34, and
   F33 is still open because its fix is a choice of policy. The ARCHITECTURE
   "Planned Features" list includes custom archival and scoring algorithms;
   a pluggable scorer is only safe if the tick's guarantees hold for any
   scorer. **Highest value:** properties proved here protect every future
   retune, not only today's numbers.
2. **Delivery and cross-session awareness.** Every new caller adds a delivery
   path: Codex (0.10), Antigravity's hook queue (0.11), paged reads (0.14),
   REST-registered sessions (0.16), and Cursor and team memory are on the
   planned list. F13–F16 and F38 broke one rule (a reply's effects land only
   when the reply is delivered), F35–F37 another (each change is announced
   once, and only when it is a write). **High value:** those rules, written
   once as a contract, are what each new adapter has to meet.
3. **Lifecycle, dispatch, credits, persistence.** Most findings here are
   fixed, and none of the planned features above depends on new guarantees
   here. **Moderate value:** keep the models
   running in CI so a later change that breaks them is noticed; no proofs.
4. **I/O glue: MCP plumbing, REST routing, hooks, installers.** **Low
   value:** formalising them costs much and catches little. Out of scope.

## 3. The steps

Each step lists what it builds, what it proves or catches, and the release
rule it falls under (RELEASING.md). Steps 1–3 change no server behaviour (step 3
adds tests only) and fit any patch release ("tests and CI").

### Step 1 — A Lake package and one shared search

- `formal/lakefile.lean` and a small library (working name `KgFormal`) holding
  the search loop, the trace printer and the report format that the 14 models
  now copy.
- Move models onto it **one at a time**. A move is done when the model prints
  the same results as its committed `evidence/*.log`. A model that resists is
  left as it is; nothing forces a big-bang migration.
- Shared domain types (levels, node ids, sessions, versions) are extracted
  only when two models need the same one. No upfront ontology.

*Catches:* nothing new. It is the base every later step builds on, and the
point where the toolchain is chosen (Q6).

### Step 2 — Models in CI with expected outcomes

- Each model declares what it expects per property and variant: `holds`, or
  `counterexample` while a finding is open (F33, F38).
- A `formal` CI job runs the fast models and fails on any change of outcome.
  The compaction search (most of `run_all.sh`'s ~30 minutes) runs in a
  reduced scope in CI and in full by hand.
- This closes the second item of the README's "Open work".

*Catches:* a model that no longer builds, or a fix in a model that silently
changed an outcome. It does not yet catch code drift; that is step 3.

### Step 3 — Model traces as Python fixtures (the drift guard)

The model should check the code, not only the reverse. The cheapest way:

- A model writes its trajectories (initial state, steps, final state) as JSON
  fixtures under `knowledge-graph/server/tests/fixtures/`.
- A Python test replays each fixture against the real code and asserts the
  same states. `compaction/repro/repro_tick.py` already does this by hand for
  the counterexamples; this makes it systematic and covers the passing
  traces too.
- The fixtures are committed, so the ordinary test job needs no Lean. When the
  Python changes behaviour, the test fails, and the fix is either the code or
  the model, regenerated in the same PR.

Start with the compaction tick: it is deterministic, so a trace is just a
sequence of states. Concurrency models follow only where a reproduction
already drives the real code through a stubbed interleaving (writes,
sessions, delivery).

*Catches:* drift in both directions, on every PR, at the cost of a normal
test.

### Step 4 — The first proof: F33, and whether the tick settles

F33 is the right pilot. It is small and already modelled, its decision is
still open, and FINDINGS.md lays out three options. Proofs settle exactly the
question the options leave open:

- **Option 2** (a swap is kept only if the summed active score rises by the
  margin) terminates on any graph. That argument does not depend on the score
  values. It holds for **any** score function, which is the guarantee a
  pluggable scorer needs. A Lean theorem can state it once, over an abstract
  score function, with no floating point involved.
- **Option 1** (judge the swap after it) has no counterexample within the
  bounds but no termination argument. Either a proof appears or the search
  widens until it finds a cycle.
- **The whole tick, not only rebalance.** The option-2 argument covers
  rebalance alone. Compaction and refill run in the same tick and can lower
  the measure. "An idle graph settles" is the property users notice (no file
  rewritten every 30 seconds), and it is the theorem worth having. If it
  fails, the failing trace is the next finding.

Lean's `Float` is opaque to proofs. The theorems therefore work over an
abstract ordered score; the executable model keeps IEEE doubles for its
comparison with Python (step 3).

The F33 fix itself is a scoring change, so it ships in a minor release. Before
release it is replayed with `python -m eval` on copies of real graphs, as
recent scoring changes were. The two tools answer different questions. The
proof says the tick cannot misbehave; the replay says whether it ranks well.

*Decision point after step 4:* was the proof affordable? If proving one
property of a 400-line Python module took more than a few focused sessions,
steps 5 and 6 shrink to prose and model checking.

### Step 5 — A spec: what the system promises

One short, numbered list of promises, prose first. Each promise names the
models and tests that check it. A sketch of the first entries:

- **P1 Budgets.** After a tick, a level renders within its budget unless
  every remaining active node is in the fresh tier.
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

Lean formalisation follows the prose only where a proof is planned (P1 and P2
after step 4). The prose alone already does architecture work. A new adapter
(Cursor), a pluggable scorer or team memory starts by saying which promises
it keeps, and a design that cannot keep one is visible before any code. F38
is a live example: it is a P3/P5 conflict on the hook path, and its options
are choices of which promise to weaken.

### Step 6 — A checked core, and optimisation behind it (conditional)

This is lean-zip's pattern at this project's scale, and it starts only if
steps 3–4 showed value.

- **Reference and fast implementation.** A pure function gets a reference
  definition in Lean, with its properties proved, and the Python version is
  checked against it by the step 3 fixtures. An optimisation then has a
  ratchet: it may change the code freely, as long as the fixtures and proofs
  still pass. FINDINGS.md already lists candidates, and the ones a
  reference would protect are pure:
  - `file_recall._rank` scores the whole graph under the lock on each tool
    event with matches; an incremental ranker must equal the full one.
  - "Render equals charge" (ARCHITECTURE: the estimator measures the exact
    strings `kg_read` renders) is a theorem candidate, `estimate = len(render)`;
    refill's off-by-one residual in FINDINGS.md lives here.
- **Concurrency optimisations are modelled first.** Moving blocking store
  calls (lock plus `fsync`) off the event loop is the largest performance
  candidate, and it also shortens the window behind F38. It is a change to
  locking, so it is modelled before it is built, as the F6 subscriptions
  were.
- **Shipping Lean in the product** (a compiled core called from Python, or a
  Lean server) stays out of scope here. It changes packaging from one Python
  wheel to per-platform binaries. It is reconsidered only if a reference
  implementation turns out faster than the Python it checks (Q4).

## 4. Lean at the level of decisions

Lean does not pick the best design. It checks claims about candidates, and
that turns several kinds of architectural question into ones with firm
answers:

- **Candidates as variants.** Write each option as a variant of one model,
  state the required properties, and let the search or a proof eliminate
  options. The compaction model's `code`/`post`/`pot` variants already do
  this informally. What remains after elimination is a judgement, made with
  replay numbers, not a guess.
- **Impossibility results.** A proof that no policy of a given shape can keep
  two promises at once ends a search for a design that cannot exist. F38's
  options are a candidate: plausibly no hook-side rule without an
  acknowledgement keeps both P3 and P5 under a client timeout (inference, to
  be checked).
- **Optimal only relative to a stated objective.** A proof can show that a
  policy minimises writes per tick, or never leaves fillable headroom, under
  stated assumptions. Choosing the objective, and judging simplicity, stays
  with the maintainer.

The proposed habit: a design decision that touches a promise gets a short
model before code, as F6 did. That model records the decision: the options,
the property each one breaks, and the choice.

## 5. Not proposed

- Rewriting the server in Lean.
- Proofs about I/O, hooks, MCP or REST plumbing.
- Proofs about floating-point arithmetic.
- Turning every existing model into proofs. Bounded search stays the tool for
  interleavings; proofs are for the deterministic core.

## 6. Costs and risks

- **Proof effort is unknown here** until step 4 measures it, which is why
  step 4 ends in a decision point.
- **Toolchain churn.** Lean releases roughly monthly. Pinning in
  `lean-toolchain` contains it, but every bump has to rebuild every model.
- **CI time.** A Lean toolchain install plus the fast models adds a job.
  Fixtures (step 3) keep the main test job free of Lean.
- **Two sources of truth** during step 6, until the fixtures tie them
  together. Steps 3 and 6 must land together for any module they cover.

## 7. Open questions

- **Q1** Which planned releases touch scoring, budgets or compaction next?
  The F33 work (step 4) should land before or with that release, not after.
- **Q2** Are pluggable scoring and team memory still planned? They decide
  whether P1/P2 must hold for any scorer (proof over an abstract score), and
  how much weight P4/P5 deserve.
- **Q3** Is Cursor, or Antigravity leaving experimental, next among the
  adapters? Either would be the first user of the delivery contract (P3).
- **Q4** Is shipping compiled Lean in the product ever on the table, or is
  Lean strictly a development tool here?
- **Q5** Should F38 be decided through step 5 (as a promise conflict), or
  separately and sooner?
- **Q6** Toolchain: models run on `v4.34.1`. Stay there, or move to a newer
  release for step 4's proofs? leanin's README notes that `Std.WP`, its
  weakest-precondition framework, first appears in 4.35.
- **Q7** Is the order right (1 → 2 → 3 → 4, then 5 and 6 as earned), and is
  step 3 or step 4 the one to start with if only one fits the next release?
