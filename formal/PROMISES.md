# What the memory server promises

Step 2 of the [plan](PROPOSAL.md). Each promise is a property of the server
that a user or caller can rely on, the checks that hold it, where it is known
not to hold yet, and the planned work that leans on it. A design that touches
a promise gets a short model before its code. Paths are under `formal/` and
`knowledge-graph/server/tests/`. "Bounded" means checked by exhaustive search
within the model's stated bounds, not proved.

**P1 Budgets.** After a tick, a level renders within its budget, unless every
remaining active node is locked or in the fresh tier.
Checked by: `compaction/lean/Compaction.lean` and `compaction/repro/tick_search.py`
(B1, B2, B4 and their exemptions; v3 with locks, #71); `test_compaction_tick.py`.
Touched by: the retention work (budget-based orphan deletion), locks.

**P2 Settling.** An idle graph reaches a state that further ticks leave
unchanged.
Checked by: C2 in the same model and search; `test_compaction_tick.py`
(`test_rebalance_settles`, #67).
Not yet: option 1 (#67) leaves one cycle in 20,000 random graphs (v1 seed
11314). The F33 proof pilot (plan step 5) decides between it and option 2.
Touched by: the retention work's activity clock, every scoring retune.

**P3 Effects on delivery.** A reply's effects (seen, read, promoted, pushed)
are recorded only for what a delivered part showed.
Checked by: `delivery/lean/Paging.lean`, `delivery/lean/Agy.lean`,
`delivery/repro/`; `test_paging.py`, `test_antigravity.py` (F13–F16).
Not yet: F38. A hook reply lost to the client's one-second timeout still moves
the push window and marks the change seen.
Touched by: the compose endpoint (does a header-less compose mark anything
seen, and for whom?), threads, the background recall partner, project-only
sessions.

**P4 No silent loss.** An acknowledged write is durable, or the writer is told
it was not.
Checked by: `store-persistence/lean/Persist.lean` and
`store-persistence/repro/repro_failed_save.py` (F2); `sessions/repro/repro_saver_death.py`
(F4); `test_races_and_guards.py`.
Limit, by policy: a forced reload lets disk win and logs what it drops (F3).
Touched by: team memory as its own instance, and sync between instances.

**P5 Each change announced once.** Another session's write reaches a session
at most once through the push, and `kg_sync` lists anything the push dropped.
Checked by: `cross-session/lean/Foreign.lean`, `cross-session/repro/repro_foreign.py`;
`test_foreign_writes.py` (F35–F37).
Touched by: threads, the background recall partner, the headless runner.

**P6 Maintenance is not use.** A maintenance session's reads and writes never
count as use, and its writes are not pushed to working sessions.
Checked by: `credits/lean/Credits.lean`, `credits/repro/repro_credits.py`
(F28–F30, F32); X-maint in `Foreign.lean`; `test_credit_accounting.py`,
`test_maintenance_credit.py`.
Touched by: the headless runner.

**P7 Stale views.** A write built on a stale or partial view never drops
content the writer did not see.
Checked by: `concurrent-writes/lean/Writes.lean`,
`concurrent-writes/repro/repro_lost_update.py`; `test_concurrent_writes.py`
(F11; F13 and F14 for paged and queued views).
Not yet: F38 lets a gist-only write built on an older view through. Edge
notes are replaced without the check (FINDINGS.md, F11).
Touched by: team memory and sync between instances.

**P8 Locks.** A locked node is never archived, orphaned or deleted, and never
changed through MCP.
Checked by: `test_fields_and_locks.py` (#66); L1 and L2 in `Compaction.lean`
and `tick_search.py` v3 (#71).
Touched by: team memory, the retention work's orphan deletion.

## F38 against the promises

F38's options (FINDINGS.md) differ in which promise they give up. Today's
behaviour is (e) without the documentation.

| Option | P3 | P5 | P7 |
|---|---|---|---|
| (a) move the window, mark nothing seen | weakened: the window moves for an undelivered reply | holds: `kg_sync` keeps the change | holds |
| (b) the hook acknowledges after printing | holds | holds | holds |
| (c) skip the notice near the timeout | weakened when the request queued before the handler | holds | weakened in the same case |
| (d) a longer client timeout | weakened, less often | holds | weakened, less often |
| (e) accept and document | weakened | holds | weakened |

Only (b) keeps all three, at the cost of a pending-notice record per session
and a second request per hook. The server cannot observe whether a hook's
output reached the model, so plausibly no hook-side rule without an
acknowledgement keeps P3 under a client timeout (inference, not checked).
