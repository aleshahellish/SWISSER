import assert from "node:assert/strict";
import test from "node:test";

import {
  clearWorkflowStates,
  createRedisWorkflowBackend,
  createWorkflowStateStore,
  loadWorkflowState,
  redisCredentials,
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

test.beforeEach(async () => clearWorkflowStates());

test("workflow store returns only a current valid short ID", async () => {
  const state = workflow("current");
  const id = await saveWorkflowState(state, { now: NOW });

  assert.match(id, /^[0-9a-f-]{36}$/i);
  assert.equal(
    (await loadWorkflowState(id, {
      mode: "setups",
      stage: "scan",
      now: NOW + 1_000,
    })).run_id,
    "current",
  );
  assert.equal(await loadWorkflowState("corrupted-id", { now: NOW + 1_000 }), null);
  assert.equal(await loadWorkflowState(id, { mode: "entry", now: NOW + 1_000 }), null);
  assert.equal(
    (await loadWorkflowState(id, { mode: "setups", now: NOW + 1_000 })).run_id,
    "current",
    "a wrong caller must not delete a valid parallel workflow",
  );
});

test("parallel runs remain isolated instead of invalidating each other", async () => {
  const first = await saveWorkflowState(workflow("first"), { now: NOW });
  const second = await saveWorkflowState(workflow("second", { now: NOW + 1_000 }), {
    now: NOW + 1_000,
  });

  assert.equal((await loadWorkflowState(first, { now: NOW + 2_000 })).run_id, "first");
  assert.equal((await loadWorkflowState(second, { now: NOW + 2_000 })).run_id, "second");
});

test("expired server state cannot be reused", async () => {
  const id = await saveWorkflowState(workflow("expired"), { now: NOW });
  assert.equal(await loadWorkflowState(id, { now: NOW + 300_001 }), null);
});

test("state too close to expiry is rejected before another tool can outlive it", async () => {
  const id = await saveWorkflowState(workflow("near-expiry"), { now: NOW });
  assert.equal(await loadWorkflowState(id, { now: NOW + 295_000 }), null);
});

test("Redis-backed state survives a different serverless instance", async () => {
  const records = new Map();
  const setCalls = [];
  const client = {
    async set(key, value, options) {
      records.set(key, structuredClone(value));
      setCalls.push({ key, options });
      return "OK";
    },
    async get(key) {
      return records.get(key) ?? null;
    },
    async del(key) {
      records.delete(key);
      return 1;
    },
  };
  const firstInstance = createWorkflowStateStore(
    createRedisWorkflowBackend({ client }),
  );
  const coldInstance = createWorkflowStateStore(
    createRedisWorkflowBackend({ client }),
  );

  const id = await firstInstance.save(workflow("durable"), { now: NOW });
  const loaded = await coldInstance.load(id, {
    mode: "setups",
    stage: "scan",
    now: NOW + 2_000,
  });

  assert.equal(loaded.run_id, "durable");
  assert.equal(setCalls[0].options.px, 300_000);
  assert.match(setCalls[0].key, /^swisser:workflow:v4:/);
});

test("Vercel custom and default Upstash prefixes are both recognized", () => {
  assert.deepEqual(
    redisCredentials({
      STORAGE_KV_REST_API_URL: "https://storage.example",
      STORAGE_KV_REST_API_TOKEN: "storage-token",
    }),
    {
      url: "https://storage.example",
      token: "storage-token",
      urlName: "STORAGE_KV_REST_API_URL",
      tokenName: "STORAGE_KV_REST_API_TOKEN",
    },
  );
  assert.equal(
    redisCredentials({
      CUSTOM_KV_REST_API_URL: "https://custom.example",
      CUSTOM_KV_REST_API_TOKEN: "custom-token",
    }).urlName,
    "CUSTOM_KV_REST_API_URL",
  );
});
