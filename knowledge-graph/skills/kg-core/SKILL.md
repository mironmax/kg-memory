---
name: kg-core
user-invocable: false
description: |
  Knowledge Graph — persistent, granular, evolving memory. Read this skill in full at the start of a session.

  Start: the preload is a partial render of both graphs. One full kg_read(session_id), following its parts with more=true, comes before any work, in every session. Without a session_id, kg_read(cwd="<project root>") opens the session and returns one.

  Recall: read in full the nodes whose gists concern the task (kg_read ids=[...]). Before deriving something the project may already hold, use kg_search: it reaches archived and orphaned nodes, which the render does not show.

  Capture at the moment of learning: a fix that took several attempts, an unexpected result, a correction from the user. One lesson per node; the id names the subject in 3-5 words; the gist states the lesson in general terms; each note records one case (what happened, where). When a lesson recurs, add the case and rewrite the gist to what the cases share. Link related nodes with edges instead of restating them; a new lesson gets its own node.

  Wrap-up, when the task is done and before the final answer: walk back through the work, write the lessons not yet captured, and endorse with kg_useful the nodes that helped, judged against results. A node that existed, was needed and was not surfaced is endorsed as soon as that is established. Endorsements are the usefulness input of archival scoring.

  Mechanics: the kg_* tool descriptions. Operations: /kg-ops.
---
# Knowledge Graph Reference

The description above reaches the agent in every session; this body is the
reference behind it. The preload header, the `kg_*` tool descriptions and the
hook notices carry the same rules at the moment they apply. A rule changes in
all of them together.

## Session start

The preload, a hook injection at session start, shows the top-scored nodes of
the user graph and the project graph within a small budget, with the
session_id. `kg_read(session_id)` renders the rest: every active gist not yet
shown, archived nodes as ids, live edges. A long render arrives in parts;
`kg_read(session_id, more=true)` returns the next part until the reply says it
is complete. The session_id goes on every later `kg_*` call: it keeps one
session, includes the project graph in searches and avoids registering further
sessions.

Without a preload (a desktop chat, a subagent, a server still starting):
`kg_read(cwd="<project root>")` opens the session, renders both graphs and
returns the session_id. `kg_read()` without cwd opens the user graph only; a
later call with cwd and the session_id attaches the project. Resuming an
earlier conversation: `kg_sync(session_id)` adds what other sessions wrote
since.

A kg tool that repeatedly fails to connect means the server did not start:
/kg-ops, `kg doctor`.

## Recall

The render is the surface: active gists in full, archived nodes as ids,
orphaned nodes not at all. `kg_read(session_id, ids=[...])` returns a node's
gist, notes, touches and edges, several nodes per call; reading an archived
node promotes it to active. `kg_search` matches ids, gists, notes and touches
across all tiers. A node found by search that should have been on the surface
is a miss; endorse it (see Endorsement).

## Capture

Worth capturing: what took effort to obtain (how the project is wired, how a
thing works, decisions and their rationale, preferences and constraints, where
things are) and what gives a later session navigation (a component node for a
cluster of files: what it handles and what it does not).

Placement:

- One lesson per node. A gist joining two ideas with "and" is two nodes and an
  edge.
- The id names the subject in 3-5 kebab-case words; the claim about the
  subject goes in the gist. Ids are weighted in search and refused at seven
  words or more. Rename with `kg_rename_node`, which carries edges and history;
  a new node plus a deletion loses both.
- No dates in ids or gists. A date says when something was written, not what
  it is; a node per dated event is usually one enduring node for the subject,
  updated as events land. A note may name the day of its case.
- The gist states the lesson in general terms; rationale and steps go in
  notes; each note is one case.
- Touches are precise pointers: `path:line-range (short anchor)`.
- Name a recurring thing once. One node owns a service, a feature or a saga;
  event nodes record what changed and edge to the owner. An entity restated in
  several nodes leaves search unable to tell which node owns it.
- Cross-level edges (a project decision to a user principle) are stored in the
  project graph.

Documents point into memory, not the other way round: a letter or a README
that names a node id depends on an id that may be renamed, merged or archived.
State the meaning in the document's own words, and let the node carry the
document's path in its touches.

Other memory systems (CLAUDE.md, auto memory) are supplementary; their
exclusion rules govern their own storage. When in doubt, record here.

## Endorsement

`kg_useful(session_id, ids=[...])` is the usefulness input of archival scoring;
reads, searches and citations do not count. Two cases earn it. A node that
helped: it was in context and the work went differently for it, judged at
wrap-up against the result. A node that was missing: it existed, this session
needed it, and nothing surfaced it; send this one as soon as it is established,
because a session may end before its wrap-up and a miss is the only signal
that corrects an archival decision. The guidance is five per session; the
reply says how far past it a session is; ten is the hard cap. One vote per
node. An endorsement does not move a node to the surface; a full read does,
and promotes an archived node. If the gist was also stale, update it.

## Levels

**user**: knowledge that applies across projects (preferences, principles,
patterns). **project**: this codebase and its operations (architecture,
decisions, component nodes, discoveries).

## Subagents

Subagents receive no preload. The dispatching session puts memory into each
prompt: exploration and mining agents get `kg_*` instructions and open their
own session with `kg_read(cwd=...)`; ordinary task agents get the relevant
gists pasted; narrow mechanical tasks get nothing.

## Operations

Server lifecycle, reconnect after a restart, backup, troubleshooting: the
/kg-ops runbook. Do not improvise server management from here.
