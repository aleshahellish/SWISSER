import assert from "node:assert/strict";
import test from "node:test";

import {
  BTC_SYMBOL,
  TRADE_SYMBOLS,
  WORKFLOW_TTL_MS,
  buildVerifiedCard,
  createScanWorkflowState,
  createSnapshotBundleState,
  verifyWorkflowPayload,
} from "../swisser_evidence.js";

const NOW = Date.parse("2026-08-24T01:05:00.000Z");

function hierarchy(direction = "BULLISH") {
  const trade = direction === "BEARISH" ? "SHORT" : "LONG";
  return {
    higher_timeframe_bias: { direction },
    session_timeframe_bias: { direction },
    setup_timeframe_bias: { direction },
    entry_timeframe_bias: { direction },
    continuation_bias: { direction: trade },
    active_trade_scenario: {
      direction: trade,
      label: trade,
      kind: "CORE_CONTINUATION",
      status: "READY",
      trade_ready: true,
    },
    trade_direction_preference: { direction: trade },
  };
}

function marketItem(symbol, price, direction = "BULLISH") {
  return {
    ok: true,
    symbol,
    current_price: price,
    mtf_hierarchy: hierarchy(direction),
  };
}

function scanData(symbols, sourceMs, directions = {}) {
  return {
    ok: true,
    mode: "swisser_gpt_scan",
    fetched_at_unix: sourceMs / 1000,
    requested_symbols: symbols,
    results: symbols.map((symbol, index) =>
      marketItem(symbol, symbol === BTC_SYMBOL ? 63_450 : 100 + index, directions[symbol]),
    ),
  };
}

function snapshotData(symbol, sourceMs, direction = "BULLISH") {
  return {
    ...marketItem(symbol, 123.45, direction),
    mode: "swisser_gpt_snapshot",
    fetched_at_unix: sourceMs / 1000,
  };
}

function rows(symbols = TRADE_SYMBOLS) {
  return symbols.map((symbol) => ({
    symbol: symbol.replace("_USDT", ""),
    priority: "none",
    note: `Свежая проверка ${symbol}`,
    price: "999999",
    h1: "Bear",
    m15: "Bear",
    m1: "Bear",
  }));
}

function candidate(symbol = "SOL", overrides = {}) {
  return {
    symbol,
    direction: "Long",
    status: "confirmed",
    entry_condition: "После свежего 1m CHoCH",
    entry: "после триггера",
    stop_or_invalidation: "ниже локального low",
    targets: ["TP1", "TP2"],
    pnl_6x: ["+3%", "+6%"],
    ...overrides,
  };
}

function makeScan({
  mode = "setups",
  expectedSymbols = [],
  sourceMs = NOW + 1_000,
  directions = {},
  session = null,
  runId,
} = {}) {
  const symbols = mode === "entry"
    ? [...expectedSymbols, BTC_SYMBOL]
    : [...TRADE_SYMBOLS, BTC_SYMBOL];
  return createScanWorkflowState({
    mode,
    expectedSymbols,
    session,
    data: scanData(symbols, sourceMs, directions),
    requestedSymbols: symbols,
    now: sourceMs + 100,
    runId,
  });
}

function makeBundle(scan, symbols, {
  sourceMs = NOW + 2_000,
  directions = {},
} = {}) {
  return createSnapshotBundleState({
    workflow: scan,
    symbols,
    dataBySymbol: new Map(
      symbols.map((symbol) => [
        symbol,
        snapshotData(symbol, sourceMs, directions[symbol]),
      ]),
    ),
    now: sourceMs + 100,
  });
}

test("overview card takes price, structure and cut time only from server workflow state", () => {
  const scan = makeScan({ mode: "overview" });
  const card = buildVerifiedCard({
    workflow: scan,
    mode: "overview",
    lead: "Нейтральный обзор",
    marketRows: rows(),
    candidates: [],
    conclusion: "Срез проверен",
    now: NOW + 3_000,
  });

  assert.equal("source_integrity" in card, false);
  assert.equal(card.market_rows[0].price, "100.00");
  assert.equal(card.market_rows[0].h1, "Bull");
  assert.notEqual(card.market_rows[0].price, "999999");
  assert.match(card.cut_time, /04:05:01 МСК/);
  assert.equal(card.btc_price, "63,450.00");
});

test("scanner creation rejects stale, incomplete and unsuccessful cuts", () => {
  const symbols = [...TRADE_SYMBOLS, BTC_SYMBOL];
  assert.throws(
    () => createScanWorkflowState({
      mode: "overview",
      data: scanData(symbols, NOW - 100_000),
      requestedSymbols: symbols,
      now: NOW,
    }),
    /not a fresh market cut/,
  );

  const incomplete = scanData(symbols, NOW);
  incomplete.results.pop();
  assert.throws(
    () => createScanWorkflowState({
      mode: "overview",
      data: incomplete,
      requestedSymbols: symbols,
      now: NOW,
    }),
    /missing or duplicate rows/,
  );

  const unsuccessful = scanData(symbols, NOW);
  unsuccessful.results[0].ok = false;
  assert.throws(
    () => createScanWorkflowState({
      mode: "overview",
      data: unsuccessful,
      requestedSymbols: symbols,
      now: NOW,
    }),
    /market row TAO_USDT is unsuccessful/,
  );
});

test("mode fixes the scanner symbol set and entry requires saved candidates", () => {
  const all = [...TRADE_SYMBOLS, BTC_SYMBOL];
  assert.throws(
    () => createScanWorkflowState({
      mode: "setups",
      expectedSymbols: ["SOL_USDT"],
      data: scanData(all, NOW),
      requestedSymbols: all,
      now: NOW,
    }),
    /expected symbols are only valid for entry/,
  );
  assert.throws(
    () => createScanWorkflowState({
      mode: "entry",
      expectedSymbols: [],
      data: scanData([BTC_SYMBOL], NOW),
      requestedSymbols: [BTC_SYMBOL],
      now: NOW,
    }),
    /entry has no saved candidates/,
  );
});

test("one server workflow has a five-minute evidence window", () => {
  const scan = makeScan();
  assert.equal(scan.exp, NOW + 1_000 + WORKFLOW_TTL_MS);
  assert.equal(
    verifyWorkflowPayload(scan, { now: scan.exp }).stage,
    "scan",
  );
  assert.throws(
    () => verifyWorkflowPayload(scan, { now: scan.exp + 1 }),
    /workflow state expired/,
  );
});

test("session binding remains strict without a model-carried signature", () => {
  const scan = makeScan({ mode: "overview", session: "session-a" });
  assert.throws(
    () => verifyWorkflowPayload(scan, {
      session: "session-b",
      now: NOW + 2_000,
    }),
    /another ChatGPT session/,
  );
  assert.equal(
    verifyWorkflowPayload(scan, {
      session: "session-a",
      now: NOW + 2_000,
    }).mode,
    "overview",
  );
});

test("all candidate snapshots become one internally consistent server bundle", () => {
  const scan = makeScan({ runId: "one-run" });
  const bundle = makeBundle(scan, ["SOL_USDT", "DOGE_USDT"]);
  const verified = verifyWorkflowPayload(bundle, {
    stage: "bundle",
    now: NOW + 3_000,
  });

  assert.equal(verified.run_id, "one-run");
  assert.deepEqual(Object.keys(verified.snapshots), ["SOL_USDT", "DOGE_USDT"]);
  assert.equal(verified.scan.source_ms, scan.scan.source_ms);
  assert.throws(
    () => createSnapshotBundleState({
      workflow: bundle,
      symbols: ["SOL_USDT"],
      dataBySymbol: new Map([
        ["SOL_USDT", snapshotData("SOL_USDT", NOW + 3_000)],
      ]),
      now: NOW + 3_100,
    }),
    /requires one current scanner workflow/,
  );
});

test("snapshot bundle rejects stale data before it can replace scanner evidence", () => {
  const scan = makeScan({ sourceMs: NOW + 5_000 });
  assert.throws(
    () => makeBundle(scan, ["SOL_USDT"], { sourceMs: NOW + 4_000 }),
    /older than the current scanner/,
  );
});

test("setups candidate and ranked tier require a snapshot in the same bundle", () => {
  const scan = makeScan();
  assert.throws(
    () => buildVerifiedCard({
      workflow: scan,
      mode: "setups",
      lead: "Сетапы",
      marketRows: rows(),
      candidates: [candidate()],
      conclusion: "Проверка",
      now: NOW + 3_000,
    }),
    /candidate SOL_USDT has no fresh snapshot/,
  );

  const rankedRows = rows();
  rankedRows.find((row) => row.symbol === "SOL").priority = "top";
  assert.throws(
    () => buildVerifiedCard({
      workflow: scan,
      mode: "setups",
      lead: "Сетапы",
      marketRows: rankedRows,
      candidates: [],
      conclusion: "Проверка",
      now: NOW + 3_000,
    }),
    /ranked setup SOL_USDT has no fresh snapshot/,
  );
});

test("renderer validates target/PnL pairs and candidate direction", () => {
  const scan = makeScan({ directions: { SOL_USDT: "BEARISH" } });
  const bundle = makeBundle(scan, ["SOL_USDT"], {
    directions: { SOL_USDT: "BEARISH" },
  });
  assert.throws(
    () => buildVerifiedCard({
      workflow: bundle,
      mode: "setups",
      lead: "Сетапы",
      marketRows: rows(),
      candidates: [candidate("SOL", { pnl_6x: ["+3%"] })],
      conclusion: "Проверка",
      now: NOW + 3_000,
    }),
    /unmatched targets and PnL values/,
  );
  assert.throws(
    () => buildVerifiedCard({
      workflow: bundle,
      mode: "setups",
      lead: "Сетапы",
      marketRows: rows(),
      candidates: [candidate("SOL", { direction: "Long" })],
      conclusion: "Проверка",
      now: NOW + 3_000,
    }),
    /direction conflicts with current evidence/,
  );
});

test("trade_ready is a confirmation gate, not an automatic entry decision", () => {
  const scan = makeScan({ mode: "setups" });
  const bundle = makeBundle(scan, ["SOL_USDT", "HYPE_USDT", "TAO_USDT"]);
  const card = buildVerifiedCard({
    workflow: bundle,
    mode: "setups",
    marketRows: rows(),
    candidates: [
      candidate("SOL", { status: "wait" }),
      candidate("HYPE", { status: "cancelled" }),
      candidate("TAO", { status: undefined }),
    ],
    now: NOW + 3_000,
  });

  assert.equal(bundle.snapshots.SOL_USDT.summary.trade_ready, true);
  assert.equal(card.candidates[0].entry_status, "wait");
  assert.equal(card.candidates[1].entry_status, "cancelled");
  assert.equal(card.candidates[2].entry_status, "wait");
  assert.equal(card.conclusion, "ЖДАТЬ: SOL, TAO; ОТМЕНА: HYPE.");
});

test("entry cancels a saved direction when the active scenario has flipped", () => {
  const scan = makeScan({
    mode: "entry",
    expectedSymbols: ["SOL_USDT"],
  });
  const bundle = makeBundle(scan, ["SOL_USDT"]);
  const card = buildVerifiedCard({
    workflow: bundle,
    mode: "entry",
    marketRows: rows(["SOL_USDT"]),
    candidates: [candidate("SOL", { direction: "Short", status: "confirmed" })],
    now: NOW + 3_000,
  });

  assert.equal(card.market_rows[0].idea, "Long");
  assert.equal(card.candidates[0].entry_status, "cancelled");
  assert.match(card.candidates[0].entry_condition, /противоположно сохранённому Short/);
  assert.equal(card.conclusion, "ОТМЕНА: SOL.");
});

test("renderer keeps every mode consistent with a Bear/Bull/Bear DOGE snapshot", () => {
  const falseNarrative = "ВХОД ПОДТВЕРЖДЁН: все таймфреймы bearish";
  const dogeWait = (summary) => ({
    ...summary,
    h4: "Bear",
    h1: "Bear",
    m15: "Bull",
    m1: "Bear",
    idea: "Wait",
    active_direction: null,
    trade_ready: false,
    scenario_kind: "CONFLICT",
    scenario_status: "WAIT",
    allowed_directions: [],
  });

  const overview = makeScan({ mode: "overview", directions: { DOGE_USDT: "BEARISH" } });
  overview.scan.summaries.DOGE_USDT = dogeWait(overview.scan.summaries.DOGE_USDT);
  const overviewCard = buildVerifiedCard({
    workflow: overview,
    mode: "overview",
    lead: falseNarrative,
    marketRows: rows().map((row) => ({ ...row, note: falseNarrative })),
    conclusion: falseNarrative,
    now: NOW + 3_000,
  });
  const overviewDoge = overviewCard.market_rows.find((row) => row.symbol === "DOGE");
  assert.equal(overviewDoge.m15, "Bull");
  assert.equal(overviewDoge.idea, "Wait");
  assert.match(overviewDoge.note, /1h\/15m\/1m: Bear\/Bull\/Bear/);
  assert.doesNotMatch(overviewDoge.note, /все таймфреймы bearish/i);

  const setups = makeScan({ mode: "setups", directions: { DOGE_USDT: "BEARISH" } });
  const setupsBundle = makeBundle(setups, ["DOGE_USDT"], {
    directions: { DOGE_USDT: "BEARISH" },
  });
  setupsBundle.snapshots.DOGE_USDT.summary = dogeWait(
    setupsBundle.snapshots.DOGE_USDT.summary,
  );
  const setupsCard = buildVerifiedCard({
    workflow: setupsBundle,
    mode: "setups",
    lead: falseNarrative,
    marketRows: rows().map((row) => ({ ...row, note: falseNarrative })),
    candidates: [candidate("DOGE", {
      direction: "Short",
      entry_condition: falseNarrative,
    })],
    conclusion: falseNarrative,
    now: NOW + 3_000,
  });
  const setupsDoge = setupsCard.market_rows.find((row) => row.symbol === "DOGE");
  assert.equal(setupsDoge.note.startsWith("ЖДАТЬ."), true);
  assert.equal(setupsCard.candidates[0].entry_status, "wait");
  assert.equal(setupsCard.candidates[0].status_label, "ЖДАТЬ");
  assert.match(setupsCard.candidates[0].entry_condition, /ядро конфликтует/);
  assert.doesNotMatch(setupsCard.candidates[0].entry_condition, /все таймфреймы bearish/i);
  assert.equal(setupsCard.conclusion, "ЖДАТЬ: DOGE.");

  const entry = makeScan({
    mode: "entry",
    expectedSymbols: ["DOGE_USDT"],
    directions: { DOGE_USDT: "BEARISH" },
  });
  const entryBundle = makeBundle(entry, ["DOGE_USDT"], {
    directions: { DOGE_USDT: "BEARISH" },
  });
  entryBundle.snapshots.DOGE_USDT.summary = dogeWait(entryBundle.snapshots.DOGE_USDT.summary);
  const entryCard = buildVerifiedCard({
    workflow: entryBundle,
    mode: "entry",
    lead: falseNarrative,
    marketRows: rows(["DOGE_USDT"]).map((row) => ({ ...row, note: falseNarrative })),
    candidates: [candidate("DOGE", {
      direction: "Short",
      entry_condition: falseNarrative,
    })],
    conclusion: falseNarrative,
    now: NOW + 3_000,
  });
  assert.equal(entryCard.market_rows[0].idea, "Wait");
  assert.equal(entryCard.market_rows[0].m15, "Bull");
  assert.equal(entryCard.candidates[0].entry_status, "wait");
  assert.equal(entryCard.lead, "Кандидаты: DOGE.");
  assert.equal(entryCard.conclusion, "ЖДАТЬ: DOGE.");
});

test("verified cards do not expose internal recovery or integrity flags", () => {
  const scan = makeScan({ mode: "setups" });
  const bundle = makeBundle(scan, ["SOL_USDT"]);
  const card = buildVerifiedCard({
    workflow: bundle,
    mode: "setups",
    marketRows: rows(),
    candidates: [candidate("SOL")],
    now: NOW + 3_000,
  });

  assert.equal("source_integrity" in card, false);
  assert.equal("state_recovered" in card, false);
  assert.equal(card.market_rows[2].priority, "none");
  assert.equal(card.candidates[0].entry_status, "confirmed");
  assert.equal(card.candidates[0].entry, "после триггера");
  assert.deepEqual(card.candidates[0].targets, ["TP1", "TP2"]);
  assert.doesNotMatch(card.lead, /восстановлен|recovered/i);
});

test("entry bundle atomically requires every saved candidate and forbids additions", () => {
  const expected = ["SOL_USDT", "DOGE_USDT"];
  const scan = makeScan({ mode: "entry", expectedSymbols: expected });
  assert.throws(
    () => makeBundle(scan, ["SOL_USDT"]),
    /entry snapshot bundle symbols do not match/,
  );

  const bundle = makeBundle(scan, expected);
  const card = buildVerifiedCard({
    workflow: bundle,
    mode: "entry",
    lead: "Проверка входа",
    marketRows: rows(expected),
    candidates: [],
    conclusion: "Проверка",
    now: NOW + 3_000,
  });
  assert.equal(card.market_rows.length, 2);

  assert.throws(
    () => buildVerifiedCard({
      workflow: bundle,
      mode: "entry",
      lead: "Проверка входа",
      marketRows: rows([...expected, "ETH_USDT"]),
      candidates: [],
      conclusion: "Проверка",
      now: NOW + 3_000,
    }),
    /entry card symbols do not match/,
  );
});

test("states from independent retries cannot be partially combined", () => {
  const first = makeScan({ runId: "first-run" });
  const second = makeScan({ runId: "second-run", sourceMs: NOW + 2_000 });
  const firstBundle = makeBundle(first, ["SOL_USDT"], { sourceMs: NOW + 3_000 });
  const secondBundle = makeBundle(second, ["DOGE_USDT"], { sourceMs: NOW + 3_000 });

  assert.equal(
    verifyWorkflowPayload(firstBundle, { now: NOW + 4_000 }).run_id,
    "first-run",
  );
  assert.equal(
    verifyWorkflowPayload(secondBundle, { now: NOW + 4_000 }).run_id,
    "second-run",
  );
  assert.equal(Object.keys(firstBundle.snapshots).length, 1);
  assert.equal(Object.keys(secondBundle.snapshots).length, 1);
  assert.equal("run_token" in firstBundle, false);
  assert.equal("scan_evidence_token" in firstBundle, false);
  assert.equal("snapshot_evidence_tokens" in firstBundle, false);
});
