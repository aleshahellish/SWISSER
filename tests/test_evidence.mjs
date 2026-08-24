import assert from "node:assert/strict";
import test from "node:test";

import {
  BTC_SYMBOL,
  EVIDENCE_TTL_MS,
  TRADE_SYMBOLS,
  buildVerifiedCard,
  createScanWorkflowEvidence,
  createSnapshotBundleEvidence,
  verifyWorkflowEvidenceToken,
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
  return createScanWorkflowEvidence({
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
  return createSnapshotBundleEvidence({
    workflow: scan.payload,
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

test("overview card takes price, structure and cut time only from atomic scan evidence", () => {
  const scan = makeScan({ mode: "overview" });
  const card = buildVerifiedCard({
    evidenceToken: scan.token,
    mode: "overview",
    lead: "Нейтральный обзор",
    marketRows: rows(),
    candidates: [],
    conclusion: "Срез проверен",
    now: NOW + 3_000,
  });

  assert.equal(card.source_integrity.verified, true);
  assert.equal(card.source_integrity.protocol, "atomic-v2");
  assert.equal(card.market_rows[0].price, "100.00");
  assert.equal(card.market_rows[0].h1, "Bull");
  assert.notEqual(card.market_rows[0].price, "999999");
  assert.match(card.cut_time, /04:05:01 МСК/);
  assert.equal(card.btc_price, "63,450.00");
});

test("scanner creation rejects stale, incomplete and unsuccessful cuts", () => {
  const symbols = [...TRADE_SYMBOLS, BTC_SYMBOL];
  assert.throws(
    () => createScanWorkflowEvidence({
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
    () => createScanWorkflowEvidence({
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
    () => createScanWorkflowEvidence({
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
    () => createScanWorkflowEvidence({
      mode: "setups",
      expectedSymbols: ["SOL_USDT"],
      data: scanData(all, NOW),
      requestedSymbols: all,
      now: NOW,
    }),
    /expected symbols are only valid for entry/,
  );
  assert.throws(
    () => createScanWorkflowEvidence({
      mode: "entry",
      expectedSymbols: [],
      data: scanData([BTC_SYMBOL], NOW),
      requestedSymbols: [BTC_SYMBOL],
      now: NOW,
    }),
    /entry has no saved candidates/,
  );
});

test("one workflow token has a five-minute evidence window", () => {
  const scan = makeScan();
  assert.equal(scan.payload.exp, NOW + 1_000 + EVIDENCE_TTL_MS);
  assert.equal(
    verifyWorkflowEvidenceToken(scan.token, { now: scan.payload.exp }).stage,
    "scan",
  );
  assert.throws(
    () => verifyWorkflowEvidenceToken(scan.token, { now: scan.payload.exp + 1 }),
    /workflow evidence expired/,
  );
});

test("session binding and token signature remain strict", () => {
  const scan = makeScan({ mode: "overview", session: "session-a" });
  assert.match(scan.token, /^z2\./);
  assert.throws(
    () => verifyWorkflowEvidenceToken(scan.token, {
      session: "session-b",
      now: NOW + 2_000,
    }),
    /another ChatGPT session/,
  );
  assert.equal(
    verifyWorkflowEvidenceToken(scan.token, {
      session: "session-a",
      now: NOW + 2_000,
    }).mode,
    "overview",
  );

  const last = scan.token.at(-1);
  const corrupted = `${scan.token.slice(0, -1)}${last === "A" ? "B" : "A"}`;
  assert.throws(
    () => verifyWorkflowEvidenceToken(corrupted, { now: NOW + 2_000 }),
    /invalid workflow token signature/,
  );
  assert.throws(
    () => verifyWorkflowEvidenceToken("z1.old.signature", { now: NOW + 2_000 }),
    /obsolete evidence token/,
  );
});

test("all candidate snapshots become one internally consistent bundle token", () => {
  const scan = makeScan({ runId: "one-run" });
  const bundle = makeBundle(scan, ["SOL_USDT", "DOGE_USDT"]);
  const verified = verifyWorkflowEvidenceToken(bundle.token, {
    stage: "bundle",
    now: NOW + 3_000,
  });

  assert.equal(verified.run_id, "one-run");
  assert.deepEqual(Object.keys(verified.snapshots), ["SOL_USDT", "DOGE_USDT"]);
  assert.equal(verified.scan.source_ms, scan.payload.scan.source_ms);
  assert.ok(bundle.token.length < 1_600);
  assert.throws(
    () => createSnapshotBundleEvidence({
      workflow: bundle.payload,
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
      evidenceToken: scan.token,
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
      evidenceToken: scan.token,
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
      evidenceToken: bundle.token,
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
      evidenceToken: bundle.token,
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

test("entry bundle atomically requires every saved candidate and forbids additions", () => {
  const expected = ["SOL_USDT", "DOGE_USDT"];
  const scan = makeScan({ mode: "entry", expectedSymbols: expected });
  assert.throws(
    () => makeBundle(scan, ["SOL_USDT"]),
    /entry snapshot bundle symbols do not match/,
  );

  const bundle = makeBundle(scan, expected);
  const card = buildVerifiedCard({
    evidenceToken: bundle.token,
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
      evidenceToken: bundle.token,
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

test("tokens from independent retries cannot be partially combined", () => {
  const first = makeScan({ runId: "first-run" });
  const second = makeScan({ runId: "second-run", sourceMs: NOW + 2_000 });
  const firstBundle = makeBundle(first, ["SOL_USDT"], { sourceMs: NOW + 3_000 });
  const secondBundle = makeBundle(second, ["DOGE_USDT"], { sourceMs: NOW + 3_000 });

  assert.equal(
    verifyWorkflowEvidenceToken(firstBundle.token, { now: NOW + 4_000 }).run_id,
    "first-run",
  );
  assert.equal(
    verifyWorkflowEvidenceToken(secondBundle.token, { now: NOW + 4_000 }).run_id,
    "second-run",
  );
  assert.equal(Object.keys(firstBundle.payload.snapshots).length, 1);
  assert.equal(Object.keys(secondBundle.payload.snapshots).length, 1);
  assert.equal("run_token" in firstBundle.payload, false);
  assert.equal("scan_evidence_token" in firstBundle.payload, false);
  assert.equal("snapshot_evidence_tokens" in firstBundle.payload, false);
});
