import { randomUUID } from "node:crypto";

import { verifyWorkflowPayload } from "./swisser_evidence.js";

const MAX_STATES = 128;
const MIN_REMAINING_MS = 5_000;
const ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// Warm Vercel instances reuse this map. A cold instance simply misses and the
// MCP tool rebuilds a fresh market cut instead of failing or using old data.
const states = new Map();
const latestByScope = new Map();

function scopeKey(workflow) {
  return [
    workflow.session || "anonymous",
    workflow.mode,
    [...(workflow.expected_symbols || [])].sort().join(","),
  ].join("|");
}

function removeState(id) {
  const stored = states.get(id);
  if (!stored) return;
  states.delete(id);
  const scope = scopeKey(stored.workflow);
  if (latestByScope.get(scope) === id) latestByScope.delete(scope);
}

function prune(now) {
  for (const [id, stored] of states) {
    if (stored.expiresAt < now) removeState(id);
  }
  while (states.size >= MAX_STATES) {
    removeState(states.keys().next().value);
  }
}

export function saveWorkflowState(workflow, { now = Date.now() } = {}) {
  verifyWorkflowPayload(workflow, { now });
  prune(now);
  const id = randomUUID();
  states.set(id, {
    workflow,
    expiresAt: workflow.exp,
  });
  latestByScope.set(scopeKey(workflow), id);
  return id;
}

export function loadWorkflowState(
  workflowId,
  { mode, stage = null, session = null, now = Date.now() } = {},
) {
  prune(now);
  if (!ID_PATTERN.test(String(workflowId || ""))) return null;
  const stored = states.get(workflowId);
  if (!stored) return null;
  if (stored.expiresAt <= now + MIN_REMAINING_MS) {
    removeState(workflowId);
    return null;
  }
  if (latestByScope.get(scopeKey(stored.workflow)) !== workflowId) return null;
  try {
    return verifyWorkflowPayload(stored.workflow, {
      mode,
      stage,
      session,
      now,
    });
  } catch {
    removeState(workflowId);
    return null;
  }
}

export function clearWorkflowStates() {
  states.clear();
  latestByScope.clear();
}
