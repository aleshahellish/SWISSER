import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

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
export const RUN_TTL_MS = 180_000;

const TOKEN_VERSION = 1;
const SOURCE_START_TOLERANCE_MS = 15_000;
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

function signature(encodedPayload) {
  return createHmac("sha256", TOKEN_SECRET)
    .update(encodedPayload)
    .digest("base64url");
}

function encodeToken(payload) {
  const encodedPayload = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `${encodedPayload}.${signature(encodedPayload)}`;
}

function constantTimeEqual(left, right) {
  const a = Buffer.from(String(left));
  const b = Buffer.from(String(right));
  return a.length === b.length && timingSafeEqual(a, b);
}

function decodeToken(token, expectedType, now = Date.now()) {
  const [encodedPayload, suppliedSignature, extra] = String(token || "").split(".");
  if (!encodedPayload || !suppliedSignature || extra) fail("malformed evidence token");
  if (!constantTimeEqual(signature(encodedPayload), suppliedSignature)) {
    fail("invalid evidence token signature");
  }

  let payload;
  try {
    payload = JSON.parse(Buffer.from(encodedPayload, "base64url").toString("utf8"));
  } catch {
    fail("invalid evidence token payload");
  }

  if (payload.v !== TOKEN_VERSION || payload.type !== expectedType) {
    fail(`expected ${expectedType} token`);
  }
  if (!Number.isFinite(payload.iat) || !Number.isFinite(payload.exp)) {
    fail("token has no valid lifetime");
  }
  if (payload.iat > now + FUTURE_CLOCK_TOLERANCE_MS) fail("token is from the future");
  if (payload.exp < now) fail("evidence token expired; collect a new market cut");
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
    fail(`${label} symbols do not match the current run`);
  }
}

function sourceTimeMs(data, label) {
  const seconds = Number(data?.fetched_at_unix);
  if (!Number.isFinite(seconds) || seconds <= 0) fail(`${label} has no valid fetched_at_unix`);
  return Math.round(seconds * 1000);
}

function assertFreshSource(data, run, now, label) {
  const sourceMs = sourceTimeMs(data, label);
  if (sourceMs < run.iat - SOURCE_START_TOLERANCE_MS) {
    fail(`${label} predates the current run`);
  }
  if (sourceMs > now + FUTURE_CLOCK_TOLERANCE_MS) fail(`${label} is from the future`);
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

export function createRunToken({
  mode,
  expectedSymbols = [],
  session = null,
  now = Date.now(),
  runId = randomUUID(),
} = {}) {
  const canonical = canonicalMode(mode);
  const expected = uniqueSymbols(expectedSymbols, { tradeOnly: true });
  if (canonical !== "entry" && expected.length) {
    fail("expected symbols are only valid for an entry run");
  }
  const payload = {
    v: TOKEN_VERSION,
    type: "run",
    run_id: runId,
    mode: canonical,
    expected_symbols: expected,
    session: sessionBinding(session),
    iat: now,
    exp: now + RUN_TTL_MS,
  };
  return { token: encodeToken(payload), payload };
}

export function verifyRunToken(token, { mode, session = null, now = Date.now() } = {}) {
  const payload = decodeToken(token, "run", now);
  assertSession(payload, session);
  if (mode && payload.mode !== canonicalMode(mode)) fail("run mode mismatch");
  return payload;
}

export function createScanEvidenceToken({
  run,
  data,
  requestedSymbols,
  now = Date.now(),
} = {}) {
  if (!data || data.ok !== true || data.mode !== "swisser_gpt_scan") {
    fail("scanner response is unsuccessful or has the wrong mode");
  }
  if (!Array.isArray(data.results) || !data.results.length) fail("scanner returned no rows");
  const requested = uniqueSymbols(requestedSymbols);
  if ((data.requested_symbols || []).length !== requested.length) {
    fail("scanner response has missing or duplicate requested symbols");
  }
  if (data.results.length !== requested.length) {
    fail("scanner result has missing or duplicate rows");
  }
  const responseRequested = uniqueSymbols(data.requested_symbols || []);
  assertSameSet(responseRequested, requested, "scanner response");
  const resultSymbols = uniqueSymbols(data.results.map((item) => item?.symbol));
  assertSameSet(resultSymbols, requested, "scanner result");
  const sourceMs = assertFreshSource(data, run, now, "scanner");

  const summaries = Object.fromEntries(
    data.results.map((item) => [item.symbol, summarizeMarketItem(item)]),
  );
  const payload = {
    v: TOKEN_VERSION,
    type: "scan",
    run_id: run.run_id,
    mode: run.mode,
    requested_symbols: requested,
    source_ms: sourceMs,
    summaries,
    iat: now,
    exp: run.exp,
  };
  return { token: encodeToken(payload), payload };
}

export function verifyScanEvidenceToken(
  token,
  { run, now = Date.now() } = {},
) {
  const payload = decodeToken(token, "scan", now);
  if (payload.run_id !== run.run_id || payload.mode !== run.mode) {
    fail("scanner evidence belongs to another run");
  }
  return payload;
}

export function createSnapshotEvidenceToken({
  run,
  scan,
  data,
  symbol,
  now = Date.now(),
} = {}) {
  if (!data || data.ok !== true || data.mode !== "swisser_gpt_snapshot") {
    fail("snapshot response is unsuccessful or has the wrong mode");
  }
  const normalized = uniqueSymbols([symbol])[0];
  if (data.symbol !== normalized) fail("snapshot returned another symbol");
  if (!scan.requested_symbols.includes(normalized)) {
    fail("snapshot symbol was not part of the current scanner run");
  }
  const sourceMs = assertFreshSource(data, run, now, `snapshot ${normalized}`);
  if (sourceMs < scan.source_ms) {
    fail(`snapshot ${normalized} is older than the current scanner`);
  }

  const payload = {
    v: TOKEN_VERSION,
    type: "snapshot",
    run_id: run.run_id,
    mode: run.mode,
    symbol: normalized,
    source_ms: sourceMs,
    summary: summarizeMarketItem(data),
    iat: now,
    exp: run.exp,
  };
  return { token: encodeToken(payload), payload };
}

export function verifySnapshotEvidenceToken(
  token,
  { run, scan, now = Date.now() } = {},
) {
  const payload = decodeToken(token, "snapshot", now);
  if (payload.run_id !== run.run_id || payload.mode !== run.mode) {
    fail("snapshot evidence belongs to another run");
  }
  if (!scan.requested_symbols.includes(payload.symbol)) {
    fail("snapshot symbol was not part of the current scanner run");
  }
  if (payload.source_ms < scan.source_ms) {
    fail(`snapshot ${payload.symbol} is older than the current scanner`);
  }
  return payload;
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

function cutTime(scan, snapshots) {
  const times = [scan.source_ms, ...snapshots.map((item) => item.source_ms)].sort(
    (a, b) => a - b,
  );
  const first = moscowTime(times[0]);
  const last = moscowTime(times[times.length - 1]);
  return `${first === last ? first : `${first}–${last}`} МСК`;
}

function directionFamily(value) {
  return String(value || "").toLowerCase().includes("short") ? "Short" : "Long";
}

export function buildVerifiedCard({
  runToken,
  scanEvidenceToken,
  snapshotEvidenceTokens = [],
  mode,
  lead,
  marketRows,
  candidates = [],
  conclusion,
  session = null,
  now = Date.now(),
} = {}) {
  const canonical = canonicalMode(mode);
  const run = verifyRunToken(runToken, { mode: canonical, session, now });
  const scan = verifyScanEvidenceToken(scanEvidenceToken, { run, now });
  const snapshots = snapshotEvidenceTokens.map((token) =>
    verifySnapshotEvidenceToken(token, { run, scan, now }),
  );
  const snapshotsBySymbol = new Map();
  for (const snapshot of snapshots) {
    if (snapshotsBySymbol.has(snapshot.symbol)) fail(`duplicate snapshot ${snapshot.symbol}`);
    snapshotsBySymbol.set(snapshot.symbol, snapshot);
  }

  const inputRows = Array.isArray(marketRows) ? marketRows : [];
  const rowSymbols = inputRows.map((row) => fullSymbol(row.symbol));
  if (new Set(rowSymbols).size !== rowSymbols.length) fail("duplicate market row");

  const scannedTradeSymbols = scan.requested_symbols.filter((symbol) => symbol !== BTC_SYMBOL);
  if (canonical === "overview" || canonical === "setups") {
    assertSameSet(scannedTradeSymbols, TRADE_SYMBOLS, `${canonical} scanner`);
    assertSameSet(rowSymbols, TRADE_SYMBOLS, `${canonical} card`);
  } else {
    if (!scannedTradeSymbols.length) fail("entry run has no saved candidates");
    if (run.expected_symbols.length) {
      assertSameSet(scannedTradeSymbols, run.expected_symbols, "entry scanner");
    }
    assertSameSet(rowSymbols, scannedTradeSymbols, "entry card");
    for (const symbol of rowSymbols) {
      if (!snapshotsBySymbol.has(symbol)) fail(`entry row ${symbol} has no fresh snapshot`);
    }
  }

  if (canonical === "overview") {
    if (snapshots.length) fail("overview must not use snapshots");
    if (candidates.length) fail("overview must not contain candidates");
  }
  if (canonical === "setups") {
    for (const row of inputRows) {
      const symbol = fullSymbol(row.symbol);
      if (["top", "secondary"].includes(row.priority) && !snapshotsBySymbol.has(symbol)) {
        fail(`ranked setup ${symbol} has no fresh snapshot`);
      }
    }
  }

  const inputRowsBySymbol = new Map(
    inputRows.map((row) => [fullSymbol(row.symbol), row]),
  );
  const orderedSymbols =
    canonical === "overview" ? TRADE_SYMBOLS : rowSymbols;
  const verifiedRows = orderedSymbols.map((symbol) => {
    const input = inputRowsBySymbol.get(symbol);
    const summary = snapshotsBySymbol.get(symbol)?.summary || scan.summaries[symbol];
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
    if (!snapshotsBySymbol.has(symbol)) fail(`candidate ${symbol} has no fresh snapshot`);
    if (candidate.targets.length !== candidate.pnl_6x.length) {
      fail(`candidate ${symbol} has unmatched targets and PnL values`);
    }
    const allowed = snapshotsBySymbol.get(symbol).summary.allowed_directions || [];
    const family = directionFamily(candidate.direction);
    if (allowed.length && !allowed.includes(family)) {
      fail(`candidate ${symbol} direction conflicts with current evidence`);
    }
  }

  const btc = scan.summaries[BTC_SYMBOL];
  if (!btc) fail("current scanner has no BTC context");
  return {
    mode: canonical,
    cut_time: cutTime(scan, snapshots),
    btc_price: formatPrice(btc.price),
    btc_structure: { h4: btc.h4, h1: btc.h1, m15: btc.m15, m1: btc.m1 },
    lead,
    market_rows: verifiedRows,
    candidates,
    conclusion,
    source_integrity: {
      verified: true,
      run_id: run.run_id,
      scan_fetched_at_unix: Math.round(scan.source_ms / 1000),
      snapshot_fetched_at_unix: Object.fromEntries(
        snapshots.map((item) => [item.symbol, Math.round(item.source_ms / 1000)]),
      ),
    },
  };
}
