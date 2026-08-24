import assert from "node:assert/strict";
import test from "node:test";

import {
  BTC_SYMBOL,
  RUN_TTL_MS,
  TRADE_SYMBOLS,
  buildVerifiedCard,
  createRunToken,
  createScanEvidenceToken,
  createSnapshotEvidenceToken,
  verifyRunToken,
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
    active_trade_scenario: { direction: trade, label: trade },
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
    entry_condition: "После свежего 1m BOS",
    entry: "после триггера",
    stop_or_invalidation: "ниже локального low",
    targets: ["TP1", "TP2"],
    pnl_6x: ["+3%", "+6%"],
    ...overrides,
  };
}

function makeRun(mode, expectedSymbols = []) {
  return createRunToken({ mode, expectedSymbols, now: NOW });
}

function makeScan(run, symbols, sourceMs = NOW + 1_000, directions = {}) {
  return createScanEvidenceToken({
    run: run.payload,
    data: scanData(symbols, sourceMs, directions),
    requestedSymbols: symbols,
    now: sourceMs + 100,
  });
}

function makeSnapshot(run, scan, symbol, sourceMs = NOW + 2_000, direction = "BULLISH") {
  return createSnapshotEvidenceToken({
    run: run.payload,
    scan: scan.payload,
    data: snapshotData(symbol, sourceMs, direction),
    symbol,
    now: sourceMs + 100,
  });
}

test("button 1 card takes price, structure and cut time only from current scan evidence", () => {
  const run = makeRun("overview");
  const scan = makeScan(run, [...TRADE_SYMBOLS, BTC_SYMBOL]);
  const card = buildVerifiedCard({
    runToken: run.token,
    scanEvidenceToken: scan.token,
    mode: "overview",
    lead: "Нейтральный обзор",
    marketRows: rows(),
    candidates: [],
    conclusion: "Срез проверен",
    now: NOW + 3_000,
  });

  assert.equal(card.source_integrity.verified, true);
  assert.equal(card.market_rows[0].price, "100.00");
  assert.equal(card.market_rows[0].h1, "Bull");
  assert.notEqual(card.market_rows[0].price, "999999");
  assert.match(card.cut_time, /04:05:01 МСК/);
  assert.equal(card.btc_price, "63,450.00");
});

test("button 1 rejects a scan that predates the current run", () => {
  const run = makeRun("overview");
  assert.throws(
    () => makeScan(run, [...TRADE_SYMBOLS, BTC_SYMBOL], NOW - 20_000),
    /scanner predates the current run/,
  );
});

test("button 1 rejects incomplete or unsuccessful scanner rows", () => {
  const run = makeRun("overview");
  const symbols = [...TRADE_SYMBOLS, BTC_SYMBOL];
  const incomplete = scanData(symbols, NOW + 1_000);
  incomplete.results.pop();
  assert.throws(
    () =>
      createScanEvidenceToken({
        run: run.payload,
        data: incomplete,
        requestedSymbols: symbols,
        now: NOW + 2_000,
      }),
    /missing or duplicate rows/,
  );

  const unsuccessful = scanData(symbols, NOW + 1_000);
  unsuccessful.results[0].ok = false;
  assert.throws(
    () =>
      createScanEvidenceToken({
        run: run.payload,
        data: unsuccessful,
        requestedSymbols: symbols,
        now: NOW + 2_000,
      }),
    /market row TAO_USDT is unsuccessful/,
  );
});

test("a run expires and non-entry modes cannot bind candidate symbols", () => {
  assert.throws(
    () => createRunToken({ mode: "setups", expectedSymbols: ["SOL_USDT"], now: NOW }),
    /expected symbols are only valid for an entry run/,
  );
  const run = makeRun("overview");
  const scan = makeScan(run, [...TRADE_SYMBOLS, BTC_SYMBOL]);
  assert.throws(
    () =>
      buildVerifiedCard({
        runToken: run.token,
        scanEvidenceToken: scan.token,
        mode: "overview",
        lead: "Просрочено",
        marketRows: rows(),
        candidates: [],
        conclusion: "Просрочено",
        now: NOW + RUN_TTL_MS + 1,
      }),
    /token expired/,
  );
});

test("a session-bound run cannot be reused by another ChatGPT session", () => {
  const run = createRunToken({ mode: "overview", session: "session-a", now: NOW });
  const payload = JSON.parse(
    Buffer.from(run.token.split(".")[0], "base64url").toString("utf8"),
  );
  assert.notEqual(payload.session, "session-a");
  assert.throws(
    () => verifyRunToken(run.token, { session: "session-b", now: NOW + 1_000 }),
    /another ChatGPT session/,
  );
  assert.equal(
    verifyRunToken(run.token, { session: "session-a", now: NOW + 1_000 }).mode,
    "overview",
  );
});

test("button 2 rejects a snapshot older than its scanner", () => {
  const run = makeRun("setups");
  const scan = makeScan(run, [...TRADE_SYMBOLS, BTC_SYMBOL], NOW + 5_000);
  assert.throws(
    () => makeSnapshot(run, scan, "SOL_USDT", NOW + 4_000),
    /older than the current scanner/,
  );
});

test("button 2 cannot render a candidate without a fresh snapshot", () => {
  const run = makeRun("setups");
  const scan = makeScan(run, [...TRADE_SYMBOLS, BTC_SYMBOL]);
  assert.throws(
    () =>
      buildVerifiedCard({
        runToken: run.token,
        scanEvidenceToken: scan.token,
        mode: "setups",
        lead: "Сетапы",
        marketRows: rows(),
        candidates: [candidate()],
        conclusion: "Проверка",
        now: NOW + 3_000,
      }),
    /candidate SOL_USDT has no fresh snapshot/,
  );
});

test("button 2 cannot award a top tier before a fresh detailed check", () => {
  const run = makeRun("setups");
  const scan = makeScan(run, [...TRADE_SYMBOLS, BTC_SYMBOL]);
  const rankedRows = rows();
  rankedRows.find((row) => row.symbol === "SOL").priority = "top";
  assert.throws(
    () =>
      buildVerifiedCard({
        runToken: run.token,
        scanEvidenceToken: scan.token,
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

test("button 2 rejects mismatched target and PnL counts", () => {
  const run = makeRun("setups");
  const scan = makeScan(run, [...TRADE_SYMBOLS, BTC_SYMBOL]);
  const snapshot = makeSnapshot(run, scan, "SOL_USDT");
  assert.throws(
    () =>
      buildVerifiedCard({
        runToken: run.token,
        scanEvidenceToken: scan.token,
        snapshotEvidenceTokens: [snapshot.token],
        mode: "setups",
        lead: "Сетапы",
        marketRows: rows(),
        candidates: [candidate("SOL", { pnl_6x: ["+3%"] })],
        conclusion: "Проверка",
        now: NOW + 3_000,
      }),
    /unmatched targets and PnL values/,
  );
});

test("candidate direction cannot contradict fresh structural evidence", () => {
  const run = makeRun("setups");
  const scan = makeScan(run, [...TRADE_SYMBOLS, BTC_SYMBOL], NOW + 1_000, {
    SOL_USDT: "BEARISH",
  });
  const snapshot = makeSnapshot(run, scan, "SOL_USDT", NOW + 2_000, "BEARISH");
  assert.throws(
    () =>
      buildVerifiedCard({
        runToken: run.token,
        scanEvidenceToken: scan.token,
        snapshotEvidenceTokens: [snapshot.token],
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

test("button 3 rejects a snapshot from another run", () => {
  const expected = ["SOL_USDT", "DOGE_USDT"];
  const firstRun = makeRun("entry", expected);
  const firstScan = makeScan(firstRun, [...expected, BTC_SYMBOL]);
  const secondRun = createRunToken({
    mode: "entry",
    expectedSymbols: expected,
    now: NOW,
    runId: "another-run",
  });
  const secondScan = makeScan(secondRun, [...expected, BTC_SYMBOL]);
  const foreignSnapshot = makeSnapshot(secondRun, secondScan, "SOL_USDT");

  assert.throws(
    () =>
      buildVerifiedCard({
        runToken: firstRun.token,
        scanEvidenceToken: firstScan.token,
        snapshotEvidenceTokens: [foreignSnapshot.token],
        mode: "entry",
        lead: "Проверка входа",
        marketRows: rows(expected),
        candidates: [],
        conclusion: "Проверка",
        now: NOW + 3_000,
      }),
    /snapshot evidence belongs to another run/,
  );
});

test("button 3 requires every saved row and forbids an added symbol", () => {
  const expected = ["SOL_USDT", "DOGE_USDT"];
  const run = makeRun("entry", expected);
  const scan = makeScan(run, [...expected, BTC_SYMBOL]);
  const sol = makeSnapshot(run, scan, "SOL_USDT");
  const doge = makeSnapshot(run, scan, "DOGE_USDT");

  assert.throws(
    () =>
      buildVerifiedCard({
        runToken: run.token,
        scanEvidenceToken: scan.token,
        snapshotEvidenceTokens: [sol.token],
        mode: "entry",
        lead: "Проверка входа",
        marketRows: rows(expected),
        candidates: [],
        conclusion: "Проверка",
        now: NOW + 3_000,
      }),
    /entry row DOGE_USDT has no fresh snapshot/,
  );

  assert.throws(
    () =>
      buildVerifiedCard({
        runToken: run.token,
        scanEvidenceToken: scan.token,
        snapshotEvidenceTokens: [sol.token, doge.token],
        mode: "entry",
        lead: "Проверка входа",
        marketRows: rows([...expected, "ETH_USDT"]),
        candidates: [],
        conclusion: "Проверка",
        now: NOW + 3_000,
      }),
    /entry card symbols do not match the current run/,
  );
});
