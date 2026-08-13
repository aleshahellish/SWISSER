import {
  RESOURCE_MIME_TYPE,
  registerAppResource,
  registerAppTool,
} from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

const SERVER_VERSION = "1.2.0";
const CONTROLS_URI = "ui://swisser/market-controls-v3.html";
const LEGACY_CONTROLS_URIS = [
  "ui://swisser/market-controls-v2.html",
  "ui://swisser/market-controls-v1.html",
];
const API_BASE = process.env.SWISSER_API_BASE ?? "https://tao-mexc-live.vercel.app";
const SUPPORTED_SYMBOLS = [
  "TAO_USDT",
  "HYPE_USDT",
  "SOL_USDT",
  "XRP_USDT",
  "DOGE_USDT",
  "ETH_USDT",
  "BTC_USDT",
];

export const COMMANDS = [
  "Обнови рынок. Кратко дай сводку.",
  "Найди лучшие потенциальные сделки сейчас.",
  "Есть ли смысл ждать сегодня? Что близко к входу?",
];

const COMMAND_LABELS = [
  "Обновить рынок",
  "Лучшие сделки",
  "Что близко к входу",
];

const controlsHtml = `<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>SWISSER — команды рынка</title>
  <style>
    :root { color-scheme: light dark; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 6px; background: transparent; color: CanvasText; }
    .panel { border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); border-radius: 12px; padding: 8px; background: Canvas; }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
    .brand { font-size: 11px; font-weight: 700; letter-spacing: .12em; opacity: .68; }
    .pin { border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); background: transparent; color: inherit; font: inherit; font-size: 10.5px; line-height: 1.3; cursor: pointer; opacity: .7; padding: 3px 6px; border-radius: 6px; }
    .pin:hover { background: color-mix(in srgb, CanvasText 7%, transparent); opacity: 1; }
    .pin:disabled { cursor: default; opacity: .55; }
    .commands { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; }
    .command { display: flex; align-items: center; width: 100%; min-height: 34px; text-align: left; border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); border-radius: 8px; padding: 6px 8px; background: color-mix(in srgb, CanvasText 3%, Canvas); color: inherit; font: inherit; font-size: 12px; line-height: 1.2; cursor: pointer; transition: border-color .15s, background .15s, transform .05s; }
    .command:hover { border-color: color-mix(in srgb, CanvasText 28%, transparent); background: color-mix(in srgb, CanvasText 7%, Canvas); }
    .command:active { transform: translateY(1px); }
    .command:disabled { cursor: wait; opacity: .55; }
    .number { flex: 0 0 auto; display: inline-grid; place-items: center; width: 18px; height: 18px; margin-right: 6px; border: 1px solid color-mix(in srgb, CanvasText 12%, transparent); border-radius: 5px; background: color-mix(in srgb, CanvasText 6%, Canvas); font-size: 10px; font-weight: 700; opacity: .78; }
    .status { margin: 5px 1px 0; font-size: 10px; line-height: 1.3; opacity: .62; }
    .status:empty { display: none; }
    @media (max-width: 560px) { .commands { grid-template-columns: 1fr; } }
    body[data-display-mode="pip"] { padding: 4px; }
    body[data-display-mode="pip"] .panel { padding: 6px; border-radius: 10px; }
    body[data-display-mode="pip"] .head { margin-bottom: 5px; }
    body[data-display-mode="pip"] .commands { grid-template-columns: 1fr; gap: 4px; }
    body[data-display-mode="pip"] .command { min-height: 30px; padding: 5px 7px; font-size: 11.5px; }
  </style>
</head>
<body>
  <section class="panel" aria-label="Быстрые команды SWISSER">
    <div class="head">
      <div class="brand">SWISSER</div>
      <button class="pin" id="pin" type="button" title="Оставить панель поверх чата">Закрепить</button>
    </div>
    <div class="commands">
      <button class="command" type="button" data-command="${COMMANDS[0]}"><span class="number">1</span>${COMMAND_LABELS[0]}</button>
      <button class="command" type="button" data-command="${COMMANDS[1]}"><span class="number">2</span>${COMMAND_LABELS[1]}</button>
      <button class="command" type="button" data-command="${COMMANDS[2]}"><span class="number">3</span>${COMMAND_LABELS[2]}</button>
    </div>
    <div class="status" id="status" role="status"></div>
  </section>
  <script>
    const status = document.getElementById("status");
    const pinButton = document.getElementById("pin");
    const buttons = [...document.querySelectorAll(".command")];
    let autoPinAttempted = false;
    let statusTimer;

    function setStatus(message, clearAfter = 0) {
      clearTimeout(statusTimer);
      status.textContent = message;
      if (clearAfter) statusTimer = setTimeout(() => { status.textContent = ""; }, clearAfter);
    }

    function setDisplayMode(mode) {
      document.body.dataset.displayMode = mode || "inline";
      const isPinned = mode === "pip";
      pinButton.disabled = isPinned;
      pinButton.textContent = isPinned ? "Готово ✓" : "Закрепить";
      pinButton.title = isPinned
        ? "Панель останется поверх чата до закрытия"
        : "Оставить панель поверх чата";
    }

    async function requestPip({ manual = false, force = false } = {}) {
      if (window.openai?.displayMode === "pip") {
        setDisplayMode("pip");
        return true;
      }
      if (!window.openai?.requestDisplayMode) {
        if (manual) setStatus("Закрепление недоступно в этом режиме ChatGPT.");
        return false;
      }
      if (autoPinAttempted && !manual && !force) return false;
      if (!manual) autoPinAttempted = true;

      try {
        const result = await window.openai.requestDisplayMode({ mode: "pip" });
        setDisplayMode(result?.mode);
        if (result?.mode === "pip") {
          setStatus("Панель закреплена.", 1600);
          return true;
        }
        if (manual) setStatus("ChatGPT оставил панель в текущем режиме.");
      } catch (error) {
        if (manual) setStatus(error?.message || "Не удалось закрепить панель.");
      }
      return false;
    }

    async function send(prompt, button) {
      buttons.forEach((item) => { item.disabled = true; });
      setStatus("Отправляю…");
      try {
        if (!window.openai?.sendFollowUpMessage) {
          throw new Error("Команды недоступны в этом режиме ChatGPT");
        }
        await requestPip({ force: true });
        await window.openai.sendFollowUpMessage({ prompt, scrollToBottom: true });
        setStatus("Отправлено.", 1200);
      } catch (error) {
        setStatus(error?.message || "Не удалось отправить запрос.");
      } finally {
        setTimeout(() => buttons.forEach((item) => { item.disabled = false; }), 700);
      }
    }

    buttons.forEach((button) => {
      button.addEventListener("click", () => send(button.dataset.command, button));
    });

    pinButton.addEventListener("click", () => requestPip({ manual: true, force: true }));

    window.addEventListener("openai:set_globals", (event) => {
      const mode = event.detail?.globals?.displayMode;
      if (mode) setDisplayMode(mode);
      requestPip();
    });

    setDisplayMode(window.openai?.displayMode || "inline");
    requestAnimationFrame(() => requestPip());
    setTimeout(() => requestPip(), 350);
  </script>
</body>
</html>`;

async function fetchSwisser(path, params) {
  const url = new URL(path, API_BASE);
  url.searchParams.set("nocache", String(Date.now()));
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  const response = await fetch(url, {
    headers: { accept: "application/json", "user-agent": "SWISSER-MCP/1.0" },
    signal: AbortSignal.timeout(58_000),
  });
  if (!response.ok) throw new Error(`SWISSER API returned HTTP ${response.status}`);
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new Error("SWISSER API returned a non-JSON response");
  }
  return response.json();
}

export function createSwisserMcpServer() {
  const server = new McpServer({
    name: "swisser-market-controls",
    version: SERVER_VERSION,
  }, {
    instructions:
      "SWISSER анализирует MEXC Futures. Сначала используй scan_swisser_markets для всех монет, " +
      "затем get_swisser_market_snapshot только для достойных кандидатов. При первой активации " +
      "вызови open_swisser_controls: панель сама запросит постоянный PiP-режим. Не открывай её повторно " +
      "Текущую сделку называй по active_trade_scenario ядра 1h→15m→1m; continuation_bias и 4h — " +
      "контекст, а не активный " +
      "LONG/SHORT. Не выдумывай отсутствующие уровни.",
  });

  server.registerTool(
    "scan_swisser_markets",
    {
      title: "Сканировать рынок SWISSER",
      description:
        "Получает компактный актуальный scanner по TAO, HYPE, SOL, XRP, DOGE и ETH; BTC используется только как рыночный контекст. " +
        "Всегда начинай рыночный запрос с этого инструмента. Для текущего сценария используй active_trade_scenario, " +
        "не continuation_bias. Пустой список означает полный скан всех семи символов.",
      inputSchema: {
        symbols: z
          .array(z.enum(SUPPORTED_SYMBOLS))
          .optional()
          .describe("Необязательный список символов. По умолчанию сканируются все поддерживаемые монеты."),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: true,
      },
    },
    async ({ symbols }) => {
      const data = await fetchSwisser("/api/scanner_action_v6", {
        symbols: symbols?.length ? symbols.join(",") : undefined,
      });
      return {
        content: [{ type: "text", text: "Актуальный SWISSER scanner получен. Используй полный structuredContent для сравнения кандидатов." }],
        structuredContent: data,
      };
    },
  );

  server.registerTool(
    "get_swisser_market_snapshot",
    {
      title: "Проверить кандидата SWISSER",
      description:
        "Получает подробный актуальный snapshot одной монеты после первичного scanner. " +
        "Используй для подтверждения структуры, входа, отмены, целей, RR и потенциального PnL выбранного кандидата.",
      inputSchema: {
        symbol: z.enum(SUPPORTED_SYMBOLS).describe("Символ кандидата, например TAO_USDT."),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: true,
      },
    },
    async ({ symbol }) => {
      const data = await fetchSwisser("/api/snapshot_action_v6", { symbol });
      return {
        content: [{ type: "text", text: `Подробный SWISSER snapshot ${symbol} получен. Используй полный structuredContent.` }],
        structuredContent: data,
      };
    },
  );

  registerAppTool(
    server,
    "open_swisser_controls",
    {
      title: "Открыть команды SWISSER",
      description:
        "Показывает компактную панель из трёх быстрых рыночных команд SWISSER и автоматически запрашивает " +
        "постоянный PiP-режим поверх чата. Вызывай инструмент при первом запуске SWISSER и по просьбе вернуть " +
        "панель. Не вызывай его повторно после каждого ответа, пока PiP-панель активна.",
      inputSchema: {},
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: false,
      },
      _meta: {
        ui: { resourceUri: CONTROLS_URI },
        "openai/outputTemplate": CONTROLS_URI,
        "openai/toolInvocation/invoking": "Открываю команды SWISSER",
        "openai/toolInvocation/invoked": "Команды SWISSER готовы",
      },
    },
    async () => ({
      content: [
        {
          type: "text",
          text: "Панель трёх быстрых команд SWISSER открыта и автоматически запрашивает закрепление поверх чата. Не перечисляй команды повторно обычным текстом.",
        },
      ],
      structuredContent: {
        title: "SWISSER · Рынок",
        commands: COMMANDS.map((prompt, index) => ({ id: index + 1, prompt })),
      },
    }),
  );

  for (const resourceUri of [CONTROLS_URI, ...LEGACY_CONTROLS_URIS]) {
    registerAppResource(
      server,
      resourceUri === CONTROLS_URI
        ? "Команды рынка SWISSER"
        : "Команды рынка SWISSER (совместимость)",
      resourceUri,
      {
        mimeType: RESOURCE_MIME_TYPE,
        description: "Три постоянные кнопки быстрых рыночных запросов SWISSER",
      },
      async () => ({
        contents: [
          {
            uri: resourceUri,
            mimeType: RESOURCE_MIME_TYPE,
            text: controlsHtml,
            _meta: {
              ui: {
                prefersBorder: false,
                csp: { connectDomains: [], resourceDomains: [] },
              },
              "openai/widgetPrefersBorder": false,
            },
          },
        ],
      }),
    );
  }

  return server;
}

function setCorsHeaders(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  res.setHeader(
    "Access-Control-Allow-Headers",
    "Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version",
  );
  res.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id");
}

export default async function handler(req, res) {
  setCorsHeaders(res);
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.end();
    return;
  }

  const server = createSwisserMcpServer();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });

  res.on("close", () => {
    transport.close().catch(() => {});
    server.close().catch(() => {});
  });

  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (error) {
    console.error("SWISSER MCP error", error);
    if (!res.headersSent) {
      res.statusCode = 500;
      res.setHeader("Content-Type", "application/json");
      res.end(
        JSON.stringify({
          jsonrpc: "2.0",
          error: { code: -32603, message: "Internal server error" },
          id: null,
        }),
      );
    }
  }
}
