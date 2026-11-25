// LM Studio Prompt Enhancer: the custom node face.
//
// Every setting is still a normal ComfyUI widget underneath. The panel only hides those widgets and
// draws controls bound to them, so the node runs and saves the same if this file fails to load.
// Look adapted from AusBoss nodes (MIT, github.com/ausboss/ComfyUI-AusBoss).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "LMStudioPromptEnhancer";
const BASE = new URL(".", import.meta.url).href;
const SAVED = "lmse_saved";
const PLAIN = "lmse_plain";
const TOKEN_SETTING = "LMStudioPromptEnhancer.ApiToken";
const panels = new Set();


const ICONS = {
  gear: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="2.3"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/></svg>',
  reload: '<svg viewBox="0 0 16 16"><path d="M13 8a5 5 0 1 1-1.5-3.6"/><path d="M13 2.5v3h-3"/></svg>',
  folder: '<svg viewBox="0 0 16 16"><path d="M1.8 4.2h4.3l1.4 1.5h6.7v7.1H1.8z"/></svg>',
  copy: '<svg viewBox="0 0 16 16"><rect x="5" y="5" width="8.5" height="8.5" rx="1.5"/><path d="M3 10.5V3.5A1 1 0 0 1 4 2.5h6.5"/></svg>',
};

// ---------------------------------------------------------------------------------------------
// small helpers

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function iconButton(name, title, onClick) {
  const b = el("button", "lmse-icon");
  b.type = "button";
  b.title = title;
  b.innerHTML = ICONS[name] ?? name;
  b.addEventListener("click", (e) => { e.stopPropagation(); onClick(b); });
  return b;
}

const findWidget = (node, name) => node.widgets?.find((w) => w.name === name);

function loadCss() {
  if (document.getElementById("lmse-css")) return;
  const link = document.createElement("link");
  link.id = "lmse-css";
  link.rel = "stylesheet";
  link.href = BASE + "panel.css";
  document.head.append(link);
}

async function getJSON(path, init) {
  const res = await api.fetchApi(path, init);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

// Hide a canvas widget in place (it stays in node.widgets, so saved values keep their order).
function hideWidget(w) {
  if (!w || w.__lmseHidden) return;
  w.__lmseHidden = { computeSize: w.computeSize, draw: w.draw, type: w.type, hidden: w.hidden,
                     own: { computeSize: Object.hasOwn(w, "computeSize"), draw: Object.hasOwn(w, "draw") } };
  w.hidden = true;
  if (w.options) w.options.hidden = true;
  w.computeSize = () => [0, -4];
  w.draw = () => {};
  if (w.element) w.element.style.display = "none";
}

function showWidget(w) {
  const s = w?.__lmseHidden;
  if (!s) return;
  for (const key of ["computeSize", "draw"]) {
    if (s.own[key]) w[key] = s[key]; else delete w[key];
  }
  w.hidden = s.hidden;
  if (w.options) w.options.hidden = false;
  if (w.element) w.element.style.display = "";
  delete w.__lmseHidden;
}

// ---------------------------------------------------------------------------------------------
// shared data

let presetCache = null;
async function presetList(force = false) {
  if (!presetCache || force) {
    presetCache = getJSON("/lmse/presets").catch((e) => { presetCache = null; throw e; });
  }
  return presetCache;
}

function renderMarkdown(md) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const out = [];
  let list = false;
  let pre = null;
  for (const line of md.split(/\r?\n/)) {
    if (pre !== null) {
      if (line.startsWith("```")) { out.push(`<pre>${esc(pre.join("\n"))}</pre>`); pre = null; } else pre.push(line);
      continue;
    }
    if (line.startsWith("```")) { pre = []; continue; }
    const item = /^\s*[-*] (.*)$/.exec(line);
    if (item) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(item[1])}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    const h = /^(#{1,3}) (.*)$/.exec(line);
    if (h) out.push(`<h${h[1].length === 1 ? 1 : 2}>${inline(h[2])}</h${h[1].length === 1 ? 1 : 2}>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}

async function showHelp() {
  const shade = el("div", "lmse-help");
  const card = el("div", "lmse-help-card", "Loading…");
  shade.append(card);
  shade.addEventListener("click", (e) => { if (e.target === shade) shade.remove(); });
  const onKey = (e) => { if (e.key === "Escape") { shade.remove(); document.removeEventListener("keydown", onKey); } };
  document.addEventListener("keydown", onKey);
  document.body.append(shade);
  try {
    const res = await fetch(BASE + `docs/${NODE}.md`);
    card.innerHTML = renderMarkdown(await res.text());
  } catch (e) {
    card.textContent = `Couldn't load the help page: ${e}`;
  }
}

// ---------------------------------------------------------------------------------------------
// the panel

class Panel {
  constructor(node) {
    this.node = node;
    this.syncs = [];
    this.presets = [];
    this.models = [];
    this.showAdvanced = false;
    this.showGear = false;
    this.lastMin = null;
    this.live = { image: false, idea: false, clip: false };
    this.build();
    panels.add(this);
    // Bypassing or muting a node upstream fires no event here, so the live state is polled.
    this.timer = setInterval(() => this.liveCheck(), 400);
    this.liveCheck(true);
  }

  // -- live state: is a picture / idea actually arriving? -------------------------------------

  // True when the input is connected and the node it comes from will run. A bypassed node passes
  // on its own input of the same type, if it has one connected; a muted node sends nothing.
  sourceActive(name, type) {
    const node = this.node;
    const graph = node.graph;
    let slot = node.inputs?.findIndex((i) => i.name === name) ?? -1;
    if (slot < 0 || !graph) return false;
    let target = node;
    for (let hops = 0; hops < 32; hops++) {
      const link = target.getInputLink ? target.getInputLink(slot) : graph.links?.[target.inputs[slot]?.link];
      if (!link) return false;
      const origin = graph.getNodeById?.(link.origin_id);
      if (!origin) return true; // a subgraph input or something unusual: assume it sends
      if (origin.mode === 2) return false; // muted ("never")
      if (origin.mode !== 4) return true; // runs normally
      slot = origin.inputs?.findIndex((i, k) => (i.type === type || i.type === "*") &&
        (origin.isInputConnected ? origin.isInputConnected(k) : i.link != null)) ?? -1;
      if (slot < 0) return false; // bypassed with nothing to pass on
      target = origin;
    }
    return true;
  }

  liveCheck(force = false) {
    if (!this.node.graph && !force) return;
    const live = { image: this.sourceActive("image", "IMAGE"), idea: this.sourceActive("idea", "STRING"),
                   clip: this.sourceActive("clip", "CLIP") };
    if (!force && ["image", "idea", "clip"].every((k) => live[k] === this.live[k])) return;
    this.live = live;
    this.markWordsBox();
    if (this.presetChips) this.refresh();
  }

  markWordsBox() {
    const w = this.w("words");
    const box = w?.inputEl ?? w?.element;
    if (!box) return;
    box.style.opacity = this.live.idea ? "0.4" : "";
    box.title = this.live.idea ? "Ignored: the idea socket is connected, so its text is used instead." : "";
  }

  stop() {
    clearInterval(this.timer);
    panels.delete(this);
  }

  w(name) { return findWidget(this.node, name); }
  get(name) { return this.w(name)?.value; }
  set(name, value) {
    const w = this.w(name);
    if (!w || w.value === value) return;
    w.value = value;
    w.callback?.(value, app.canvas, this.node);
    this.node.setDirtyCanvas?.(true, true);
    this.refresh();
  }
  linked(name) {
    const i = this.node.inputs?.findIndex((input) => input.name === name || input.widget?.name === name) ?? -1;
    if (i < 0) return false;
    return this.node.isInputConnected ? this.node.isInputConnected(i) : this.node.inputs[i].link != null;
  }

  // -- controls bound to widgets --------------------------------------------------------------

  row(label, ...controls) {
    const row = el("div", "lmse-row");
    const l = el("span", "lmse-label", label);
    const c = el("div", "lmse-control");
    c.append(...controls);
    row.append(l, c);
    return row;
  }

  tooltip(name) {
    const spec = this.node.constructor?.nodeData?.input;
    const s = spec?.required?.[name] ?? spec?.optional?.[name];
    return Array.isArray(s) ? s[1]?.tooltip ?? "" : "";
  }

  select(name, options, labels) {
    const s = el("select", "lmse-select");
    s.title = this.tooltip(name);
    const fill = (opts) => {
      s.replaceChildren(...opts.map((o, i) => { const op = el("option", "", labels?.[i] ?? o); op.value = o; return op; }));
    };
    fill(options);
    s.addEventListener("change", () => this.set(name, s.value));
    this.syncs.push(() => {
      const v = this.get(name);
      if (v !== undefined && ![...s.options].some((o) => o.value === String(v))) {
        const op = el("option", "", String(v)); op.value = v; s.append(op);
      }
      if (v !== undefined) s.value = v;
      s.disabled = this.linked(name);
    });
    s.fill = fill;
    return s;
  }

  seg(name, options, labels) {
    const box = el("div", "lmse-seg");
    box.title = this.tooltip(name);
    const buttons = options.map((o, i) => {
      const b = el("button", "", labels?.[i] ?? o);
      b.type = "button";
      b.addEventListener("click", () => this.set(name, o));
      return b;
    });
    box.append(...buttons);
    this.syncs.push(() => buttons.forEach((b, i) => b.classList.toggle("on", this.get(name) === options[i])));
    return box;
  }

  text(name, placeholder, cls = "") {
    const t = el("input", "lmse-text " + cls);
    t.type = "text";
    t.placeholder = placeholder ?? "";
    t.title = this.tooltip(name);
    t.addEventListener("change", () => this.set(name, t.value));
    this.syncs.push(() => { if (document.activeElement !== t) t.value = this.get(name) ?? ""; t.disabled = this.linked(name); });
    return t;
  }

  area(name, placeholder, cls = "") {
    const t = el("textarea", "lmse-area " + cls);
    t.placeholder = placeholder ?? "";
    t.title = this.tooltip(name);
    t.spellcheck = false;
    t.addEventListener("change", () => this.set(name, t.value));
    t.addEventListener("input", () => { const w = this.w(name); if (w) w.value = t.value; });
    this.syncs.push(() => { if (document.activeElement !== t) t.value = this.get(name) ?? ""; });
    return t;
  }

  number(name, { step = 1, float = false } = {}) {
    const n = el("input", "lmse-num");
    n.type = "number";
    n.step = String(step);
    n.title = this.tooltip(name);
    const opts = this.w(name)?.options ?? {};
    if (opts.min !== undefined) n.min = opts.min;
    if (opts.max !== undefined) n.max = opts.max;
    n.addEventListener("change", () => {
      let v = float ? parseFloat(n.value) : parseInt(n.value, 10);
      if (Number.isNaN(v)) { n.value = this.get(name); return; }
      if (opts.min !== undefined) v = Math.max(opts.min, v);
      if (opts.max !== undefined) v = Math.min(opts.max, v);
      this.set(name, v);
    });
    this.syncs.push(() => { if (document.activeElement !== n) n.value = this.get(name) ?? ""; n.disabled = this.linked(name); });
    return n;
  }

  tick(name) {
    const t = el("input", "lmse-tick");
    t.type = "checkbox";
    t.title = this.tooltip(name) || "On: the node's value is sent. Off: LM Studio's own setting applies.";
    t.addEventListener("change", () => this.set(name, t.checked));
    this.syncs.push(() => { t.checked = !!this.get(name); });
    return t;
  }

  // Dims a row while its setting can't have any effect, with the reason as its tooltip.
  inert(row, reason) {
    this.syncs.push(() => {
      const why = reason();
      row.classList.toggle("dim", !!why);
      row.title = why || "";
    });
    return row;
  }

  ticked(label, name, control) {
    const t = this.tick(`use_${name}`);
    const row = this.row(label, t, control);
    this.syncs.push(() => { control.style.opacity = this.get(`use_${name}`) ? "1" : ".45"; });
    return row;
  }

  switch(name, note) {
    const s = el("button", "lmse-switch");
    s.type = "button";
    s.title = this.tooltip(name);
    s.addEventListener("click", () => this.set(name, !this.get(name)));
    const wrap = el("div", "lmse-control");
    wrap.append(s);
    let noteEl = null;
    if (note) { noteEl = el("span", "lmse-switch-note", ""); wrap.append(noteEl); }
    this.syncs.push(() => {
      s.classList.toggle("on", !!this.get(name));
      if (noteEl) noteEl.textContent = typeof note === "function" ? note(!!this.get(name)) : note;
    });
    return wrap;
  }

  // -- build ----------------------------------------------------------------------------------

  build() {
    const node = this.node;
    // Everything but the words box, including extras the frontend adds (the model list's refresh).
    this.hidden = node.widgets.filter((w) => w.name !== "words");
    this.hidden.forEach(hideWidget);

    // top: header, preset, slots (above the native words box)
    this.top = el("div", "lmse");
    const head = el("div", "lmse-card");
    const bar = el("div", "lmse-head");
    this.statusButton = el("button", "lmse-status");
    this.statusButton.type = "button";
    this.statusButton.title = "Test: is LM Studio answering, is the model loaded, does it accept images?";
    this.dot = el("span", "lmse-dot");
    this.statusText = el("span", "", "LM Studio · click to test");
    this.statusButton.append(this.dot, this.statusText);
    this.statusButton.addEventListener("click", () => this.test());
    this.gearButton = iconButton("gear", "Connection and picture settings", () => {
      this.showGear = !this.showGear; this.refresh(); this.fit();
    });
    bar.append(this.statusButton, iconButton("?", "Help", showHelp), this.gearButton);
    head.append(bar);

    // gear card
    this.gear = el("div", "lmse-card");
    this.gear.append(el("div", "lmse-section", "Connection"));
    this.gear.append(this.row("Server URL", this.text("server_url", "http://127.0.0.1:1234", "mono")));
    this.gear.append(this.row("API", this.select("api_mode", this.w("api_mode")?.options?.values ?? [])));
    const token = el("input", "lmse-text mono");
    token.type = "password";
    token.placeholder = "only if LM Studio asks for one";
    token.title = "Saved in ComfyUI's settings, never in the workflow, so shared workflows don't leak it.";
    token.addEventListener("change", () => app.extensionManager?.setting?.set?.(TOKEN_SETTING, token.value.trim()));
    this.syncs.push(() => { if (document.activeElement !== token) token.value = app.extensionManager?.setting?.get?.(TOKEN_SETTING) ?? ""; });
    this.gear.append(this.row("API token", token));
    this.gear.append(this.row("Timeout (s)", this.number("timeout")));
    this.gear.append(this.inert(this.row("Stream", this.switch("stream", (on) => on ? "text appears as it's written" : "")),
      () => this.get("api_mode") === "Native" && "The native endpoint doesn't stream; switch API to OpenAI-compatible."));
    this.gear.append(el("div", "lmse-section", "Pictures"));
    const noPicture = () => !this.live.image && "Only used while an image is connected and its node is on.";
    this.gear.append(this.inert(this.row("Longest side", this.number("max_image_side", { step: 8 })), noPicture));
    this.gear.append(this.inert(this.row("Batch", this.seg("image_batch", ["first", "all"], ["First image", "All images"])), noPicture));
    this.gear.append(el("div", "lmse-section", "Other"));
    this.gear.append(this.row("Debug", this.switch("debug", (on) => on ? "full request and reply in the console" : "")));
    const plain = el("button", "lmse-seg");
    plain.type = "button";
    plain.style.cssText = "display:block;color:var(--lmse-label);font:600 11px var(--lmse-font);cursor:pointer;border:none";
    plain.textContent = "Show plain widgets instead of this panel";
    plain.addEventListener("click", () => { node.properties[PLAIN] = true; this.destroy(); });
    this.gear.append(this.row("", plain));

    // preset card
    const presetCard = el("div", "lmse-card");
    presetCard.append(el("div", "lmse-section", "Preset"));
    this.presetSelect = this.select("preset", this.w("preset")?.options?.values ?? []);
    this.presetSelect.addEventListener("change", () => this.applyPresetSettings(this.presetSelect.value));
    presetCard.append(this.row("Preset", this.presetSelect,
      iconButton("reload", "Reload presets from disk", (b) => this.loadPresets(true, b)),
      iconButton("folder", "Open your presets folder", () => this.openFolder())));
    this.presetInfo = el("div", "lmse-desc");
    this.presetChips = el("div", "lmse-chips");
    presetCard.append(this.presetInfo, this.presetChips);
    this.slotBox = el("div", "lmse-card");
    this.slotBox.style.cssText = "padding:0;background:transparent";
    presetCard.append(this.slotBox);
    this.top.append(head, this.gear, presetCard);

    // bottom: main, advanced, result (below the native words box)
    this.bottom = el("div", "lmse");
    const main = el("div", "lmse-card");
    main.append(el("div", "lmse-section", "Model"));
    this.modelSelect = this.select("model", []);
    main.append(this.row("Model", this.modelSelect, iconButton("reload", "Refresh the model list", (b) => this.loadModels(b))));
    this.lengthRow = this.row("Length", this.seg("length", ["short", "medium", "long"], ["Short", "Medium", "Long"]));
    this.triggerRow = this.row("Trigger words", this.text("trigger_words", "LoRA trigger words, kept exactly"));
    main.append(this.lengthRow, this.triggerRow);
    main.append(this.ticked("Temperature", "temperature", this.number("temperature", { step: 0.05, float: true })));
    main.append(this.ticked("Max tokens", "max_tokens", this.number("max_tokens", { step: 50 })));
    const seedControl = this.w("control_after_generate");
    const seedParts = [this.number("seed")];
    if (seedControl) seedParts.push(this.select("control_after_generate", seedControl.options?.values ?? ["fixed", "increment", "decrement", "randomize"]));
    const seedBox = el("div", "lmse-control");
    seedBox.append(...seedParts);
    seedBox.style.flex = "1 1 auto";
    main.append(this.ticked("Seed", "seed", seedBox));
    main.append(this.row("Thinking", this.seg("reasoning", ["off", "low", "medium", "high", "model default"],
      ["Off", "Low", "Med", "High", "Model"])));
    main.append(this.row("Hold", this.switch("hold", (on) => on ? "wrapped up: last prompt kept, no call to LM Studio" : "")));

    // advanced card
    const adv = el("div", "lmse-card");
    this.advToggle = el("button", "lmse-section", "Advanced ▸");
    this.advToggle.type = "button";
    this.advToggle.addEventListener("click", () => { this.showAdvanced = !this.showAdvanced; this.refresh(); this.fit(); });
    this.advBody = el("div", "lmse-card");
    this.advBody.style.cssText = "padding:0;background:transparent";
    const a = this.advBody;
    a.append(this.ticked("top_p", "top_p", this.number("top_p", { step: 0.01, float: true })));
    a.append(this.ticked("top_k", "top_k", this.number("top_k")));
    a.append(this.inert(this.ticked("min_p", "min_p", this.number("min_p", { step: 0.01, float: true })),
      () => this.get("api_mode") === "OpenAI-compatible" && "min_p only reaches LM Studio through the Native API (gear)."));
    a.append(this.ticked("Repeat penalty", "repeat_penalty", this.number("repeat_penalty", { step: 0.01, float: true })));
    const stopRow = this.ticked("Stop", "stop", this.area("stop", "one stop string per line"));
    stopRow.classList.add("top");
    a.append(stopRow);
    a.append(this.inert(this.row("Thinking room", this.number("thinking_budget", { step: 128 })),
      () => (!["low", "medium", "high"].includes(this.get("reasoning")) || !this.get("use_max_tokens")) &&
        "Added to max tokens only while thinking is Low, Med or High and the max tokens tick is on."));
    a.append(this.inert(this.row("Checks", this.switch("checks", (on) => on ? "retry once if a check fails" : "")),
      () => (this.preset()?.structured || this.get("use_response_format")) && "Skipped for JSON output."));
    a.append(this.row("Variations", this.number("variations")));
    a.append(this.row("One per line", this.switch("one_per_line", (on) => on ? "each line is its own idea" : "")));
    a.append(this.inert(this.row("Token limit", this.number("token_limit")),
      () => !this.live.clip && "Connect your text encoder to the clip socket to count tokens."));
    a.append(this.row("History", this.switch("history", (on) => on ? "saved to history.jsonl" : "")));
    a.append(this.row("On error", this.seg("on_error", ["Stop", "Pass through"])));
    const fmtRow = this.ticked("JSON schema", "response_format", this.area("response_format", "a JSON schema (overrides the preset's)", "mono"));
    fmtRow.classList.add("top");
    a.append(fmtRow);
    const extraRow = this.row("Extra JSON", this.area("extra_json", '{"presence_penalty": 0.2}', "mono"));
    extraRow.classList.add("top");
    a.append(extraRow);
    adv.append(this.advToggle, this.advBody);

    // result card
    const res = el("div", "lmse-card");
    const resHead = el("div", "lmse-section", "Result");
    this.copyButton = iconButton("copy", "Copy the prompt", () => this.copy());
    this.copyButton.style.cssText = "width:20px;height:20px";
    const resBar = el("div", "lmse-head");
    resHead.style.flex = "1";
    resBar.append(resHead, this.copyButton);
    this.result = el("div", "lmse-result empty", "Nothing in the jar yet: your prompt appears here after a run.");
    this.info = el("div", "lmse-info");
    this.warn = el("div", "lmse-warn");
    this.warn.hidden = true;
    res.append(resBar, this.result, this.warn, this.info);
    this.bottom.append(main, adv, res);

    // stop canvas drag/zoom from stealing clicks, wheel and keys inside the panel
    for (const root of [this.top, this.bottom]) {
      root.addEventListener("pointerdown", (e) => {
        if (e.target.closest("button,input,select,textarea,.lmse-result")) e.stopPropagation();
      });
      root.addEventListener("wheel", (e) => { if (e.target.closest("textarea,.lmse-result,select")) e.stopPropagation(); }, { passive: true });
      root.addEventListener("keydown", (e) => e.stopPropagation());
    }

    this.topWidget = this.mount("lmse_top", this.top);
    this.bottomWidget = this.mount("lmse_bottom", this.bottom);
    this.place();
    const saved = node.properties?.[SAVED];
    if (saved) this.showResult(saved, false);
    this.refresh();
    this.loadPresets(false);
    this.loadModels();
    requestAnimationFrame(() => this.fit(true));
  }

  // The frontend sizes a DOM widget's element itself, so the panel content sits in a wrapper and
  // its natural height is what the node is told.
  mount(name, element) {
    const wrapper = el("div");
    wrapper.style.cssText = "width:100%;overflow:visible";
    wrapper.append(element);
    const height = () => (Math.ceil(element.getBoundingClientRect().height / (app.canvas?.ds?.scale || 1)) || element.scrollHeight || 60) + 6;
    const widget = this.node.addDOMWidget(name, "lmse_panel", wrapper, {
      serialize: false,
      hideOnZoom: false,
      getMinHeight: height,
      getMaxHeight: height,
    });
    widget.serialize = false;
    widget.computeSize = (width) => [width ?? this.node.size[0], height()];
    const ro = new ResizeObserver(() => this.fit());
    ro.observe(element);
    widget.__lmseObserver = ro;
    return widget;
  }

  // Panel widgets go around the native words box: header and preset above, the rest below.
  place() {
    const ws = this.node.widgets;
    for (const w of [this.topWidget, this.bottomWidget]) ws.splice(ws.indexOf(w), 1);
    const words = ws.findIndex((w) => w.name === "words");
    const at = words < 0 ? 0 : words;
    ws.splice(at, 0, this.topWidget);
    ws.splice(at + 2, 0, this.bottomWidget);
  }

  fit(initial = false) {
    const node = this.node;
    if (!node.graph && !initial) return;
    const min = node.computeSize?.();
    if (!min) return;
    const extra = this.lastMin == null ? 120 : Math.max(0, node.size[1] - this.lastMin);
    this.lastMin = min[1];
    const h = Math.max(min[1], min[1] + extra);
    if (Math.abs(h - node.size[1]) > 1 || node.size[0] < 340) {
      node.setSize([Math.max(node.size[0], 340), h]);
      node.setDirtyCanvas?.(true, true);
    }
  }

  destroy() {
    this.stop();
    this.live.idea = false;
    this.markWordsBox();
    for (const w of [this.topWidget, this.bottomWidget]) {
      w?.__lmseObserver?.disconnect();
      const i = this.node.widgets.indexOf(w);
      if (i >= 0) this.node.widgets.splice(i, 1);
      w?.onRemove?.();
      w?.element?.remove();
    }
    (this.hidden ?? []).forEach(showWidget);
    this.node.panel = null;
    this.node.setSize(this.node.computeSize());
    this.node.setDirtyCanvas?.(true, true);
  }

  // -- state ----------------------------------------------------------------------------------

  preset() { return this.presets.find((p) => p.name === this.get("preset")); }

  refresh() {
    this.syncs.forEach((f) => f());
    this.gear.hidden = !this.showGear;
    this.gearButton.classList.toggle("on", this.showGear);
    this.advBody.hidden = !this.showAdvanced;
    this.advToggle.textContent = this.showAdvanced ? "Advanced ▾" : "Advanced ▸";
    const p = this.preset();
    if (p) {
      this.presetInfo.textContent = p.description || "";
      const chips = [];
      const picture = this.live.image;
      if (p.mode === "auto" || p.mode === "both") {
        chips.push([picture ? "picture → text" : "words → text", ""]);
        chips.push([picture ? "image connected" : "no image: connect one to describe it", "plain"]);
      } else if (p.mode === "words") {
        chips.push(["words → text", ""]);
        if (picture) chips.push(["this preset ignores the image", "warn"]);
      } else {
        chips.push(["picture → text", ""]);
        if (!picture) chips.push(["needs an image", "warn"]);
      }
      if (this.live.idea) chips.push(["idea from socket", ""]);
      if (p.structured) chips.push(["JSON output", ""]);
      if (p.negative) chips.push(["+ negative", ""]);
      chips.push([p.source === "user" ? "your preset" : "shipped", "plain"]);
      this.presetChips.replaceChildren(...chips.map(([t, c]) => el("span", "lmse-chip " + c, t)));
      this.lengthRow.classList.toggle("dim", !p.uses_length);
      this.lengthRow.title = p.uses_length ? "" : "This preset has no {length} slot.";
      this.triggerRow.title = p.uses_trigger ? "" : "This preset has no {trigger} slot: trigger words aren't sent, but they're still checked in the reply.";
    }
    this.renderSlots(p);
  }

  slotValues() {
    const out = {};
    let cur = null;
    for (const line of String(this.get("slots") ?? "").split(/\r?\n/)) {
      const m = /^([a-z_][a-z0-9_]*)\s*:\s?(.*)$/.exec(line);
      if (m) { cur = m[1]; out[cur] = m[2]; } else if (cur && line.trim()) out[cur] += "\n" + line;
    }
    return out;
  }

  writeSlots(values) {
    const text = Object.entries(values).map(([k, v]) => `${k}: ${v}`).join("\n");
    this.set("slots", text);
  }

  renderSlots(p) {
    const names = Object.keys(p?.slots ?? {});
    const key = names.join(",") + "|" + (p?.name ?? "");
    if (this.slotKey === key) {
      const values = this.slotValues();
      for (const input of this.slotBox.querySelectorAll("input")) {
        if (document.activeElement !== input) input.value = values[input.dataset.slot] ?? p.slots[input.dataset.slot] ?? "";
      }
      return;
    }
    this.slotKey = key;
    this.slotBox.replaceChildren();
    this.slotBox.hidden = names.length === 0;
    const values = this.slotValues();
    for (const name of names) {
      const input = el("input", "lmse-text");
      input.dataset.slot = name;
      input.placeholder = p.slots[name] ? `default: ${p.slots[name]}` : `{${name}}`;
      input.value = values[name] ?? p.slots[name] ?? "";
      input.addEventListener("change", () => {
        const v = this.slotValues();
        v[name] = input.value;
        this.writeSlots(v);
      });
      this.slotBox.append(this.row(name.replace(/_/g, " "), input));
    }
    if (names.length) requestAnimationFrame(() => this.fit());
  }

  async loadPresets(force, button) {
    button?.classList.add("on");
    try {
      const data = await presetList(force);
      this.presets = data.presets ?? [];
      const names = this.presets.map((p) => p.name);
      const w = this.w("preset");
      if (w?.options) w.options.values = names;
      this.presetSelect.fill(names);
      // An old preset name (e.g. "MOSS from words") moves to the preset that took it over.
      const current = this.get("preset");
      if (current && !names.includes(current)) {
        const heir = this.presets.find((p) => (p.aliases ?? []).includes(current));
        if (heir) this.set("preset", heir.name);
      }
      if (data.problems?.length) this.showWarnings(data.problems.map((p) => "Preset problem: " + p));
      if (force) for (const p of panels) if (p !== this) p.loadPresets(false);
    } catch (e) {
      this.showWarnings([`Couldn't list presets: ${e.message ?? e}`]);
    } finally {
      button?.classList.remove("on");
      this.refresh();
    }
  }

  applyPresetSettings(name) {
    const p = this.presets.find((x) => x.name === name);
    if (!p) return;
    for (const [key, value] of Object.entries(p.settings ?? {})) {
      if (this.w(`use_${key}`)) this.set(`use_${key}`, true);
      if (this.w(key)) this.set(key, value);
    }
    const values = this.slotValues();
    let changed = false;
    for (const [slot, def] of Object.entries(p.slots ?? {})) {
      if (!(slot in values)) { values[slot] = def; changed = true; }
    }
    if (changed) this.writeSlots(values);
    this.refresh();
  }

  async openFolder() {
    try {
      const r = await getJSON("/lmse/open_folder", { method: "POST" });
      if (!r.ok) this.showWarnings([`Couldn't open ${r.path}: ${r.error}`]);
      else this.setStatus("", `Presets folder: ${r.path}`);
    } catch (e) {
      this.showWarnings([`Couldn't open the folder: ${e.message ?? e}`]);
    }
  }

  async loadModels(button) {
    button?.classList.add("on");
    const url = encodeURIComponent(this.get("server_url") ?? "");
    try {
      const data = await getJSON(`/lmse/models?detail=1&server_url=${url}`);
      if (!data.ok) {
        this.setStatus("bad", data.error);
        return;
      }
      this.models = data.models;
      const ids = this.models.map((m) => m.id);
      const labels = this.models.map((m) => `${m.loaded === false ? "○" : "●"} ${m.id}${m.vision ? " · images" : ""}${m.loaded === false ? " (not loaded)" : ""}`);
      const w = this.w("model");
      if (w?.options) w.options.values = ids;
      if (!this.get("model") && ids.length) {
        const first = this.models.find((m) => m.loaded) ?? this.models[0];
        this.set("model", first.id);
      }
      this.modelSelect.fill(ids, labels);
      this.refresh();
      const m = this.models.find((x) => x.id === this.get("model"));
      if (m) this.setStatus(m.loaded === false ? "warn" : "ok", m.loaded === false ? `${m.id} isn't loaded` : `LM Studio · ${m.id}`);
      else this.setStatus("warn", "LM Studio answered · pick a model");
    } catch (e) {
      this.setStatus("bad", `Couldn't reach ComfyUI's model route: ${e.message ?? e}`);
    } finally {
      button?.classList.remove("on");
    }
  }

  async test() {
    this.setStatus("busy", "Testing…");
    try {
      const r = await getJSON("/lmse/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_url: this.get("server_url"), model: this.get("model") }),
      });
      this.setStatus(r.ok ? "ok" : r.reachable ? "warn" : "bad", r.message);
      if (r.reachable) this.loadModels();
    } catch (e) {
      this.setStatus("bad", String(e.message ?? e));
    }
  }

  setStatus(kind, text) {
    this.dot.className = "lmse-dot " + (kind || "");
    this.statusText.textContent = text;
    this.statusButton.title = text + "\nClick to test again.";
  }

  // -- results --------------------------------------------------------------------------------

  showResult(data, live) {
    const prompts = [].concat(data.prompt ?? []);
    const negatives = [].concat(data.negative ?? []);
    this.result.classList.remove("empty", "error");
    this.result.replaceChildren();
    prompts.forEach((p, i) => {
      if (i) this.result.append(el("span", "lmse-sep"));
      this.result.append(document.createTextNode(p));
      if (negatives[i]) this.result.append(el("span", "lmse-think", `Negative: ${negatives[i]}`));
    });
    if (!prompts.length) { this.result.classList.add("empty"); this.result.textContent = "(empty)"; }
    this.lastPrompts = prompts;
    const infos = [].concat(data.info ?? []);
    this.info.textContent = infos.length > 1 ? `${infos.length} prompts · ${infos[0]}` : (infos[0] ?? "");
    this.showWarnings(data.warnings ?? []);
    if (live) {
      if (data.held) this.setStatus("ok", "Held: same sweet prompt, no call made");
      else if ((data.warnings ?? []).some((w) => w.startsWith("Passed through"))) this.setStatus("bad", "Failed · words passed through");
      else this.setStatus("ok", infos[0]?.split(" · ").slice(0, 1).join("") || "Done");
    }
  }

  showStream(text, thinking) {
    this.result.classList.remove("empty", "error");
    this.result.replaceChildren();
    if (thinking) this.result.append(el("span", "lmse-think", thinking));
    this.result.append(document.createTextNode(text || "…"));
    this.result.scrollTop = this.result.scrollHeight;
    this.setStatus("busy", "Writing…");
  }

  showError(message) {
    this.result.classList.remove("empty");
    this.result.classList.add("error");
    this.result.textContent = message.trim();
    this.info.textContent = "";
    this.showWarnings([]);
    this.setStatus("bad", message.split("\n")[0]);
  }

  showWarnings(list) {
    this.warn.hidden = !list.length;
    this.warn.textContent = list.join("\n");
  }

  async copy() {
    const text = (this.lastPrompts ?? []).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      this.copyButton.classList.add("on");
      setTimeout(() => this.copyButton.classList.remove("on"), 700);
    } catch {
      const t = el("textarea");
      t.value = text;
      document.body.append(t);
      t.select();
      document.execCommand("copy");
      t.remove();
    }
  }
}

function panelFor(nodeId) {
  const id = String(nodeId ?? "").split(":").pop();
  for (const p of panels) if (String(p.node.id) === id) return p;
  return null;
}

function attach(node) {
  if (node.panel || node.properties?.[PLAIN]) return;
  try {
    node.panel = new Panel(node);
  } catch (e) {
    console.error("[LM Studio Prompt Enhancer] panel failed, using plain widgets:", e);
    node.widgets?.forEach(showWidget);
    node.panel = null;
  }
}

app.registerExtension({
  name: "LMStudioPromptEnhancer.Panel",
  settings: [
    {
      id: TOKEN_SETTING,
      category: ["LM Studio Prompt Enhancer", "Connection", "API token"],
      name: "LM Studio API token",
      tooltip: "Only needed when LM Studio's server requires one. Kept in ComfyUI's settings, not in workflows.",
      type: "text",
      defaultValue: "",
    },
  ],

  setup() {
    loadCss();
    api.addEventListener("lmse.stream", ({ detail }) => panelFor(detail?.node)?.showStream(detail.text, detail.thinking));
    api.addEventListener("execution_error", ({ detail }) => {
      const p = panelFor(detail?.node_id);
      if (p && detail?.node_type === NODE) p.showError(detail.exception_message ?? "Failed.");
    });
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE) return;
    nodeType.nodeData = nodeData;

    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      this.properties ??= {};
      // Built on the next tick, after the frontend has added the seed control widget.
      setTimeout(() => attach(this), 0);
      return r;
    };

    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = configure?.apply(this, arguments);
      setTimeout(() => {
        if (this.properties?.[PLAIN]) { this.panel?.destroy(); return; }
        attach(this);
        this.panel?.refresh();
        const saved = this.properties?.[SAVED];
        if (saved) this.panel?.showResult(saved, false);
      }, 0);
      return r;
    };

    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = executed?.apply(this, arguments);
      const data = message?.lmse?.[0];
      if (data) {
        // Kept in the workflow (and so in every PNG): the exact prompt used, for hold after a restart.
        this.properties ??= {};
        this.properties[SAVED] = { prompt: data.prompt, negative: data.negative, info: data.info,
                                   thinking: data.thinking, warnings: data.warnings };
        this.panel?.showResult(data, true);
      }
      return r;
    };

    const removed = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this.panel?.stop();
      return removed?.apply(this, arguments);
    };

    const menu = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (_, options) {
      const r = menu?.apply(this, arguments);
      options.push({
        content: this.properties?.[PLAIN] ? "LM Studio: use the panel" : "LM Studio: show plain widgets",
        callback: () => {
          if (this.properties?.[PLAIN]) { delete this.properties[PLAIN]; attach(this); }
          else { this.properties[PLAIN] = true; this.panel?.destroy(); }
        },
      });
      return r;
    };
  },
});
