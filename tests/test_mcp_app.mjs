import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import handler, { COMMAND_LABELS, COMMANDS } from "../api/mcp.js";

async function startServer() {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  return {
    server,
    url: `http://127.0.0.1:${address.port}`,
  };
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
    const data = text
      .split("\n")
      .find((line) => line.startsWith("data: "))
      ?.slice(6);
    return JSON.parse(data);
  }
  return response.json();
}

test("SWISSER MCP exposes the three full commands and UI resource", async (t) => {
  const { server, url } = await startServer();
  t.after(() => server.close());

  const initialized = await rpc(url, 1, "initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "swisser-test", version: "1.0.0" },
  });
  assert.equal(initialized.result.serverInfo.name, "swisser-market-controls");
  assert.equal(initialized.result.serverInfo.version, "1.5.0");
  assert.deepEqual(initialized.result.serverInfo.icons, [
    {
      src: "https://tao-mexc-live.vercel.app/swisser-icon.svg",
      mimeType: "image/svg+xml",
      sizes: ["64x64"],
    },
  ]);

  const tools = await rpc(url, 2, "tools/list");
  assert.deepEqual(tools.result.tools.map((tool) => tool.name), [
    "scan_swisser_markets",
    "render_swisser_market_card",
    "get_swisser_market_snapshot",
    "open_swisser_controls",
  ]);
  assert.equal(
    tools.result.tools[3]._meta["openai/outputTemplate"],
    "ui://swisser/market-controls/1.5.0.html",
  );
  assert.equal(
    tools.result.tools[1]._meta["openai/outputTemplate"],
    "ui://swisser/market-card-v1.html",
  );

  const called = await rpc(url, 3, "tools/call", {
    name: "open_swisser_controls",
    arguments: {},
  });
  assert.deepEqual(
    called.result.structuredContent.commands.map((command) => command.prompt),
    COMMANDS,
  );

  const resources = await rpc(url, 4, "resources/read", {
    uri: "ui://swisser/market-controls/1.5.0.html",
  });
  const html = resources.result.contents[0].text;
  for (const command of COMMANDS) assert.match(html, new RegExp(command.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(html, /sendFollowUpMessage/);
  assert.match(html, /requestDisplayMode\(\{ mode: "pip" \}\)/);
  assert.match(html, /requestAnimationFrame\(\(\) => requestPip\(\)\)/);
  assert.match(html, /openai:set_globals/);
  for (const label of COMMAND_LABELS) assert.match(html, new RegExp(label));
  assert.match(COMMANDS[0], /только общий scanner/);
  assert.match(COMMANDS[0], /без подробных snapshot и новостей/);
  assert.match(COMMANDS[1], /snapshot всех действительно подходящих кандидатов/);
  assert.match(COMMANDS[1], /без жёсткого лимита/);
  assert.match(COMMANDS[2], /близко к формированию в ближайшие часы/);
  assert.match(COMMANDS[2], /только когда есть действительно значимое событие/);
  assert.doesNotMatch(html, /#36a269|#238a55/);

  const supportedResourceUris = [
    "ui://swisser/market-controls/1.5.0.html",
    "ui://swisser/market-controls/1.4.1.html",
    "ui://swisser/market-controls-v4.html",
    "ui://swisser/market-controls.html",
    "ui://swisser/market-controls-v3.html",
    "ui://swisser/market-controls-v2.html",
    "ui://swisser/market-controls-v1.html",
  ];
  for (const [index, uri] of supportedResourceUris.entries()) {
    const compatibleResource = await rpc(url, 10 + index, "resources/read", { uri });
    assert.equal(compatibleResource.result.contents[0].uri, uri);
    assert.equal(compatibleResource.result.contents[0].mimeType, "text/html;profile=mcp-app");
    assert.match(compatibleResource.result.contents[0].text, /<div class="brand">SWISSER<\/div>/);
  }

  const listedResources = await rpc(url, 20, "resources/list");
  assert.deepEqual(
    listedResources.result.resources.map((resource) => resource.uri),
    [...supportedResourceUris, "ui://swisser/market-card-v1.html"],
  );

  const cardInput = {
    mode: "trades",
    cut_time: "02:12 МСК",
    btc_price: "$63,450",
    btc_structure: { h4: "Bear", h1: "Bear", m15: "Bull", m1: "Bear" },
    lead: "Готовых входов сейчас нет · ближе всего SOL",
    market_rows: [
      { symbol: "SOL", price: "$76.16", idea: "Wait", h4: "Bear", h1: "Bull", m15: "Bull", m1: "Bear", note: "Ждать возврата 76.31." },
      { symbol: "HYPE", price: "$57.433", idea: "Wait", h4: "Bull", h1: "Bull", m15: "Bear", m1: "Bear", note: "Fresh-window закрыт." },
      { symbol: "TAO", price: "$202.88", idea: "Wait", h4: "Bull", h1: "Bull", m15: "Bull", m1: "Bear", note: "Нужен свежий 1m Bull." },
      { symbol: "ETH", price: "$1,886.05", idea: "Local Short", h4: "Bear", h1: "Bull", m15: "Bear", m1: "Bear", note: "Нет свежего подтверждения." },
      { symbol: "XRP", price: "$1.0081", idea: "Wait", h4: "Bear", h1: "Bear", m15: "Bull", m1: "Bear", note: "Активного сценария нет." },
      { symbol: "DOGE", price: "$0.07006", idea: "Wait", h4: "Bull", h1: "Bear", m15: "Bull", m1: "Bear", note: "Рассинхронизация." },
    ],
    candidates: [
      {
        symbol: "SOL",
        direction: "Long",
        entry_condition: "1m должен стать Bull и закрепиться выше 76.31.",
        entry: "≈76.31",
        stop_or_invalidation: "ниже 76.08",
        targets: ["76.59", "77.32", "77.83"],
        pnl_6x: ["+2.2%", "+7.9%"],
      },
    ],
    conclusion: "SOL ближе всего, но вход только после подтверждения.",
  };
  const rendered = await rpc(url, 6, "tools/call", {
    name: "render_swisser_market_card",
    arguments: cardInput,
  });
  assert.deepEqual(rendered.result.structuredContent, {
    ...cardInput,
    commands: COMMANDS.map((prompt, index) => ({
      id: index + 1,
      label: COMMAND_LABELS[index],
      prompt,
    })),
  });

  const cardResource = await rpc(url, 7, "resources/read", {
    uri: "ui://swisser/market-card-v1.html",
  });
  const cardHtml = cardResource.result.contents[0].text;
  assert.match(cardHtml, /Монета/);
  assert.match(cardHtml, /Состояние \/ ориентир/);
  assert.match(cardHtml, /Потенц\. PnL 6x/);
  assert.match(cardHtml, /window\.openai\?\.toolOutput/);
  assert.match(cardHtml, /openai:set_globals/);
  assert.match(cardHtml, /sendFollowUpMessage/);
  assert.match(cardHtml, /command-bar/);
  assert.match(cardHtml, /Italianno/);
  assert.doesNotMatch(cardHtml, />RR</);
  assert.doesNotMatch(cardHtml, /innerHTML/);
});
