export const MARKET_CARD_URI = "ui://swisser/market-card-v7.html";
export const LEGACY_MARKET_CARD_URIS = [
  "ui://swisser/market-card-v6.html",
  "ui://swisser/market-card-v5.html",
  "ui://swisser/market-card-v4.html",
  "ui://swisser/market-card-v3.html",
  "ui://swisser/market-card-v2.html",
  "ui://swisser/market-card-v1.html",
];

export const marketCardHtml = String.raw`<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>SWISSER — обзор рынка</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Italianno&display=swap");

    :root {
      color-scheme: light dark;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --bull: light-dark(#4f7668, #789b8b);
      --bear: light-dark(#8b596a, #a97888);
      --wait: light-dark(#6f6b76, #9a96a1);
      --champagne: light-dark(#786132, #d5bd87);
      --silver: light-dark(#626872, #bec3cc);
      --bronze: light-dark(#7b4e34, #bc805a);
      --line: color-mix(in srgb, CanvasText 13%, transparent);
      --muted: color-mix(in srgb, CanvasText 58%, transparent);
    }

    * { box-sizing: border-box; }
    html, body { width: 100%; max-width: 100%; overflow-x: hidden; }
    body { margin: 0; padding: 0; background: transparent; color: CanvasText; }
    .card {
      width: 100%;
      max-width: none;
      min-width: 0;
      margin: 0;
      padding: 9px 10px 8px;
      border: 1px solid var(--line);
      border-radius: 9px;
      background: Canvas;
      font-size: 14px;
      line-height: 1.36;
    }
    .head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px 14px;
      flex-wrap: wrap;
      margin-bottom: 6px;
      font-size: 12px;
    }
    .context { color: var(--muted); }
    .lead { color: var(--bear); font-weight: 600; }
    .table-wrap { width: 100%; min-width: 0; overflow: visible; }
    table { width: 100%; max-width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td {
      min-width: 0;
      padding: 7px 7px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      overflow-wrap: break-word;
    }
    th { color: var(--muted); font-size: 12.5px; font-weight: 600; text-align: left; }
    .overview th:nth-child(1) { width: 12%; }
    .overview th:nth-child(2) { width: 14%; }
    .overview th:nth-child(3) { width: 11%; }
    .overview th:nth-child(4),
    .overview th:nth-child(5),
    .overview th:nth-child(6),
    .overview th:nth-child(7) { width: 7%; }
    .overview th:nth-child(8) { width: 35%; }
    .overview th:not(:last-child),
    .overview td:not(:last-child) { white-space: nowrap; }
    .price { text-align: right; font-weight: 600; }
    .tf { text-align: center; }
    .coin { font-weight: 600; }
    .rank-1 { color: var(--champagne); }
    .rank-2 { color: var(--silver); }
    .rank-3 { color: var(--bronze); }
    .signal { font-family: "Italianno", "Segoe Script", cursive; font-size: 1.12em; font-weight: 400; }
    .bull { color: var(--bull); }
    .bear { color: var(--bear); }
    .wait { color: var(--wait); }
    .candidates { margin-top: 10px; }
    .candidates th:nth-child(1) { width: 14%; }
    .candidates th:nth-child(2) { width: 29%; }
    .candidates th:nth-child(3) { width: 24%; }
    .candidates th:nth-child(4) { width: 16%; }
    .candidates th:nth-child(5) { width: 17%; }
    .candidate-name { white-space: nowrap; }
    .targets, .pnl {
      white-space: normal;
      overflow-wrap: normal;
      word-break: normal;
      font-variant-numeric: tabular-nums;
    }
    .pnl { font-weight: 600; }
    .foot { margin-top: 8px; color: color-mix(in srgb, CanvasText 76%, transparent); }
    .command-bar {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 5px;
      margin-top: 10px;
      padding-top: 9px;
      border-top: 1px solid var(--line);
    }
    .command {
      display: flex;
      align-items: center;
      min-height: 34px;
      padding: 6px 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: color-mix(in srgb, CanvasText 3%, Canvas);
      color: inherit;
      font: inherit;
      font-size: 12px;
      text-align: left;
      cursor: pointer;
    }
    .command:hover { background: color-mix(in srgb, CanvasText 7%, Canvas); }
    .command:disabled { cursor: wait; opacity: .55; }
    .number {
      flex: 0 0 auto;
      display: inline-grid;
      place-items: center;
      width: 18px;
      height: 18px;
      margin-right: 6px;
      border: 1px solid var(--line);
      border-radius: 5px;
      font-size: 10px;
      font-weight: 700;
      color: var(--muted);
    }
    .command-status { margin-top: 5px; min-height: 14px; font-size: 10.5px; color: var(--muted); }
    .empty { padding: 16px 8px; color: var(--muted); text-align: center; }

    @media (max-width: 620px) {
      .card { padding: 7px 5px; font-size: 12.5px; line-height: 1.32; }
      .head { font-size: 11px; }
      th, td { padding: 6px 3px; }
      th { font-size: 11.5px; }
      .overview th:not(:last-child),
      .overview td:not(:last-child) { white-space: normal; }
      .overview .coin,
      .overview .price,
      .overview .tf { white-space: nowrap; }
      .candidate-name { white-space: normal; }
      .signal { font-size: 1.08em; }
      .command-bar { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <section class="card" aria-label="Рыночная карточка SWISSER">
    <div id="loading" class="empty">Формирую карточку SWISSER…</div>
    <div id="content" hidden>
      <div class="head">
        <div class="context" id="context"></div>
        <div class="lead" id="lead"></div>
      </div>

      <div class="table-wrap">
        <table class="overview">
          <thead>
            <tr>
              <th>Монета</th>
              <th class="price">Цена</th>
              <th>Идея</th>
              <th class="tf">4h</th>
              <th class="tf">1h</th>
              <th class="tf">15m</th>
              <th class="tf">1m</th>
              <th>Состояние / ориентир</th>
            </tr>
          </thead>
          <tbody id="market-rows"></tbody>
        </table>
      </div>

      <div class="table-wrap" id="candidates-wrap" hidden>
        <table class="candidates">
          <thead>
            <tr>
              <th>Кандидат</th>
              <th>Условие входа</th>
              <th>Вход / стоп-отмена</th>
              <th>Цели</th>
              <th>Потенц. PnL 6x</th>
            </tr>
          </thead>
          <tbody id="candidate-rows"></tbody>
        </table>
      </div>

      <div class="foot" id="conclusion"></div>
      <div class="command-bar" id="command-bar" hidden></div>
      <div class="command-status" id="command-status" role="status"></div>
    </div>
  </section>

  <script>
    const loading = document.getElementById("loading");
    const content = document.getElementById("content");
    const contextNode = document.getElementById("context");
    const leadNode = document.getElementById("lead");
    const marketRowsNode = document.getElementById("market-rows");
    const candidatesWrap = document.getElementById("candidates-wrap");
    const candidateRowsNode = document.getElementById("candidate-rows");
    const conclusionNode = document.getElementById("conclusion");
    const commandBarNode = document.getElementById("command-bar");
    const commandStatusNode = document.getElementById("command-status");
    const cardNode = document.querySelector(".card");
    let commandStatusTimer;
    let heightFrame;
    let heightObserver;
    let lastReportedHeight = 0;

    function reportIntrinsicHeight() {
      cancelAnimationFrame(heightFrame);
      heightFrame = requestAnimationFrame(() => {
        const height = Math.ceil(cardNode.getBoundingClientRect().height) + 2;
        if (!height || Math.abs(height - lastReportedHeight) < 2) return;
        lastReportedHeight = height;
        try {
          const pending = window.openai?.notifyIntrinsicHeight?.(height);
          if (pending?.catch) pending.catch(() => {});
        } catch (_) {
          // Older hosts may not expose dynamic-height notifications.
        }
      });
    }

    function signalClass(value) {
      const normalized = String(value || "").toLowerCase();
      if (normalized.includes("bull") || normalized.includes("long")) return "bull";
      if (normalized.includes("bear") || normalized.includes("short")) return "bear";
      return "wait";
    }

    function appendText(parent, tag, value, className = "") {
      const node = document.createElement(tag);
      if (className) node.className = className;
      node.textContent = value == null || value === "" ? "—" : String(value);
      parent.appendChild(node);
      return node;
    }

    function appendSignal(parent, value) {
      return appendText(parent, "span", value, "signal " + signalClass(value));
    }

    function priorityClass(value) {
      if (value === "top") return "rank-1";
      if (value === "secondary") return "rank-2";
      if (value === "watch") return "rank-3";
      return "";
    }

    function buildContext(data) {
      contextNode.replaceChildren();
      contextNode.append(document.createTextNode("Срез: " + data.cut_time + " · BTC " + data.btc_price + " · 4h "));
      appendSignal(contextNode, data.btc_structure.h4);
      contextNode.append(document.createTextNode(" · 1h "));
      appendSignal(contextNode, data.btc_structure.h1);
      contextNode.append(document.createTextNode(" · 15m "));
      appendSignal(contextNode, data.btc_structure.m15);
      contextNode.append(document.createTextNode(" · 1m "));
      appendSignal(contextNode, data.btc_structure.m1);
    }

    function buildMarketRows(rows) {
      marketRowsNode.replaceChildren();
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        const coinCell = appendText(tr, "td", row.symbol, "coin " + priorityClass(row.priority));
        coinCell.classList.add("coin");
        appendText(tr, "td", row.price, "price");
        const idea = document.createElement("td");
        appendSignal(idea, row.idea);
        tr.appendChild(idea);
        [row.h4, row.h1, row.m15, row.m1].forEach((value) => {
          const td = document.createElement("td");
          td.className = "tf";
          appendSignal(td, value);
          tr.appendChild(td);
        });
        appendText(tr, "td", row.note);
        marketRowsNode.appendChild(tr);
      });
    }

    function buildCandidates(rows, marketRows) {
      candidateRowsNode.replaceChildren();
      const visible = Array.isArray(rows) && rows.length > 0;
      candidatesWrap.hidden = !visible;
      if (!visible) return;
      const priorities = new Map(
        (marketRows || []).map((row) => [row.symbol, priorityClass(row.priority)]),
      );
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        const name = document.createElement("td");
        name.className = "candidate-name " + (priorities.get(row.symbol) || "");
        name.append(document.createTextNode(row.symbol + " "));
        appendSignal(name, row.direction);
        tr.appendChild(name);
        appendText(tr, "td", row.entry_condition);
        appendText(tr, "td", row.entry + " / " + row.stop_or_invalidation);
        appendText(tr, "td", row.targets.join(" → "), "targets");
        appendText(tr, "td", row.pnl_6x.join(" · "), "pnl");
        candidateRowsNode.appendChild(tr);
      });
    }

    function setCommandStatus(message, clearAfter = 0) {
      clearTimeout(commandStatusTimer);
      commandStatusNode.textContent = message;
      if (clearAfter) {
        commandStatusTimer = setTimeout(() => { commandStatusNode.textContent = ""; }, clearAfter);
      }
    }

    function preparedPrompt(command) {
      const expected = command.expected_symbols || [];
      if (command.mode !== "entry" || !expected.length) return command.prompt;
      return command.prompt + "\n\nСохранённые кандидаты этой карточки: "
        + expected.join(", ") + ". Вызови scan_swisser_markets с mode=entry "
        + "и передай ровно этот список как expected_symbols.";
    }

    async function sendCommand(command, buttons) {
      buttons.forEach((button) => { button.disabled = true; });
      try {
        if (!window.openai?.sendFollowUpMessage) {
          throw new Error("Команды недоступны в этом режиме ChatGPT");
        }
        const prompt = preparedPrompt(command);
        setCommandStatus("Отправляю…");
        await window.openai.sendFollowUpMessage({ prompt, scrollToBottom: true });
        setCommandStatus("Отправлено.", 1200);
      } catch (error) {
        setCommandStatus(error?.message || "Не удалось отправить запрос.");
      } finally {
        setTimeout(() => buttons.forEach((button) => { button.disabled = false; }), 700);
      }
    }

    function buildCommands(commands) {
      commandBarNode.replaceChildren();
      const visible = Array.isArray(commands) && commands.length > 0;
      commandBarNode.hidden = !visible;
      if (!visible) return;
      const buttons = commands.map((command, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "command";
        const number = appendText(button, "span", command.id || index + 1, "number");
        number.setAttribute("aria-hidden", "true");
        button.append(document.createTextNode(command.label || "Команда " + (index + 1)));
        button.addEventListener("click", () => sendCommand(command, buttons));
        commandBarNode.appendChild(button);
        return button;
      });
    }

    function render(data) {
      if (!data || !Array.isArray(data.market_rows) || !data.btc_structure) return;
      buildContext(data);
      leadNode.textContent = data.lead;
      leadNode.className = "lead " + signalClass(data.lead);
      buildMarketRows(data.market_rows);
      buildCandidates(data.candidates || [], data.market_rows);
      conclusionNode.textContent = "Итог: " + data.conclusion;
      buildCommands(data.commands || []);
      loading.hidden = true;
      content.hidden = false;
      reportIntrinsicHeight();
    }

    window.addEventListener("openai:set_globals", (event) => {
      render(event.detail?.globals?.toolOutput);
    });
    window.addEventListener("resize", reportIntrinsicHeight, { passive: true });
    if (typeof ResizeObserver === "function") {
      heightObserver = new ResizeObserver(reportIntrinsicHeight);
      heightObserver.observe(cardNode);
    }
    if (document.fonts?.ready) document.fonts.ready.then(reportIntrinsicHeight);
    render(window.openai?.toolOutput);
  </script>
</body>
</html>`;
