import assert from "node:assert/strict";
import test from "node:test";

import {
  clearWorkflowStates,
  loadWorkflowState,
  saveWorkflowState,
} from "../swisser_workflow_store.js";

const NOW = Date.parse("2026-08-24T18:00:00.000Z");

function workflow(runId, { mode = "setups", stage = "scan", now = NOW } = {}) {
  return {
    v: 3,
    type: "workflow",
    stage,
    run_id: runId,
    mode,
    expected_symbols: [],
    requested_symbols: [],
    scan: { source_ms: now, summaries: {} },
    snapshots: {},
    session: null,
    iat: now,
    exp: now + 300_000,
  };
}

test.beforeEach(() => clearWorkflowStates());

test("workflow store returns only a current valid short ID", () => {
  const state = workflow("current");
  const id = saveWorkflowState(state, { now: NOW });

  assert.match(id, /^[0-9a-f-]{36}$/i);
  assert.equal(
    loadWorkflowState(id, { mode: "setups", stage: "scan", now: NOW + 1_000 }).run_id,
    "current",
  );
  assert.equal(loadWorkflowState("corrupted-id", { now: NOW + 1_000 }), null);
  assert.equal(loadWorkflowState(id, { mode: "entry", now: NOW + 1_000 }), null);
});

test("a newer run supersedes an older run in the same scope", () => {
  const first = saveWorkflowState(workflow("first"), { now: NOW });
  const second = saveWorkflowState(workflow("second", { now: NOW + 1_000 }), {
    now: NOW + 1_000,
  });

  assert.equal(loadWorkflowState(first, { now: NOW + 2_000 }), null);
  assert.equal(loadWorkflowState(second, { now: NOW + 2_000 }).run_id, "second");
});

test("expired server state cannot be reused", () => {
  const id = saveWorkflowState(workflow("expired"), { now: NOW });
  assert.equal(loadWorkflowState(id, { now: NOW + 300_001 }), null);
});

test("state too close to expiry is replaced before another tool can outlive it", () => {
  const id = saveWorkflowState(workflow("near-expiry"), { now: NOW });
  assert.equal(loadWorkflowState(id, { now: NOW + 295_000 }), null);
});
