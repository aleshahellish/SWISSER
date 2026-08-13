import {
  RESOURCE_MIME_TYPE,
  registerAppResource,
  registerAppTool,
} from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";

const SERVER_VERSION = "1.0.0";
const CONTROLS_URI = "ui://swisser/market-controls-v1.html";

export const COMMANDS = [
  "Обнови рынок. Кратко дай сводку.",
  "Найди лучшие потенциальные сделки сейчас.",
  "Есть ли смысл ждать сегодня? Что близко к входу?",
];

const controlsHtml = `<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>SWISSER — команды рынка</title>
  <style>
    :root { color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 12px; background: transparent; color: CanvasText; }
    .panel { border: 1px solid color-mix(in srgb, CanvasText 18%, transparent); border-radius: 16px; padding: 12px; background: color-mix(in srgb, Canvas 96%, transparent); }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
    .brand { font-size: 13px; font-weight: 800; letter-spacing: .08em; }
    .pin { border: 0; background: transparent; color: inherit; font: inherit; font-size: 12px; cursor: pointer; opacity: .72; padding: 5px 7px; border-radius: 8px; }
    .pin:hover { background: color-mix(in srgb, CanvasText 8%, transparent); opacity: 1; }
    .commands { display: grid; gap: 8px; }
    .command { width: 100%; text-align: left; border: 1px solid color-mix(in srgb, CanvasText 18%, transparent); border-radius: 12px; padding: 10px 12px; background: color-mix(in srgb, CanvasText 5%, Canvas); color: inherit; font: inherit; font-size: 13px; line-height: 1.35; cursor: pointer; transition: border-color .15s, background .15s, transform .05s; }
    .command:hover { border-color: #36a269; background: color-mix(in srgb, #36a269 10%, Canvas); }
    .command:active { transform: translateY(1px); }
    .command:disabled { cursor: wait; opacity: .55; }
    .number { display: inline-grid; place-items: center; width: 21px; height: 21px; margin-right: 7px; border-radius: 7px; color: white; background: #238a55; font-size: 11px; font-weight: 800; }
    .status { min-height: 17px; margin: 8px 2px 0; font-size: 11px; opacity: .68; }
    @media (min-width: 680px) { .commands { grid-template-columns: repeat(3, 1fr); } .command { min-height: 72px; } }
  </style>
</head>
<body>
  <section class="panel" aria-label="Быстрые команды SWISSER">
    <div class="head">
      <div class="brand">SWISSER · РЫНОК</div>
      <button class="pin" id="pin" type="button" title="Оставить панель поверх чата">Закрепить панель</button>
    </div>
    <div class="commands">
      <button class="command" type="button" data-command="${COMMANDS[0]}"><span class="number">1</span>${COMMANDS[0]}</button>
      <button class="command" type="button" data-command="${COMMANDS[1]}"><span class="number">2</span>${COMMANDS[1]}</button>
      <button class="command" type="button" data-command="${COMMANDS[2]}"><span class="number">3</span>${COMMANDS[2]}</button>
    </div>
    <div class="status" id="status" role="status">Нажатие отправляет полный запрос в этот чат.</div>
  </section>
  <script>
    const status = document.getElementById("status");
    const buttons = [...document.querySelectorAll(".command")];

    async function send(prompt, button) {
      buttons.forEach((item) => { item.disabled = true; });
      status.textContent = "Отправляю запрос…";
      try {
        if (!window.openai?.sendFollowUpMessage) {
          throw new Error("Команды недоступны в этом режиме ChatGPT");
        }
        await window.openai.sendFollowUpMessage({ prompt, scrollToBottom: true });
        status.textContent = "Запрос отправлен.";
      } catch (error) {
        status.textContent = error?.message || "Не удалось отправить запрос.";
      } finally {
        setTimeout(() => buttons.forEach((item) => { item.disabled = false; }), 700);
      }
    }

    buttons.forEach((button) => {
      button.addEventListener("click", () => send(button.dataset.command, button));
    });

    document.getElementById("pin").addEventListener("click", async () => {
      try {
        if (!window.openai?.requestDisplayMode) throw new Error("Закрепление недоступно");
        const result = await window.openai.requestDisplayMode({ mode: "pip" });
        status.textContent = result?.mode === "pip" ? "Панель закреплена." : "ChatGPT оставил панель в текущем режиме.";
      } catch (error) {
        status.textContent = error?.message || "Не удалось закрепить панель.";
      }
    });
  </script>
</body>
</html>`;

export function createSwisserMcpServer() {
  const server = new McpServer({
    name: "swisser-market-controls",
    version: SERVER_VERSION,
  });

  registerAppTool(
    server,
    "open_swisser_controls",
    {
      title: "Открыть команды SWISSER",
      description:
        "Показывает компактную панель из трёх быстрых рыночных команд SWISSER. " +
        "Вызывай этот инструмент при первом запуске SWISSER, по просьбе показать или вернуть кнопки, " +
        "а также после каждого ответа на одну из трёх рыночных команд, чтобы панель снова была доступна внизу чата.",
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
          text: "Панель трёх быстрых команд SWISSER открыта. Не перечисляй команды повторно обычным текстом.",
        },
      ],
      structuredContent: {
        title: "SWISSER · Рынок",
        commands: COMMANDS.map((prompt, index) => ({ id: index + 1, prompt })),
      },
    }),
  );

  registerAppResource(
    server,
    "Команды рынка SWISSER",
    CONTROLS_URI,
    {
      mimeType: RESOURCE_MIME_TYPE,
      description: "Три постоянные кнопки быстрых рыночных запросов SWISSER",
    },
    async () => ({
      contents: [
        {
          uri: CONTROLS_URI,
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
