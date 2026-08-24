import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import handler, { COMMAND_LABELS, COMMANDS } from "../api/mcp.js";
import {
  BTC_SYMBOL,
  TRADE_SYMBOLS,
} from "../swisser_evidence.js";

async function startServer() {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  return { server, url: `http://127.0.0.1:${address.port}` };
}

async function rpc(url, id, method, params = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      "mcp-protocol-version": "2025-06-18",
    },
    body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
  });
  assert.equal(response.status, 200);
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("text/event-stream")) {
    const text = await response.text();
    const data = text.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
    return JSON.parse(data);
  }
  return response.json();
}

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

function marketItem(symbol, price) {
  return { ok: true, symbol, current_price: price, mtf_hierarchy: hierarchy() };
}

function scannerData(symbols, fetchedAt) {
  return {
    ok: true,
    mode: "swisser_gpt_scan",
    fetched_at_unix: fetchedAt / 1000,
    requested_symbols: symbols,
    results: symbols.map((symbol, index) =>
      marketItem(symbol, symbol === BTC_SYMBOL ? 63_450 : 100 + index),
    ),
  };
}

function snapshotData(symbol, fetchedAt) {
  return {
    ...marketItem(symbol, 123.45),
    mode: "swisser_gpt_snapshot",
    fetched_at_unix: fetchedAt / 1000,
  };
}

function assertEmbeddedScriptParses(html) {
  const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
  assert.ok(script, "embedded app script is present");
  assert.doesNotThrow(() => new Function(script));
}

test("SWISSER MCP exposes evidence-gated three-stage workflow and compatible UI", async (t) => {
  const { server, url } = await startServer();
  t.after(() => server.close());

  const initialized = await rpc(url, 1, "initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "swisser-test", version: "1.0.0" },
  });
  assert.equal(initialized.result.serverInfo.name, "swisser-market-controls");
  assert.equal(initialized.result.serverInfo.version, "1.8.0");
  assert.match(initialized.result.instructions, /одним scan_swisser_markets/);
  assert.match(initialized.result.instructions, /только один evidence_token/);
  assert.match(initialized.result.instructions, /одним get_swisser_candidate_snapshots/);
  assert.doesNotMatch(
    initialized.result.instructions,
    /start_swisser_run|get_swisser_market_snapshot|run_token|scan_evidence_token|snapshot_evidence_token/,
  );
  assert.match(initialized.result.instructions, /[Бб]ез проверенного свежего evidence renderer обязан отказать/);
  assert.match(initialized.result.instructions, /«3» — entry/);
  assert.match(initialized.result.instructions, /последнего результата «Лучшие сетапы»/);
  assert.match(initialized.result.instructions, /исходный срез и исходный рейтинг/);
  assert.match(initialized.result.instructions, /движение после среза, а не PnL сделки/);

  const tools = await rpc(url, 2, "tools/list");
  assert.deepEqual(tools.result.tools.map((tool) => tool.name), [
    "scan_swisser_markets",
    "get_swisser_candidate_snapshots",
    "render_swisser_market_card",
    "open_swisser_controls",
  ]);
  assert.equal(
    tools.result.tools[3]._meta["openai/outputTemplate"],
    "ui://swisser/market-controls/1.8.0.html",
  );
  assert.equal(
    tools.result.tools[2]._meta["openai/outputTemplate"],
    "ui://swisser/market-card-v6.html",
  );
  const scannerProperties = tools.result.tools[0].inputSchema.properties;
  assert.ok(scannerProperties.mode);
  assert.ok(scannerProperties.expected_symbols);
  assert.equal(scannerProperties.run_token, undefined);
  const snapshotProperties = tools.result.tools[1].inputSchema.properties;
  assert.ok(snapshotProperties.evidence_token);
  assert.ok(snapshotProperties.symbols);
  assert.equal(snapshotProperties.scan_evidence_token, undefined);
  const rendererProperties = tools.result.tools[2].inputSchema.properties;
  assert.equal(rendererProperties.cut_time, undefined);
  assert.equal(rendererProperties.btc_price, undefined);
  assert.equal(rendererProperties.btc_structure, undefined);
  assert.ok(rendererProperties.evidence_token);
  assert.equal(rendererProperties.run_token, undefined);
  assert.equal(rendererProperties.scan_evidence_token, undefined);
  assert.equal(rendererProperties.snapshot_evidence_tokens, undefined);
  const rowProperties = rendererProperties.market_rows.items.properties;
  for (const forbidden of ["price", "idea", "h4", "h1", "m15", "m1"]) {
    assert.equal(rowProperties[forbidden], undefined);
  }

  const controlsCall = await rpc(url, 3, "tools/call", {
    name: "open_swisser_controls",
    arguments: {},
  });
  assert.deepEqual(
    controlsCall.result.structuredContent.commands.map((command) => command.prompt),
    COMMANDS,
  );

  const controlsResource = await rpc(url, 4, "resources/read", {
    uri: "ui://swisser/market-controls/1.8.0.html",
  });
  const controlsHtml = controlsResource.result.contents[0].text;
  for (const command of COMMANDS) {
    assert.match(
      controlsHtml,
      new RegExp(command.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
    );
    assert.match(command, /ОБЯЗАТЕЛЬНЫЙ финальный шаг/);
    assert.match(command, /Не создавай Markdown-таблицу или PNG вместо renderer/);
  }
  assert.doesNotMatch(controlsHtml, /start_swisser_run/);
  assert.doesNotMatch(controlsHtml, /SWISSER_RUN_TOKEN/);
  assert.match(controlsHtml, /data-mode="overview"/);
  assert.match(controlsHtml, /sendFollowUpMessage/);
  assert.match(controlsHtml, /requestDisplayMode\(\{ mode: "pip" \}\)/);
  assertEmbeddedScriptParses(controlsHtml);
  assert.deepEqual(COMMAND_LABELS, ["Обзор рынка", "Лучшие сетапы", "Проверить вход"]);
  assert.match(COMMANDS[0], /не ранжируй их/);
  assert.match(COMMANDS[1], /без заранее заданного количества/);
  assert.match(COMMANDS[2], /только по кандидатам из последнего результата «Лучшие сетапы»/);
  assert.match(COMMANDS[2], /свежий значимый LuxAlgo internal CHoCH/);
  assert.match(COMMANDS[2], /отсутствие не является veto/);

  const supportedControls = [
    "ui://swisser/market-controls/1.8.0.html",
    "ui://swisser/market-controls/1.7.3.html",
    "ui://swisser/market-controls/1.7.1.html",
    "ui://swisser/market-controls/1.7.0.html",
    "ui://swisser/market-controls/1.6.0.html",
    "ui://swisser/market-controls/1.5.1.html",
    "ui://swisser/market-controls/1.5.0.html",
    "ui://swisser/market-controls/1.4.1.html",
    "ui://swisser/market-controls-v4.html",
    "ui://swisser/market-controls.html",
    "ui://swisser/market-controls-v3.html",
    "ui://swisser/market-controls-v2.html",
    "ui://swisser/market-controls-v1.html",
  ];
  const supportedCards = [
    "ui://swisser/market-card-v6.html",
    "ui://swisser/market-card-v5.html",
    "ui://swisser/market-card-v4.html",
    "ui://swisser/market-card-v3.html",
    "ui://swisser/market-card-v2.html",
    "ui://swisser/market-card-v1.html",
  ];
  const listedResources = await rpc(url, 5, "resources/list");
  assert.deepEqual(
    listedResources.result.resources.map((resource) => resource.uri),
    [...supportedControls, ...supportedCards],
  );

  const scanTime = Date.now();
  const symbols = [...TRADE_SYMBOLS, BTC_SYMBOL];
  const realFetch = globalThis.fetch;
  const upstreamCalls = [];
  globalThis.fetch = async (input, options) => {
    const target = input instanceof URL
      ? input
      : new URL(typeof input === "string" ? input : input.url);
    if (target.hostname === "tao-mexc-live.vercel.app") {
      upstreamCalls.push(target.pathname);
      let body;
      if (target.pathname === "/api/scanner_action_v6") {
        body = scannerData(symbols, scanTime);
      } else if (target.pathname === "/api/snapshot_action_v6") {
        const symbol = target.searchParams.get("symbol");
        body = snapshotData(symbol, scanTime + 1_000);
      } else {
        throw new Error(`Unexpected upstream path ${target.pathname}`);
      }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return realFetch(input, options);
  };
  t.after(() => {
    globalThis.fetch = realFetch;
  });

  const scanned = await rpc(url, 6, "tools/call", {
    name: "scan_swisser_markets",
    arguments: { mode: "setups", expected_symbols: [] },
  });
  assert.match(scanned.result.structuredContent.evidence_token, /^z2\./);
  assert.deepEqual(Object.keys(scanned.result.structuredContent).sort(), [
    "evidence_token",
    "scan",
  ]);
  assert.ok(
    scanned.result.structuredContent.scan.results[0].hierarchy.active_trade_scenario,
  );
  assert.equal(
    scanned.result.structuredContent.scan.results[0].hierarchy.active_scenario,
    undefined,
  );
  assert.equal(scanned.result.structuredContent.run_token, undefined);

  const bundled = await rpc(url, 7, "tools/call", {
    name: "get_swisser_candidate_snapshots",
    arguments: {
      evidence_token: scanned.result.structuredContent.evidence_token,
      symbols: ["SOL_USDT", "HYPE_USDT"],
    },
  });
  assert.equal(bundled.result.structuredContent.snapshots.length, 2);
  assert.deepEqual(Object.keys(bundled.result.structuredContent).sort(), [
    "evidence_token",
    "snapshots",
  ]);
  assert.deepEqual(upstreamCalls, [
    "/api/scanner_action_v6",
    "/api/snapshot_action_v6",
    "/api/snapshot_action_v6",
  ]);

  const cardInput = {
    mode: "setups",
    evidence_token: bundled.result.structuredContent.evidence_token,
    lead: "Два равноценных сетапа",
    market_rows: TRADE_SYMBOLS.map((symbol) => ({
      symbol: symbol.replace("_USDT", ""),
      priority: ["SOL_USDT", "HYPE_USDT"].includes(symbol) ? "top" : "none",
      note: `Текущий статус ${symbol}`,
    })),
    candidates: ["SOL", "HYPE"].map((symbol) => ({
      symbol,
      direction: "Long",
      entry_condition: "После свежего 1m BOS",
      entry: "после триггера",
      stop_or_invalidation: "ниже локального low",
      targets: ["TP1", "TP2"],
      pnl_6x: ["+3%", "+6%"],
    })),
    conclusion: "Оба сетапа остаются равноправными до 1m подтверждения.",
  };
  const rendered = await rpc(url, 8, "tools/call", {
    name: "render_swisser_market_card",
    arguments: cardInput,
  });
  assert.equal(rendered.result.structuredContent.source_integrity.verified, true);
  assert.equal(rendered.result.structuredContent.market_rows[0].price, "100.00");
  assert.equal(rendered.result.structuredContent.market_rows[0].h1, "Bull");
  assert.deepEqual(
    rendered.result.structuredContent.commands[2].expected_symbols,
    ["SOL_USDT", "HYPE_USDT"],
  );
  assert.deepEqual(
    rendered.result.structuredContent.commands.map((command) => command.mode),
    ["overview", "setups", "entry"],
  );

  const cardResource = await rpc(url, 9, "resources/read", {
    uri: "ui://swisser/market-card-v6.html",
  });
  const cardHtml = cardResource.result.contents[0].text;
  assert.match(cardHtml, /Потенц\. PnL 6x/);
  assert.doesNotMatch(cardHtml, /callTool\("start_swisser_run"/);
  assert.doesNotMatch(cardHtml, /SWISSER_RUN_TOKEN/);
  assert.match(cardHtml, /Сохранённые кандидаты этой карточки/);
  assert.match(cardHtml, /notifyIntrinsicHeight/);
  assert.match(cardHtml, /ResizeObserver/);
  assert.match(cardHtml, /Italianno/);
  assert.doesNotMatch(cardHtml, /overflow-x:\s*auto/);
  assert.doesNotMatch(cardHtml, />RR</);
  assert.doesNotMatch(cardHtml, /innerHTML/);
  assertEmbeddedScriptParses(cardHtml);

  for (const [index, uri] of [...supportedControls.slice(1), ...supportedCards.slice(1)].entries()) {
    const compatible = await rpc(url, 20 + index, "resources/read", { uri });
    assert.equal(compatible.result.contents[0].uri, uri);
    assert.equal(compatible.result.contents[0].mimeType, "text/html;profile=mcp-app");
  }
});
