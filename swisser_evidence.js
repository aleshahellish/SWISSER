import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { deflateRawSync, inflateRawSync } from "node:zlib";

export const TRADE_SYMBOLS = [
  "TAO_USDT",
  "HYPE_USDT",
  "SOL_USDT",
  "XRP_USDT",
  "DOGE_USDT",
  "ETH_USDT",
];
export const BTC_SYMBOL = "BTC_USDT";
export const SUPPORTED_SYMBOLS = [...TRADE_SYMBOLS, BTC_SYMBOL];

// One atomic workflow token replaces the former run + scan + N snapshot tokens.
// Five minutes is long enough for ChatGPT reasoning while keeping a 1m entry cut fresh.
export const EVIDENCE_TTL_MS = 300_000;

const TOKEN_VERSION = 2;
const TOKEN_FORMAT = "z2";
const MAX_ENCODED_TOKEN_PAYLOAD_LENGTH = 12_288;
const MAX_DECODED_TOKEN_PAYLOAD_LENGTH = 49_152;
const SOURCE_AGE_TOLERANCE_MS = 90_000;
const FUTURE_CLOCK_TOLERANCE_MS = 15_000;
const TOKEN_SECRET =
  process.env.SWISSER_EVIDENCE_SECRET ??
  "swisser-evidence-integrity-v1-not-an-authentication-secret";

const MODE_ALIASES = {
  overview: "overview",
  setups: "setups",
  entry: "entry",
  quick: "overview",
  trades: "setups",
  day: "entry",
};

function fail(message) {
  throw new Error(`SWISSER integrity error: ${message}`);
}

export function canonicalMode(value) {
  const mode = MODE_ALIASES[String(value || "").toLowerCase()];
  if (!mode) fail(`unsupported mode ${String(value)}`);
  return mode;
}

function uniqueSymbols(values, { tradeOnly = false } = {}) {
  const allowed = tradeOnly ? TRADE_SYMBOLS : SUPPORTED_SYMBOLS;
  const result = [];
  for (const raw of values || []) {
    const symbol = String(raw || "").trim().toUpperCase();
    if (!allowed.includes(symbol)) fail(`unsupported symbol ${symbol}`);
    if (!result.includes(symbol)) result.push(symbol);
  }
  return result;
}

function signature(signedPayload) {
  return createHmac("sha256", TOKEN_SECRET)
    .update(signedPayload)
    .digest("base64url");
}

function encodeToken(payload) {
  const encodedPayload = deflateRawSync(Buffer.from(JSON.stringify(payload)), {
    level: 9,
  }).toString("base64url");
  const signedPayload = `${TOKEN_FORMAT}.${encodedPayload}`;
  return `${signedPayload}.${signature(signedPayload)}`;
}

function constantTimeEqual(left, right) {
  const a = Buffer.from(String(left));
  const b = Buffer.from(String(right));
  return a.length === b.length && timingSafeEqual(a, b);
}

function decodeWorkflowToken(token, now = Date.now()) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3 || parts[0] !== TOKEN_FORMAT) {
    if (parts[0] === "z1") {
      fail("obsolete evidence token; refresh SWISSER and start a new analysis");
    }
    fail("malformed workflow token");
  }

  const [, encodedPayload, suppliedSignature] = parts;
  if (
    !encodedPayload ||
    !suppliedSignature ||
    encodedPayload.length > MAX_ENCODED_TOKEN_PAYLOAD_LENGTH
  ) {
    fail("malformed workflow token");
  }

  const signedPayload = `${TOKEN_FORMAT}.${encodedPayload}`;
  if (!constantTimeEqual(signature(signedPayload), suppliedSignature)) {
    fail("invalid workflow token signature");
  }

  let payload;
  try {
    const json = inflateRawSync(Buffer.from(encodedPayload, "base64url"), {
      maxOutputLength: MAX_DECODED_TOKEN_PAYLOAD_LENGTH,
    }).toString("utf8");
    if (json.length > MAX_DECODED_TOKEN_PAYLOAD_LENGTH) {
      fail("invalid workflow token payload");
    }
    payload = JSON.parse(json);
  } catch {
    fail("invalid workflow token payload");
  }

  if (payload.v !== TOKEN_VERSION || payload.type !== "workflow") {
    fail("expected current workflow token");
  }
  if (!Number.isFinite(payload.iat) || !Number.isFinite(payload.exp)) {
    fail("workflow token has no valid lifetime");
  }
  if (payload.iat > now + FUTURE_CLOCK_TOLERANCE_MS) {
    fail("workflow token is from the future");
  }
  if (payload.exp < now) {
    fail("workflow evidence expired; collect one new market cut");
  }
  return payload;
}

function sessionBinding(session) {
  if (!session) return null;
  return createHmac("sha256", TOKEN_SECRET)
    .update(`session:${session}`)
    .digest("base64url")
    .slice(0, 24);
}

function assertSession(payload, session) {
  if (payload.session && session && payload.session !== sessionBinding(session)) {
    fail("evidence belongs to another ChatGPT session");
  }
}

function assertSameSet(actual, expected, label) {
  const left = [...actual].sort();
  const right = [...expected].sort();
  if (left.length !== right.length || left.some((value, index) => value !== right[index])) {
    fail(`${label} symbols do not match`);
  }
}

function sourceTimeMs(data, label) {
  const seconds = Number(data?.fetched_at_unix);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    fail(`${label} has no valid fetched_at_unix`);
  }
  return Math.round(seconds * 1000);
}

function assertNewScanSource(data, now) {
  const sourceMs = sourceTimeMs(data, "scanner");
  if (sourceMs < now - SOURCE_AGE_TOLERANCE_MS) {
    fail("scanner response is not a fresh market cut");
  }
  if (sourceMs > now + FUTURE_CLOCK_TOLERANCE_MS) {
    fail("scanner is from the future");
  }
  return sourceMs;
}

function cardSignal(value) {
  const normalized = String(value || "").toUpperCase();
  if (normalized.includes("BULL")) return "Bull";
  if (normalized.includes("BEAR")) return "Bear";
  if (normalized.includes("WAIT") || normalized.includes("MIXED")) return "Wait";
  return "—";
}

function cardIdea(value) {
  const normalized = String(value || "").replaceAll("_", " ").toUpperCase();
  if (normalized.includes("LOCAL") && normalized.includes("LONG")) return "Local Long";
  if (normalized.includes("LOCAL") && normalized.includes("SHORT")) return "Local Short";
  if (normalized.includes("LONG")) return "Long";
  if (normalized.includes("SHORT")) return "Short";
  if (normalized.includes("WAIT")) return "Wait";
  return "—";
}

function tradeDirection(value) {
  const normalized = String(value || "").replaceAll("_", " ").toUpperCase();
  if (normalized.includes("BULL") || normalized.includes("LONG")) return "Long";
  if (normalized.includes("BEAR") || normalized.includes("SHORT")) return "Short";
  return null;
}

function summarizeMarketItem(item) {
  if (!item || item.ok !== true || !SUPPORTED_SYMBOLS.includes(item.symbol)) {
    fail(`market row ${String(item?.symbol || "unknown")} is unsuccessful`);
  }
  if (!Number.isFinite(Number(item.current_price)) || Number(item.current_price) <= 0) {
    fail(`market row ${item.symbol} has no valid current price`);
  }
  const hierarchy = item?.mtf_hierarchy || {};
  for (const key of [
    "higher_timeframe_bias",
    "session_timeframe_bias",
    "setup_timeframe_bias",
    "entry_timeframe_bias",
    "active_trade_scenario",
  ]) {
    if (!hierarchy[key] || typeof hierarchy[key] !== "object") {
      fail(`market row ${item.symbol} has no ${key}`);
    }
  }

  const active = hierarchy.active_trade_scenario || {};
  const continuation = hierarchy.continuation_bias || {};
  const allowedDirections = new Set();
  for (const value of [
    active.label,
    active.direction,
    active.current_local_direction,
    active.potential_local_direction,
    active.potential_continuation_direction,
    continuation.direction,
    continuation.bias_direction,
    hierarchy.trade_direction_preference?.direction,
  ]) {
    const direction = tradeDirection(value);
    if (direction) allowedDirections.add(direction);
  }

  return {
    symbol: item.symbol,
    price: item.current_price,
    h4: cardSignal(hierarchy.higher_timeframe_bias?.direction),
    h1: cardSignal(hierarchy.session_timeframe_bias?.direction),
    m15: cardSignal(hierarchy.setup_timeframe_bias?.direction),
    m1: cardSignal(hierarchy.entry_timeframe_bias?.direction),
    idea: cardIdea(active.label || active.direction),
    allowed_directions: [...allowedDirections],
  };
}

function validateModeSymbols(mode, expectedSymbols, requestedSymbols) {
  const expected = uniqueSymbols(expectedSymbols, { tradeOnly: true });
  const requested = uniqueSymbols(requestedSymbols);
  if (mode === "entry") {
    if (!expected.length) {
      fail("entry has no saved candidates; run «Лучшие сетапы» first");
    }
    assertSameSet(requested, [...expected, BTC_SYMBOL], "entry scanner");
  } else {
    if (expected.length) fail("expected symbols are only valid for entry");
    assertSameSet(requested, SUPPORTED_SYMBOLS, `${mode} scanner`);
  }
  return { expected, requested };
}

export function createScanWorkflowEvidence({
  mode,
  expectedSymbols = [],
  session = null,
  data,
  requestedSymbols,
  now = Date.now(),
  runId = randomUUID(),
} = {}) {
  const canonical = canonicalMode(mode);
  const { expected, requested } = validateModeSymbols(
    canonical,
    expectedSymbols,
    requestedSymbols,
  );
  if (!data || data.ok !== true || data.mode !== "swisser_gpt_scan") {
    fail("scanner response is unsuccessful or has the wrong mode");
  }
  if (!Array.isArray(data.results) || !data.results.length) {
    fail("scanner returned no rows");
  }
  if ((data.requested_symbols || []).length !== requested.length) {
    fail("scanner response has missing or duplicate requested symbols");
  }
  if (data.results.length !== requested.length) {
    fail("scanner result has missing or duplicate rows");
  }
  assertSameSet(uniqueSymbols(data.requested_symbols || []), requested, "scanner response");
  assertSameSet(
    uniqueSymbols(data.results.map((item) => item?.symbol)),
    requested,
    "scanner result",
  );

  const sourceMs = assertNewScanSource(data, now);
  const summaries = Object.fromEntries(
    data.results.map((item) => [item.symbol, summarizeMarketItem(item)]),
  );
  const payload = {
    v: TOKEN_VERSION,
    type: "workflow",
    stage: "scan",
    run_id: runId,
    mode: canonical,
    expected_symbols: expected,
    requested_symbols: requested,
    scan: { source_ms: sourceMs, summaries },
    snapshots: {},
    session: sessionBinding(session),
    iat: now,
    exp: sourceMs + EVIDENCE_TTL_MS,
  };
  return { token: encodeToken(payload), payload };
}

export function verifyWorkflowEvidenceToken(
  token,
  { mode, stage = null, session = null, now = Date.now() } = {},
) {
  const payload = decodeWorkflowToken(token, now);
  assertSession(payload, session);
  if (mode && payload.mode !== canonicalMode(mode)) fail("workflow mode mismatch");
  const allowedStages = stage == null ? null : Array.isArray(stage) ? stage : [stage];
  if (allowedStages && !allowedStages.includes(payload.stage)) {
    fail(`workflow stage ${payload.stage} cannot be used here`);
  }
  return payload;
}

export function createSnapshotBundleEvidence({
  workflow,
  dataBySymbol,
  symbols,
  now = Date.now(),
} = {}) {
  if (!workflow || workflow.type !== "workflow" || workflow.stage !== "scan") {
    fail("snapshot bundle requires one current scanner workflow");
  }
  if (workflow.mode === "overview") fail("overview must not use snapshots");

  const selected = uniqueSymbols(symbols, { tradeOnly: true });
  if (!selected.length) fail("snapshot bundle has no candidates");
  for (const symbol of selected) {
    if (!workflow.requested_symbols.includes(symbol)) {
      fail(`snapshot symbol ${symbol} was not part of the scanner cut`);
    }
  }
  if (workflow.mode === "entry") {
    assertSameSet(selected, workflow.expected_symbols, "entry snapshot bundle");
  }

  const supplied = dataBySymbol instanceof Map
    ? dataBySymbol
    : new Map(Object.entries(dataBySymbol || {}));
  assertSameSet([...supplied.keys()], selected, "snapshot bundle response");

  const snapshots = {};
  for (const symbol of selected) {
    const data = supplied.get(symbol);
    if (!data || data.ok !== true || data.mode !== "swisser_gpt_snapshot") {
      fail(`snapshot ${symbol} is unsuccessful or has the wrong mode`);
    }
    if (data.symbol !== symbol) fail(`snapshot ${symbol} returned another symbol`);
    const sourceMs = sourceTimeMs(data, `snapshot ${symbol}`);
    if (sourceMs < workflow.scan.source_ms) {
      fail(`snapshot ${symbol} is older than the current scanner`);
    }
    if (sourceMs > now + FUTURE_CLOCK_TOLERANCE_MS) {
      fail(`snapshot ${symbol} is from the future`);
    }
    snapshots[symbol] = {
      source_ms: sourceMs,
      summary: summarizeMarketItem(data),
    };
  }

  const payload = {
    ...workflow,
    stage: "bundle",
    snapshots,
    iat: now,
  };
  return { token: encodeToken(payload), payload };
}

function shortSymbol(symbol) {
  return String(symbol).replace(/_USDT$/, "");
}

function fullSymbol(symbol) {
  const normalized = String(symbol || "").trim().toUpperCase();
  return normalized.endsWith("_USDT") ? normalized : `${normalized}_USDT`;
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  let maximumFractionDigits = 2;
  if (Math.abs(number) < 1) maximumFractionDigits = 6;
  else if (Math.abs(number) < 100) maximumFractionDigits = 4;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: Math.min(2, maximumFractionDigits),
    maximumFractionDigits,
  }).format(number);
}

function moscowTime(timestampMs) {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(timestampMs));
}

function cutTime(workflow) {
  const times = [
    workflow.scan.source_ms,
    ...Object.values(workflow.snapshots || {}).map((item) => item.source_ms),
  ].sort((a, b) => a - b);
  const first = moscowTime(times[0]);
  const last = moscowTime(times[times.length - 1]);
  return `${first === last ? first : `${first}–${last}`} МСК`;
}

function directionFamily(value) {
  return String(value || "").toLowerCase().includes("short") ? "Short" : "Long";
}

export function buildVerifiedCard({
  evidenceToken,
  mode,
  lead,
  marketRows,
  candidates = [],
  conclusion,
  session = null,
  now = Date.now(),
} = {}) {
  const canonical = canonicalMode(mode);
  const workflow = verifyWorkflowEvidenceToken(evidenceToken, {
    mode: canonical,
    session,
    now,
  });
  const snapshots = workflow.snapshots || {};
  const snapshotSymbols = Object.keys(snapshots);

  const inputRows = Array.isArray(marketRows) ? marketRows : [];
  const rowSymbols = inputRows.map((row) => fullSymbol(row.symbol));
  if (new Set(rowSymbols).size !== rowSymbols.length) fail("duplicate market row");

  const scannedTradeSymbols = workflow.requested_symbols.filter(
    (symbol) => symbol !== BTC_SYMBOL,
  );
  if (canonical === "overview" || canonical === "setups") {
    assertSameSet(scannedTradeSymbols, TRADE_SYMBOLS, `${canonical} scanner`);
    assertSameSet(rowSymbols, TRADE_SYMBOLS, `${canonical} card`);
  } else {
    assertSameSet(scannedTradeSymbols, workflow.expected_symbols, "entry scanner");
    assertSameSet(rowSymbols, workflow.expected_symbols, "entry card");
    if (workflow.stage !== "bundle") fail("entry requires one fresh snapshot bundle");
    assertSameSet(snapshotSymbols, workflow.expected_symbols, "entry snapshot bundle");
  }

  if (canonical === "overview") {
    if (workflow.stage !== "scan" || snapshotSymbols.length) {
      fail("overview must use scanner evidence only");
    }
    if (candidates.length) fail("overview must not contain candidates");
  }
  if (canonical === "setups") {
    for (const row of inputRows) {
      const symbol = fullSymbol(row.symbol);
      if (["top", "secondary"].includes(row.priority) && !snapshots[symbol]) {
        fail(`ranked setup ${symbol} has no fresh snapshot`);
      }
    }
  }

  const inputRowsBySymbol = new Map(
    inputRows.map((row) => [fullSymbol(row.symbol), row]),
  );
  const orderedSymbols = canonical === "overview" ? TRADE_SYMBOLS : rowSymbols;
  const verifiedRows = orderedSymbols.map((symbol) => {
    const input = inputRowsBySymbol.get(symbol);
    const summary = snapshots[symbol]?.summary || workflow.scan.summaries[symbol];
    if (!summary) fail(`no current evidence for ${symbol}`);
    return {
      symbol: shortSymbol(symbol),
      price: formatPrice(summary.price),
      idea: summary.idea,
      h4: summary.h4,
      h1: summary.h1,
      m15: summary.m15,
      m1: summary.m1,
      priority: canonical === "setups" ? input.priority || "none" : "none",
      note: input.note,
    };
  });

  const candidateSymbols = new Set();
  for (const candidate of candidates) {
    const symbol = fullSymbol(candidate.symbol);
    if (candidateSymbols.has(symbol)) fail(`duplicate candidate ${symbol}`);
    candidateSymbols.add(symbol);
    if (!rowSymbols.includes(symbol)) fail(`candidate ${symbol} is outside the current card`);
    if (!snapshots[symbol]) fail(`candidate ${symbol} has no fresh snapshot`);
    if (candidate.targets.length !== candidate.pnl_6x.length) {
      fail(`candidate ${symbol} has unmatched targets and PnL values`);
    }
    const allowed = snapshots[symbol].summary.allowed_directions || [];
    const family = directionFamily(candidate.direction);
    if (allowed.length && !allowed.includes(family)) {
      fail(`candidate ${symbol} direction conflicts with current evidence`);
    }
  }

  const btc = workflow.scan.summaries[BTC_SYMBOL];
  if (!btc) fail("current scanner has no BTC context");
  return {
    mode: canonical,
    cut_time: cutTime(workflow),
    btc_price: formatPrice(btc.price),
    btc_structure: { h4: btc.h4, h1: btc.h1, m15: btc.m15, m1: btc.m1 },
    lead,
    market_rows: verifiedRows,
    candidates,
    conclusion,
    source_integrity: {
      verified: true,
      protocol: "atomic-v2",
      run_id: workflow.run_id,
      scan_fetched_at_unix: Math.round(workflow.scan.source_ms / 1000),
      snapshot_fetched_at_unix: Object.fromEntries(
        Object.entries(snapshots).map(([symbol, item]) => [
          symbol,
          Math.round(item.source_ms / 1000),
        ]),
      ),
    },
  };
}
