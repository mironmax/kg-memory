#!/usr/bin/env bash
# KG ambient memory reminder — the attention mechanism. Skill instructions
# load once at session start and fade from attention as the context deepens;
# this hook re-injects one targeted nudge per prompt, near the end of context
# where attention is strongest. The pool is staged by session depth: early
# prompts point at recall (read before you work), mid-session at capture
# (write while it's fresh), deep sessions at maintenance and wrap-up (tend
# the garden, endorse what helped). Random pick within the stage prevents
# habituation to a fixed sequence.

# Session depth from transcript size (bytes), taken from the hook's stdin
# JSON. Rough but cheap: tool results dominate transcript growth, so file
# size tracks how much work has happened better than prompt count would.
# Unknown/missing transcript falls back to the mid pool.
# A maintenance chore runs headless inside this same plugin: it needs no
# preload (its prompt names its targets), no recall, and above all no chore
# dispatch of its own. The runner exports KG_CHORE=1, and any caller that
# wants no memory in a run sets KG_HOOKS=off — stay silent.
{ [ -n "${KG_CHORE:-}" ] || [ "${KG_HOOKS:-}" = off ]; } && exit 0

STDIN_JSON=$(cat 2>/dev/null)

# Server-side deterministic pass (v0.9.24+): POST the whole hook payload to
# /api/prompt_context — the server answers with ready-to-print hook output
# when it has something deterministic to say (the full-read nudge while the
# loud kg_read is outstanding, or gists matching this prompt), and {} when
# the staged random pools below should speak. The bash side never parses.
HOST="${KG_HTTP_HOST:-127.0.0.1}"
PORT="${KG_HTTP_PORT:-8765}"
RESP=$(printf '%s' "$STDIN_JSON" | curl -sf --max-time 1 -X POST \
    -H 'Content-Type: application/json' --data-binary @- \
    "http://${HOST}:${PORT}/api/prompt_context" 2>/dev/null)
case "$RESP" in
    *hookSpecificOutput*) printf '%s' "$RESP"; exit 0 ;;
esac

# Legacy fallback for a pre-0.9.24 server (endpoint missing → empty RESP):
# query /api/session_state and compose the full-read nudge hook-side. A new
# server answering {} already handled this case — skip straight to the pools.
if [ -z "$RESP" ]; then
    CWD=$(printf '%s' "$STDIN_JSON" | sed -n 's/.*"cwd":"\([^"]*\)".*/\1/p')
    if [ -n "$CWD" ]; then
        STATE=$(curl -sf --max-time 1 --get "http://${HOST}:${PORT}/api/session_state" \
            --data-urlencode "project_path=${CWD}" 2>/dev/null)
        if printf '%s' "$STATE" | grep -q '"found":[[:space:]]*true' && \
           printf '%s' "$STATE" | grep -q '"full_read_done":[[:space:]]*false'; then
            printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}' \
                "KG preload is a partial render; the full graph is not in context yet. Call kg_read(session_id) once before substantive work, following its parts with more=true if it says it continues; it renders everything the preload dropped without repeating it."
            exit 0
        fi
    fi
fi

DEPTH=mid
TRANSCRIPT=$(printf '%s' "$STDIN_JSON" | sed -n 's/.*"transcript_path":"\([^"]*\)".*/\1/p')
if [ -n "$TRANSCRIPT" ]; then
    if [ -f "$TRANSCRIPT" ]; then
        SIZE=$(stat -c%s "$TRANSCRIPT" 2>/dev/null || echo 0)
    else
        SIZE=0   # first prompt — transcript not written yet
    fi
    if [ "$SIZE" -lt 200000 ]; then
        DEPTH=early
    elif [ "$SIZE" -gt 2500000 ]; then
        DEPTH=deep
    fi
fi

early=(
  "KG memory is active. If no preload block is in context and nothing has been read yet, call kg_read(cwd) before task work."
  "Before searching files or the web, check the memory: kg_search may hold the answer."
  "Before opening files, check for component nodes in the memory; what is already mapped need not be re-read."
  "Archived node ids that relate to the current work: kg_read(session_id, ids=[...]) promotes them and returns their content."
  "A dispatched subagent receives no KG preload; put the relevant gists or kg_* instructions in its prompt."
  "Before acting on an assumption, kg_search. If the graph has nothing, state the assumption and capture what you find."
)

mid=(
  "KG capture pulse: did the last exchange produce anything worth keeping? Write it before moving on."
  "Did the user state a preference, a style or a constraint? Capture it as a user-level node now."
  "Did the user correct the approach? Capture the signal that was missed, not only the fix."
  "Did the user have to say something the graph already held? That is a miss: kg_search for the node, kg_read it to promote it, kg_useful it now. Only a miss corrects a wrong archival decision."
  "Did the user agree on an approach? Capture the method as a node; the decision alone is not enough."
  "Did something take ten minutes or more to resolve? Write a root-cause node before moving on."
  "Explained something non-obvious? That explanation is a node; write it before the context moves on."
  "Found how two parts of the codebase connect? That is an edge; write it now."
  "Before acting on an assumption, kg_search. If the graph has nothing, state the assumption and capture what you find."
  "Write discoveries during the conversation, not at the end of the task; the cache is warm and the cost is small."
  "A dispatched subagent receives no KG preload; put the relevant gists or kg_* instructions in its prompt."
  "Before searching files or the web, check the memory: kg_search may hold the answer."
)

deep=(
  "The context is deep: scan for anything unrecorded. This is the highest-value moment to capture."
  "Check the DEBT lines from your last kg_read: HIGH on either graph? Dispatch a maintenance subagent per the /kg-maintain skill (it gets no preload; the skill holds the dispatch prompt)."
  "Has a node's gist proved stale or vague in use? Sharpen it while the context is live."
  "Are edges missing between nodes used today? One edge raises both nodes' standing against archival."
  "Was an architectural decision made this session? A node with the rationale in notes, not only the conclusion."
  "What would a later session wish had been recorded from this conversation? Write it."
  "Wrapping up? Two questions: which nodes changed the outcome, and what was re-derived or corrected that the graph already held and never showed? Both earn kg_useful(session_id, ids=[...]); five is guidance, not a quota; send each one that earned it, judged by what happened."
  "KG capture pulse: did the last exchange produce anything worth keeping? Write it before moving on."
)

case "$DEPTH" in
  early) msgs=("${early[@]}") ;;
  deep)  msgs=("${deep[@]}") ;;
  *)     msgs=("${mid[@]}") ;;
esac

idx=$(( RANDOM % ${#msgs[@]} ))
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}' "${msgs[$idx]}"
