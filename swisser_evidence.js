import { createHmac, randomUUID } from "node:crypto";

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

// Five minutes is long enough for ChatGPT reasoning while keeping a 1m entry cut fresh.
export const WORKFLOW_TTL_MS = 300_000;

const STATE_VERSION = 3;
const SOURCE_AGE_TOLERANCE_MS = 90_000;
const FUTURE_CLOCK_TOLERANCE_MS = 15_000;
const SESSION_SECRET =
  process.env.SWISSER_SESSION_SECRET ??
  "swisser-session-binding-v3-not-an-authentication-secret";

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

function sessionBinding(session) {
  if (!session) return null;
  return createHmac("sha256", SESSION_SECRET)
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
  const activeDirection = tradeDirection(active.direction);
  const pendingDirection = tradeDirection(
    active.potential_local_direction ||
    active.potential_continuation_direction ||
    continuation.direction,
  );
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
    active_direction: activeDirection,
    pending_direction: pendingDirection,
    trade_ready: active.trade_ready === true,
    scenario_kind: active.kind || null,
    scenario_status: active.status || null,
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

export function createScanWorkflowState({
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
    v: STATE_VERSION,
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
    exp: sourceMs + WORKFLOW_TTL_MS,
  };
  return payload;
}

export function verifyWorkflowPayload(
  payload,
  { mode, stage = null, session = null, now = Date.now() } = {},
) {
  if (!payload || payload.v !== STATE_VERSION || payload.type !== "workflow") {
    fail("expected current workflow state");
  }
  if (!Number.isFinite(payload.iat) || !Number.isFinite(payload.exp)) {
    fail("workflow state has no valid lifetime");
  }
  if (payload.iat > now + FUTURE_CLOCK_TOLERANCE_MS) {
    fail("workflow state is from the future");
  }
  if (payload.exp < now) {
    fail("workflow state expired; collect one new market cut");
  }
  assertSession(payload, session);
  if (mode && payload.mode !== canonicalMode(mode)) fail("workflow mode mismatch");
  const allowedStages = stage == null ? null : Array.isArray(stage) ? stage : [stage];
  if (allowedStages && !allowedStages.includes(payload.stage)) {
    fail(`workflow stage ${payload.stage} cannot be used here`);
  }
  return payload;
}

export function createSnapshotBundleState({
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

function guardedEntryStatus(summary, candidateDirection, requestedStatus = "wait") {
  const family = directionFamily(candidateDirection);
  if (summary.active_direction && summary.active_direction !== family) {
    return "cancelled";
  }
  if (requestedStatus === "cancelled") return "cancelled";
  if (
    requestedStatus === "confirmed" &&
    summary.trade_ready === true &&
    summary.active_direction === family
  ) {
    return "confirmed";
  }
  return "wait";
}

function entryStatusLabel(status) {
  if (status === "confirmed") return "ВХОД ПОДТВЕРЖДЁН";
  if (status === "cancelled") return "ОТМЕНА";
  return "ЖДАТЬ";
}

function coreStack(summary) {
  return `1h/15m/1m: ${summary.h1}/${summary.m15}/${summary.m1}`;
}

function scenarioDescription(summary) {
  if (summary.scenario_kind === "CORE_CONTINUATION") return "ядро согласовано";
  if (summary.scenario_kind === "PULLBACK_IN_PROGRESS") {
    return `идёт 1m-откат; ожидаемое продолжение ${summary.pending_direction || "не определено"}`;
  }
  if (summary.scenario_kind === "LOCAL_COUNTER_1H") {
    return `${summary.active_direction || "локальный сценарий"} против 1h`;
  }
  if (summary.scenario_kind === "LOCAL_COUNTER_1H_WAITING_CONFIRMATION") {
    return `${summary.pending_direction || "локальный сценарий"} против 1h ещё не подтверждён`;
  }
  if (summary.scenario_kind === "INSUFFICIENT_DATA") return "недостаточно структурных данных";
  return "ядро конфликтует";
}

function authoritativeStatusReason(summary, status, candidateDirection) {
  const family = directionFamily(candidateDirection);
  if (status === "confirmed") {
    return `${coreStack(summary)}; сервер подтвердил активный ${family} и свежий 1m CHoCH/BOS.`;
  }
  if (status === "cancelled") {
    return `${coreStack(summary)}; активное направление ${summary.active_direction} противоположно сохранённому ${family}.`;
  }
  if (summary.scenario_kind === "PULLBACK_IN_PROGRESS") {
    return `${coreStack(summary)}; 1m-откат не завершён, нужен свежий ${summary.pending_direction || family} CHoCH/BOS.`;
  }
  if (summary.scenario_kind === "LOCAL_COUNTER_1H_WAITING_CONFIRMATION") {
    return `${coreStack(summary)}; локальный ${summary.pending_direction || family} против 1h ещё не получил свежий 1m CHoCH/BOS.`;
  }
  if (!summary.active_direction || summary.idea === "Wait") {
    return `${coreStack(summary)}; ядро конфликтует, поэтому вход не подтверждён.`;
  }
  if (summary.trade_ready === true) {
    return `${coreStack(summary)}; структурный 1m-фильтр пройден, но итоговая проверка места, пространства и стопа оставила статус ЖДАТЬ.`;
  }
  return `${coreStack(summary)}; сценарий ${summary.active_direction} сохраняется, но свежий входной триггер пока не подтверждён.`;
}

function authoritativeMarketNote(summary, status, candidateDirection, hasCandidate) {
  if (!hasCandidate) {
    const idea = summary.idea === "—" ? "не определена" : summary.idea;
    const readiness = summary.trade_ready
      ? "структурный 1m-фильтр пройден"
      : "структурный 1m-фильтр не пройден";
    return `${coreStack(summary)}; ${scenarioDescription(summary)}; активная идея: ${idea}; ${readiness}.`;
  }
  return `${entryStatusLabel(status)}. ${authoritativeStatusReason(summary, status, candidateDirection)}`;
}

function groupedStatuses(candidates) {
  const groups = { confirmed: [], wait: [], cancelled: [] };
  for (const candidate of candidates) {
    groups[candidate.entry_status].push(shortSymbol(fullSymbol(candidate.symbol)));
  }
  return groups;
}

function authoritativeCardSummary(mode, rows, candidates) {
  if (mode === "overview") {
    const byIdea = { Long: [], Short: [], Wait: [], Other: [] };
    for (const row of rows) {
      const bucket = row.idea.includes("Long")
        ? byIdea.Long
        : row.idea.includes("Short")
        ? byIdea.Short
        : row.idea === "Wait"
        ? byIdea.Wait
        : byIdea.Other;
      bucket.push(row.symbol);
    }
    const parts = [];
    if (byIdea.Long.length) parts.push(`Long: ${byIdea.Long.join(", ")}`);
    if (byIdea.Short.length) parts.push(`Short: ${byIdea.Short.join(", ")}`);
    if (byIdea.Wait.length) parts.push(`Wait: ${byIdea.Wait.join(", ")}`);
    if (byIdea.Other.length) parts.push(`Без идеи: ${byIdea.Other.join(", ")}`);
    return {
      lead: "Нейтральный серверный срез без ранжирования.",
      conclusion: parts.join("; ") + ".",
    };
  }

  const groups = groupedStatuses(candidates);
  const lead = candidates.length
    ? `Кандидаты: ${candidates.map((item) => item.symbol).join(", ")}.`
    : "Конкурентных кандидатов сейчас нет.";
  const parts = [];
  if (groups.confirmed.length) parts.push(`ВХОД ПОДТВЕРЖДЁН: ${groups.confirmed.join(", ")}`);
  if (groups.wait.length) parts.push(`ЖДАТЬ: ${groups.wait.join(", ")}`);
  if (groups.cancelled.length) parts.push(`ОТМЕНА: ${groups.cancelled.join(", ")}`);
  return {
    lead,
    conclusion: parts.length ? parts.join("; ") + "." : "Подтверждённых входов сейчас нет.",
  };
}

export function buildVerifiedCard({
  workflow: suppliedWorkflow,
  mode,
  marketRows,
  candidates = [],
  session = null,
  now = Date.now(),
} = {}) {
  const canonical = canonicalMode(mode);
  const workflow = verifyWorkflowPayload(suppliedWorkflow, {
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
  const candidateSymbols = new Set();
  const verifiedCandidates = [];
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
    if (canonical !== "entry" && allowed.length && !allowed.includes(family)) {
      fail(`candidate ${symbol} direction conflicts with current evidence`);
    }
    const summary = snapshots[symbol].summary;
    const status = guardedEntryStatus(
      summary,
      candidate.direction,
      candidate.status || "wait",
    );
    const reason = authoritativeStatusReason(summary, status, candidate.direction);
    verifiedCandidates.push({
      ...candidate,
      entry_status: status,
      status_label: entryStatusLabel(status),
      entry_condition: reason,
      entry: candidate.entry,
      stop_or_invalidation: candidate.stop_or_invalidation,
      targets: candidate.targets,
      pnl_6x: candidate.pnl_6x,
    });
  }

  const candidatesBySymbol = new Map(
    verifiedCandidates.map((candidate) => [fullSymbol(candidate.symbol), candidate]),
  );
  const orderedSymbols = canonical === "overview" ? TRADE_SYMBOLS : rowSymbols;
  const verifiedRows = orderedSymbols.map((symbol) => {
    const input = inputRowsBySymbol.get(symbol);
    const summary = snapshots[symbol]?.summary || workflow.scan.summaries[symbol];
    if (!summary) fail(`no current evidence for ${symbol}`);
    const candidate = candidatesBySymbol.get(symbol);
    const fallbackDirection = summary.active_direction || "Long";
    const status = candidate?.entry_status || "wait";
    return {
      symbol: shortSymbol(symbol),
      price: formatPrice(summary.price),
      idea: summary.idea,
      h4: summary.h4,
      h1: summary.h1,
      m15: summary.m15,
      m1: summary.m1,
      priority: canonical === "setups" ? input.priority || "none" : "none",
      note: authoritativeMarketNote(
        summary,
        status,
        candidate?.direction || fallbackDirection,
        Boolean(candidate),
      ),
    };
  });

  const authoritative = authoritativeCardSummary(canonical, verifiedRows, verifiedCandidates);

  const btc = workflow.scan.summaries[BTC_SYMBOL];
  if (!btc) fail("current scanner has no BTC context");
  return {
    mode: canonical,
    cut_time: cutTime(workflow),
    btc_price: formatPrice(btc.price),
    btc_structure: { h4: btc.h4, h1: btc.h1, m15: btc.m15, m1: btc.m1 },
    lead: authoritative.lead,
    market_rows: verifiedRows,
    candidates: verifiedCandidates,
    conclusion: authoritative.conclusion,
  };
}
