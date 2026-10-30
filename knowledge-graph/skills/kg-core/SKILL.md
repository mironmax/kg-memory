---
name: kg-core
user-invocable: false
description: |
  Knowledge Graph — persistent, granular, evolving memory.

  Start: the preloaded block is only the top of memory. One full
  kg_read(session_id) — kg_read(cwd="<project root>") when nothing was
  preloaded — comes before any work, whatever the task.

  Recall: when a gist points at the task, read that node in full. Search
  before re-deriving what this project may have met before; in a grown
  graph the fact you need is often buried under fresher work.

  Capture as you go, at the moment a lesson lands: a fix that took several
  tries, a surprise, a user's correction. One lesson, one node, its id
  naming the subject in 3-5 words, never a container: the gist states the
  lesson so it holds beyond the case that taught it; each note tells one
  case of it — what happened, where. A failure met again means
  its lesson missed something: add the case, then sharpen the gist to what
  the cases share. Connect with edges rather than re-describe a node that
  exists; a new lesson still earns its own node.

  Wrap-up, when a task is done and before the final answer: walk back
  through the work — each point where another choice would have changed
  the outcome is a lesson, rarely just one — and write what is still
  missing. Then endorse with kg_useful the nodes that helped, judged
  against results. A node that was MISSING (it existed, the work needed
  it, nothing surfaced it) is endorsed the moment that shows: that credit
  is what keeps a node alive.

  Mechanics live in the kg_* tool descriptions; operations in /kg-ops.
---
# Knowledge Graph Reference

The description above is what reaches the agent every session. The rest of
the doctrine lives where it is read at the moment it matters: the preload
header (the start of a session), the `kg_*` tool and parameter descriptions
(reading, writing, naming, endorsing), and the hook notices (capture, budget,
wrap-up). This body keeps what has no such channel yet; change those texts,
not a copy here.

## When there is no preload

Desktop sessions, or a server still warming up: `kg_read(cwd="<project root>")`
returns the full graph plus the session_id. With no project folder (a general
chat), `kg_read()` opens user memory alone; passing cwd with that session_id
later attaches the project. The tools start the server themselves and wait out
restarts, so a kg tool that keeps failing to connect means a start that fails
(a /kg-ops matter: `kg doctor`). Resuming an earlier conversation:
`kg_sync(session_id)` picks up what other sessions wrote meanwhile.

## What is worth capturing

Whatever took effort to obtain: how the project is wired, how a thing works,
decisions and their rationale, preferences and constraints, where things are.
And whatever gives future sessions navigation: a component node for files you
explored, saying what the cluster handles and what it does not.

## Placement

- Name things once. When a thing recurs across sessions (a service, a
  feature, a saga) one node owns it; event nodes record what changed and edge
  to the owner instead of re-describing it. Re-description is how an entity
  smears across a dozen narratives until search can no longer tell which node
  owns it.
- Touches are precise pointers, `path:line-range (short anchor)`, so the next
  session reads ten lines instead of the file.
- Cross-level edges (project decision to user principle) are legitimate;
  store them in the project graph.

Documents point INTO memory, never the other way round. A letter, a handover
or a README naming a node id is a stationary artifact taking a dependency on a
moving one: nodes get renamed, merged, archived, and the doc silently rots.
Write what the doc means in its own words, and let the node carry the doc's
path in `touches`.

Other memory systems (CLAUDE.md, auto memory) are supplementary; their
exclusion rules govern their own storage. When in doubt, record here.

## Levels

**user**: cross-project wisdom, preferences, principles, patterns that
travel. **project**: this codebase and its operations, architecture,
decisions, component nodes, discoveries.

## Subagents

Subagents receive no preload. The dispatching session sizes memory into each
prompt: exploration and mining agents get kg_* instructions plus the
session_id; ordinary task agents get the few relevant gists pasted; narrow
mechanical tasks get nothing.

## Operations

Server lifecycle, reconnect after restart, backup, troubleshooting: the
/kg-ops runbook covers it; do not improvise server management from here.
