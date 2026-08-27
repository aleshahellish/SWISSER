import { randomUUID } from "node:crypto";

import { Redis } from "@upstash/redis";

import { verifyWorkflowPayload } from "./swisser_evidence.js";

const MIN_REMAINING_MS = 5_000;
const KEY_PREFIX = "swisser:workflow:v4:";
const ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

let defaultBackend;
let backendOverride;

function configurationError(message) {
  return new Error(`SWISSER configuration error: ${message}`);
}

export function redisCredentials(env = process.env) {
  const explicitPairs = [
    ["STORAGE_KV_REST_API_URL", "STORAGE_KV_REST_API_TOKEN"],
    ["KV_REST_API_URL", "KV_REST_API_TOKEN"],
    ["UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"],
    ["STORAGE_REDIS_REST_URL", "STORAGE_REDIS_REST_TOKEN"],
    ["STORAGE_REST_API_URL", "STORAGE_REST_API_TOKEN"],
  ];
  for (const [urlName, tokenName] of explicitPairs) {
    if (env[urlName] && env[tokenName]) {
      return { url: env[urlName], token: env[tokenName], urlName, tokenName };
    }
  }

  // Vercel allows a custom integration prefix. Accept any complete pair with
  // the standard Upstash suffix so renaming the prefix cannot break storage.
  for (const urlName of Object.keys(env).filter((name) =>
    name.endsWith("_KV_REST_API_URL") || name.endsWith("_REDIS_REST_URL"))) {
    const tokenName = urlName.endsWith("_KV_REST_API_URL")
      ? urlName.replace(/_KV_REST_API_URL$/, "_KV_REST_API_TOKEN")
      : urlName.replace(/_REDIS_REST_URL$/, "_REDIS_REST_TOKEN");
    if (env[urlName] && env[tokenName]) {
      return { url: env[urlName], token: env[tokenName], urlName, tokenName };
    }
  }
  return null;
}

export function createMemoryWorkflowBackend(states = new Map()) {
  return {
    kind: "memory",
    async set(id, workflow, ttlMs, now) {
      states.set(id, { workflow, expiresAt: now + ttlMs });
    },
    async get(id, now) {
      const stored = states.get(id);
      if (!stored) return null;
      if (stored.expiresAt <= now) {
        states.delete(id);
        return null;
      }
      return stored.workflow;
    },
    async delete(id) {
      states.delete(id);
    },
    async clear() {
      states.clear();
    },
  };
}

export function createRedisWorkflowBackend({ url, token, client } = {}) {
  if (!client && (!url || !token)) {
    throw configurationError("Upstash REST URL/token are missing");
  }
  const redis = client || new Redis({
    url,
    token,
    retry: {
      retries: 2,
      backoff: (attempt) => Math.min(100 * 2 ** attempt, 800),
    },
  });
  return {
    kind: "redis",
    async set(id, workflow, ttlMs) {
      await redis.set(`${KEY_PREFIX}${id}`, workflow, { px: ttlMs });
    },
    async get(id) {
      return redis.get(`${KEY_PREFIX}${id}`);
    },
    async delete(id) {
      await redis.del(`${KEY_PREFIX}${id}`);
    },
  };
}

function resolveDefaultBackend() {
  const credentials = redisCredentials();
  if (credentials) return createRedisWorkflowBackend(credentials);
  if (process.env.VERCEL) {
    throw configurationError(
      "durable Upstash Redis is not connected to this Vercel environment",
    );
  }
  return createMemoryWorkflowBackend();
}

function activeBackend() {
  if (backendOverride) return backendOverride;
  if (!defaultBackend) defaultBackend = resolveDefaultBackend();
  return defaultBackend;
}

export function setWorkflowBackendForTests(backend = null) {
  backendOverride = backend || undefined;
}

export function createWorkflowStateStore(backend) {
  if (!backend?.set || !backend?.get) {
    throw new TypeError("workflow backend must implement get and set");
  }
  return {
    async save(workflow, { now = Date.now() } = {}) {
      verifyWorkflowPayload(workflow, { now });
      const ttlMs = workflow.exp - now;
      if (ttlMs <= MIN_REMAINING_MS) {
        throw new Error("SWISSER integrity error: workflow is too close to expiry");
      }
      const id = randomUUID();
      await backend.set(id, workflow, ttlMs, now);
      return id;
    },

    async load(
      workflowId,
      { mode, stage = null, session = null, now = Date.now() } = {},
    ) {
      if (!ID_PATTERN.test(String(workflowId || ""))) return null;
      let stored;
      try {
        stored = await backend.get(workflowId, now);
        if (typeof stored === "string") stored = JSON.parse(stored);
      } catch (error) {
        if (error instanceof SyntaxError) {
          await backend.delete?.(workflowId);
          return null;
        }
        throw error;
      }
      if (!stored || stored.exp <= now + MIN_REMAINING_MS) {
        if (stored) await backend.delete?.(workflowId);
        return null;
      }
      try {
        return verifyWorkflowPayload(stored, { mode, stage, session, now });
      } catch {
        // A wrong mode/session must not destroy an otherwise valid parallel run.
        return null;
      }
    },
  };
}

function defaultStore() {
  return createWorkflowStateStore(activeBackend());
}

export async function saveWorkflowState(workflow, options = {}) {
  return defaultStore().save(workflow, options);
}

export async function loadWorkflowState(workflowId, options = {}) {
  return defaultStore().load(workflowId, options);
}

export async function clearWorkflowStates() {
  const backend = activeBackend();
  if (backend.kind !== "memory" || !backend.clear) {
    throw new Error("clearWorkflowStates is only available for the local test backend");
  }
  await backend.clear();
}
