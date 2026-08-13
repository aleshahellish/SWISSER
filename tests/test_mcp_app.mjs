import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import handler, { COMMANDS } from "../api/mcp.js";

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
  assert.equal(initialized.result.serverInfo.version, "1.2.0");

  const tools = await rpc(url, 2, "tools/list");
  assert.deepEqual(tools.result.tools.map((tool) => tool.name), [
    "scan_swisser_markets",
    "get_swisser_market_snapshot",
    "open_swisser_controls",
  ]);
  assert.equal(
    tools.result.tools[2]._meta["openai/outputTemplate"],
    "ui://swisser/market-controls-v3.html",
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
    uri: "ui://swisser/market-controls-v3.html",
  });
  const html = resources.result.contents[0].text;
  for (const command of COMMANDS) assert.match(html, new RegExp(command.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(html, /sendFollowUpMessage/);
  assert.match(html, /requestDisplayMode\(\{ mode: "pip" \}\)/);
  assert.match(html, /requestAnimationFrame\(\(\) => requestPip\(\)\)/);
  assert.match(html, /openai:set_globals/);
  assert.match(html, /Обновить рынок/);
  assert.match(html, /Лучшие сделки/);
  assert.match(html, /Что близко к входу/);
  assert.doesNotMatch(html, /#36a269|#238a55/);

  const legacyResources = await rpc(url, 5, "resources/read", {
    uri: "ui://swisser/market-controls-v1.html",
  });
  assert.equal(legacyResources.result.contents[0].mimeType, "text/html;profile=mcp-app");
  assert.match(legacyResources.result.contents[0].text, /<div class="brand">SWISSER<\/div>/);
});
