import {
  RESOURCE_MIME_TYPE,
  registerAppResource,
  registerAppTool,
} from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

import {
  LEGACY_MARKET_CARD_URIS,
  MARKET_CARD_URI,
  marketCardHtml,
} from "../swisser_market_card.js";

const SERVER_VERSION = "1.6.0";
const CONTROLS_URI = "ui://swisser/market-controls/1.6.0.html";
const LEGACY_CONTROLS_URIS = [
  "ui://swisser/market-controls/1.5.1.html",
  "ui://swisser/market-controls/1.5.0.html",
  "ui://swisser/market-controls/1.4.1.html",
  "ui://swisser/market-controls-v4.html",
  "ui://swisser/market-controls.html",
  "ui://swisser/market-controls-v3.html",
  "ui://swisser/market-controls-v2.html",
  "ui://swisser/market-controls-v1.html",
];
const SERVER_ICON = "https://tao-mexc-live.vercel.app/swisser-icon.svg";
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
  "Собери нейтральный обзор рынка без выбора лучших. Используй полный scan_swisser_markets по всем поддерживаемым символам; BTC оставь только рыночным контекстом. Покажи фактическое состояние всех шести торговых монет, не ранжируй их, не формируй торговых кандидатов и не выдумывай отсутствующие детали. ОБЯЗАТЕЛЬНЫЙ финальный шаг — вызови render_swisser_market_card в режиме overview: шесть монет в фиксированном порядке, priority none, candidates пустой. Не создавай Markdown-таблицу или PNG вместо renderer.",
  "Найди лучшие сетапы на всём рынке. После полного scan_swisser_markets подробно проверь snapshot всех действительно конкурентных кандидатов. Не задавай количество заранее и не назначай единственного победителя, если данные не позволяют объективно разделить монеты: любое число равноценных кандидатов помести в один top-tier. Отдельно оцени качество сетапа и готовность входа; сильный свежий 1m BOS/CHoCH может сочетаться со статусом WAIT из-за опоздания. ОБЯЗАТЕЛЬНЫЙ финальный шаг — вызови render_swisser_market_card в режиме setups: все шесть монет и все реальные кандидаты без слабых заполнителей. Не создавай Markdown-таблицу или PNG вместо renderer.",
  "Проверь входы только по кандидатам из последнего результата «Лучшие сетапы» в этом диалоге, исключая уже отменённые последующими проверками. Не сканируй и не добавляй остальные монеты и не формируй новый рейтинг. Если такого результата ещё нет либо в нём не было кандидатов, коротко попроси сначала запустить «Лучшие сетапы». Для каждого сохранённого кандидата обнови scanner и подробный snapshot; на 1m проверь свежесть CHoCH/BOS, displacement, окончание отката или ретест, C2/C3, риск опоздания и инвалидацию. Для каждого дай статус ВХОД ПОДТВЕРЖДЁН, ЖДАТЬ или ОТМЕНА; допускай несколько одновременных входов без искусственного выбора одного. Если все сценарии отменены, предложи заново запустить «Лучшие сетапы». ОБЯЗАТЕЛЬНЫЙ финальный шаг после проверки — вызови render_swisser_market_card в режиме entry: в market_rows покажи проверенный прежний набор со статусом каждого, а в candidates оставь только неотменённые сценарии. Не создавай Markdown-таблицу или PNG вместо renderer.",
];

export const COMMAND_LABELS = [
  "Обзор рынка",
  "Лучшие сетапы",
  "Проверить вход",
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
    icons: [
      {
        src: SERVER_ICON,
        mimeType: "image/svg+xml",
        sizes: ["64x64"],
      },
    ],
  }, {
    instructions:
      "ОБЯЗАТЕЛЬНО: каждый завершённый рыночный анализ SWISSER заканчивай вызовом render_swisser_market_card. " +
      "Обычная Markdown-таблица, список или PNG не заменяют renderer. После вызова не дублируй карточку текстом. " +
      "Точный запрос «1» означает режим overview («Обзор рынка»), «2» — setups («Лучшие сетапы»), " +
      "«3» — entry («Проверить вход»); не проси расшифровку цифры. SWISSER анализирует MEXC Futures в трёх последовательных режимах. " +
      "«Обзор рынка» — полный scan_swisser_markets всех монет без snapshot, ранжирования и торгового отбора; " +
      "покажи нейтральный фактический срез шести торговых монет. «Лучшие сетапы» — полный scanner, затем snapshot " +
      "всех действительно конкурентных кандидатов без заранее заданного количества; не добавляй слабые ситуации " +
      "ради количества и не назначай победителя, если несколько монет объективно равноценны. Качество сетапа " +
      "и готовность входа оценивай отдельно. «Проверить вход» — повторно проверь только кандидатов из последнего " +
      "результата «Лучшие сетапы» в текущем диалоге, исключая уже отменённые последующими проверками; не добавляй " +
      "другие монеты и не составляй новый рейтинг; " +
      "для каждого обнови scanner и snapshot, особенно свежий 1m CHoCH/BOS, displacement, откат/ретест, C2/C3, " +
      "опоздание и инвалидацию, затем дай статус ВХОД ПОДТВЕРЖДЁН, ЖДАТЬ или ОТМЕНА. Если предыдущего списка нет, " +
      "коротко попроси сначала запустить «Лучшие сетапы»; если все сценарии отменены, предложи повторить второй режим. " +
      "При первой активации " +
      "вызови open_swisser_controls: панель сама запросит PiP-режим на время активной сессии. Не открывай её повторно " +
      "после каждого ответа, пока она активна; после анализа рабочие команды уже доступны под итоговой карточкой. " +
      "Текущую сделку называй по active_trade_scenario ядра 1h→15m→1m; continuation_bias и 4h — " +
      "контекст, а не активный " +
      "LONG/SHORT. Не выдумывай отсутствующие уровни. При ретроспективном разборе зафиксируй исходный срез " +
      "и исходный рейтинг, не пересортировывай монеты по последующему результату и не используй данные после " +
      "контрольного времени для объяснения прежнего выбора. PnL 6x называй результатом сделки только от реально " +
      "исполненного либо заранее заданного условного входа; изменение цены от произвольного среза обозначай как " +
      "движение после среза, а не PnL сделки. Один эпизод не меняет веса ранжирования без серии наблюдений.",
  });

  server.registerTool(
    "scan_swisser_markets",
    {
      title: "Сканировать рынок SWISSER",
      description:
        "Получает компактный актуальный scanner по TAO, HYPE, SOL, XRP, DOGE и ETH; BTC используется только как рыночный контекст. " +
        "Всегда начинай рыночный запрос с этого инструмента. Для текущего сценария используй active_trade_scenario, " +
        "не continuation_bias. Пустой список означает полный скан всех семи символов. После анализа не отвечай " +
        "Markdown-таблицей: обязательный финальный шаг — render_swisser_market_card.",
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
        content: [{ type: "text", text: "Актуальный SWISSER scanner получен. Используй полный structuredContent для сравнения кандидатов. Не формируй финальную Markdown-таблицу: заверши рыночный ответ вызовом render_swisser_market_card." }],
        structuredContent: data,
      };
    },
  );

  registerAppTool(
    server,
    "render_swisser_market_card",
    {
      title: "Показать карточку рынка SWISSER",
      description:
        "Финально отображает неизменяемую компактную карточку SWISSER после анализа. " +
        "Это обязательный финальный инструмент, а не необязательное украшение. Вызывай ровно один раз в конце " +
        "каждого завершённого рыночного анализа в режимах overview, setups и entry. В overview передавай все шесть монет " +
        "в фиксированном порядке без приоритета и оставляй candidates пустым. В setups передавай все шесть монет и любое " +
        "число действительно сильных кандидатов; равноценным монетам назначай одинаковый priority. В entry в market_rows " +
        "передавай проверенный набор из последнего результата setups со статусом каждого, а в candidates — только ещё " +
        "действующие сценарии; новые монеты не добавляй. Не показывай RR. " +
        "Карточка сама добавляет три команды под итогом, чтобы к ним не приходилось прокручивать чат.",
      inputSchema: {
        mode: z
          .enum(["overview", "setups", "entry", "quick", "trades", "day"])
          .describe("Используй overview, setups или entry; quick/trades/day сохранены только для старых клиентов."),
        cut_time: z.string().min(1).max(40).describe("Время среза с пометкой МСК."),
        btc_price: z.string().min(1).max(32).describe("Отформатированная цена BTC."),
        btc_structure: z.object({
          h4: z.enum(["Bull", "Bear", "Wait", "—"]),
          h1: z.enum(["Bull", "Bear", "Wait", "—"]),
          m15: z.enum(["Bull", "Bear", "Wait", "—"]),
          m1: z.enum(["Bull", "Bear", "Wait", "—"]),
        }),
        lead: z.string().min(1).max(180).describe("Один главный вывод над таблицей."),
        market_rows: z
          .array(
            z.object({
              symbol: z.enum(["TAO", "HYPE", "SOL", "XRP", "DOGE", "ETH"]),
              price: z.string().min(1).max(32),
              idea: z.enum(["Long", "Short", "Local Long", "Local Short", "Wait", "—"]),
              h4: z.enum(["Bull", "Bear", "Wait", "—"]),
              h1: z.enum(["Bull", "Bear", "Wait", "—"]),
              m15: z.enum(["Bull", "Bear", "Wait", "—"]),
              m1: z.enum(["Bull", "Bear", "Wait", "—"]),
              priority: z
                .enum(["top", "secondary", "watch", "none"])
                .optional()
                .describe("В overview и entry — none. В setups одинаково сильные монеты получают одинаковый tier."),
              note: z.string().min(1).max(260),
            }),
          )
          .min(1)
          .max(6)
          .describe("В overview/setups — все шесть монет; в entry — только кандидаты из последнего setups."),
        candidates: z
          .array(
            z.object({
              symbol: z.enum(["TAO", "HYPE", "SOL", "XRP", "DOGE", "ETH"]),
              direction: z.enum(["Long", "Short", "Local Long", "Local Short"]),
              entry_condition: z.string().min(1).max(320),
              entry: z.string().min(1).max(80),
              stop_or_invalidation: z.string().min(1).max(120),
              targets: z.array(z.string().min(1).max(40)).min(1).max(3),
              pnl_6x: z.array(z.string().min(1).max(32)).min(1).max(3),
            }),
          )
          .max(6)
          .describe("Пусто для overview; в setups — все сильные кандидаты без лимита и заполнителей; в entry — только неотменённые сценарии из сохранённого набора."),
        conclusion: z.string().min(1).max(420),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: false,
      },
      _meta: {
        ui: { resourceUri: MARKET_CARD_URI },
        "openai/outputTemplate": MARKET_CARD_URI,
        "openai/toolInvocation/invoking": "Оформляю сводку SWISSER",
        "openai/toolInvocation/invoked": "Сводка SWISSER готова",
      },
    },
    async (card) => ({
      content: [
        {
          type: "text",
          text: "Карточка SWISSER отображена. Не повторяй её содержимое большой Markdown-таблицей; при необходимости добавь только одну короткую оговорку.",
        },
      ],
      structuredContent: {
        ...card,
        commands: COMMANDS.map((prompt, index) => ({
          id: index + 1,
          label: COMMAND_LABELS[index],
          prompt,
        })),
      },
    }),
  );

  server.registerTool(
    "get_swisser_market_snapshot",
    {
      title: "Проверить кандидата SWISSER",
      description:
        "Получает подробный актуальный snapshot одной монеты после первичного scanner. " +
        "Используй для подтверждения структуры, входа, отмены, целей и потенциального PnL выбранного кандидата. " +
        "Не вызывай в режиме «Обзор рынка».",
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
        content: [{ type: "text", text: `Подробный SWISSER snapshot ${symbol} получен. Используй полный structuredContent и заверши рыночный ответ вызовом render_swisser_market_card вместо Markdown-таблицы.` }],
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
        "Показывает компактную панель из трёх последовательных этапов SWISSER и автоматически запрашивает " +
        "PiP-режим поверх чата на время активной сессии. Вызывай инструмент при первом запуске SWISSER и по просьбе вернуть " +
        "панель. После ухода из чата ChatGPT может завершить PiP; три команды остаются под последней рыночной карточкой.",
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
          text: "Панель трёх команд SWISSER открыта и автоматически запрашивает закрепление поверх чата. Точные запросы 1, 2 и 3 соответствуют overview, setups и entry. Каждый завершённый анализ обязательно завершается render_swisser_market_card; Markdown-таблица или PNG не являются заменой. Не перечисляй команды повторно обычным текстом.",
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
        description: "Три последовательных этапа анализа рынка SWISSER",
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

  for (const resourceUri of [MARKET_CARD_URI, ...LEGACY_MARKET_CARD_URIS]) {
    registerAppResource(
      server,
      resourceUri === MARKET_CARD_URI
        ? "Рыночная карточка SWISSER"
        : "Рыночная карточка SWISSER (совместимость)",
      resourceUri,
      {
        mimeType: RESOURCE_MIME_TYPE,
        description: "Компактная таблица рынка и реальных торговых кандидатов SWISSER",
      },
      async () => ({
        contents: [
          {
            uri: resourceUri,
            mimeType: RESOURCE_MIME_TYPE,
            text: marketCardHtml,
            _meta: {
              ui: {
                prefersBorder: false,
                csp: {
                  connectDomains: [],
                  resourceDomains: [
                    "https://fonts.googleapis.com",
                    "https://fonts.gstatic.com",
                  ],
                },
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
