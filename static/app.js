const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const PER_PAGE = 20;
let offers = [];
const list = { q: "", page: 1, total: 0 };
let current = null; // id открытой кампании

// ---- кто работает (уходит в X-User для аудита)
const store = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};
$("actor").value = store.get("actor");
$("actor").onchange = () => store.set("actor", $("actor").value.trim());

const FIELD_LABELS = { name: "Название", countries: "Гео", country: "Гео", offer_id: "Оффер" };

// FastAPI отдаёт 422 как список {loc, msg, type, ctx}; превращаем его в строки «Поле: что не так».
function validationText(items) {
  return items.map((e) => {
    const field = (e.loc || []).filter((p) => p !== "body" && typeof p === "string").pop();
    const label = FIELD_LABELS[field] || field;
    let text;
    if (e.type === "value_error") text = String(e.msg || "").replace(/^Value error,\s*/, "");
    else if (e.type === "missing") text = "обязательное поле";
    else if (e.type === "too_short") text = `слишком мало значений (минимум ${e.ctx?.min_length})`;
    else if (e.type === "too_long") text = `слишком много значений (максимум ${e.ctx?.max_length})`;
    else if (e.type === "string_too_long") text = `слишком длинно (максимум ${e.ctx?.max_length} символов)`;
    else if (e.type?.startsWith("int_")) text = "нужно целое число";
    else text = e.msg;
    return label && !text.startsWith(label) ? `${label}: ${text}` : text;
  }).join("\n");
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const actor = $("actor").value.trim();
  if (actor) headers["X-User"] = actor;
  const r = await fetch("/api" + path, { headers, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    const text = typeof d === "string" ? d : Array.isArray(d) ? validationText(d) : d ? JSON.stringify(d) : "";
    const err = new Error(text || `${r.status} ${r.statusText}`);
    err.code = data.code;
    throw err;
  }
  return data;
}

function show(el, text, kind) {
  el.innerHTML = text ? `<div class="msg ${kind}">${esc(text)}</div>` : "";
}


function tab(name, campaignId) {
  for (const t of ["create", "edit"]) {
    $("view-" + t).classList.toggle("hide", t !== name);
    $("tab-" + t).classList.toggle("on", t === name);
  }
  if (name !== "edit") return;
  if (campaignId) {
    list.q = String(campaignId);
    list.page = 1;
    $("e-search").value = list.q;
    current = +campaignId;
  }
  loadList();
}
$("tab-create").onclick = () => tab("create");
$("tab-edit").onclick = () => tab("edit");

// ---- создание
const fmt = (v) => (typeof v === "string" ? v : JSON.stringify(v));

function renderCreated(c) {
  const links = [
    `<a href="${esc(c.admin_url)}" target="_blank" rel="noopener">Открыть в админке Keitaro</a>`,
    c.public_url ? `Публичный URL: <a href="${esc(c.public_url)}" target="_blank" rel="noopener">${esc(c.public_url)}</a>` : `<span class="muted">Публичный URL: у кампании нет alias</span>`,
    `<a href="#" data-open="${c.id}">Открыть в редакторе</a>`,
  ];
  let check;
  if (c.verify_error) check = `<div class="warn">Не удалось сверить сохранённые поля: ${esc(c.verify_error)}</div>`;
  else if (!c.mismatches.length) check = `<div>Сверка: всё, что отправлено, Keitaro сохранил как есть.</div>`;
  else
    check = `<div class="warn">Keitaro сохранил не то, что отправлено (${c.mismatches.length}):</div>
      <table><tr><th>Где</th><th>Поле</th><th>Отправлено</th><th>Сохранено</th></tr>
      ${c.mismatches.map((m) => `<tr><td>${esc(m.entity)}</td><td>${esc(m.field)}</td><td><code>${esc(fmt(m.sent))}</code></td><td><code>${esc(fmt(m.saved))}</code></td></tr>`).join("")}</table>`;
  $("c-msg").innerHTML = `<div class="msg ok">Создана кампания «${esc(c.name)}» (id ${c.id})
    <div class="links">${links.map((l) => `<div>${l}</div>`).join("")}</div>${check}</div>`;
}

const parseCountries = (text) => [...new Set(text.toUpperCase().split(/[\s,;]+/).filter(Boolean))];

// ---- выбор оффера: комбобокс с поиском по id и названию
// Имя на стенде вида «18165 Glucobalance [VISION-RO] [spin ro -] OFFERIA v1» — разбираем на части для показа.
function offerParts(o) {
  const rest = o.name.replace(new RegExp("^" + o.id + "\\s*"), "");
  const tags = [...rest.matchAll(/\[([^\]]*)\]/g)].map((m) => m[1].replace(/\s*-\s*$/, "").trim()).filter(Boolean);
  const title = rest.split("[")[0].trim() || rest;
  const net = rest.includes("]") ? rest.slice(rest.lastIndexOf("]") + 1).trim() : "";
  return { title, tags, net };
}
const matchOffer = (o, q) => !q || String(o.id).startsWith(q) || o.name.toLowerCase().includes(q);
function hl(text, q) {
  if (!q) return esc(text);
  const i = text.toLowerCase().indexOf(q);
  return i < 0 ? esc(text) : esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length));
}

const offerById = (id) => offers.find((o) => String(o.id) === String(id));
const selectedLabel = (o) => `#${o.id} · ${offerParts(o).title}`;

// Разметка комбобокса для динамических мест (редактор). В форме создания она в index.html.
const comboMarkup = (attrs, placeholder) => `<div class="combo" ${attrs}>
  <input class="combo-input" role="combobox" aria-autocomplete="list" aria-expanded="false"
         placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false">
  <button type="button" class="combo-clear hide" aria-label="Сбросить оффер">×</button>
  <span class="combo-caret" aria-hidden="true"></span>
  <ul class="combo-list hide" role="listbox" aria-label="Офферы"></ul></div>`;

// Комбобокс выбора оффера: поиск по id и названию, ↑ ↓ Enter Esc, подсветка совпадений.
// items() — из каких офферов выбирать; onEnter — Enter при закрытом списке и выбранном оффере.
let comboSeq = 0;
function mountCombo(root, { items, countEl = null, onEnter = null }) {
  const input = root.querySelector(".combo-input");
  const list = root.querySelector(".combo-list");
  const clear = root.querySelector(".combo-clear");
  const hidden = root.querySelector(".combo-value"); // необязательно: для форм
  const uid = root.id || `combo-${++comboSeq}`;
  list.id = `${uid}-list`;
  input.setAttribute("aria-controls", list.id);
  const st = { value: "", found: [], active: -1, LIMIT: 80 };
  const isOpen = () => !list.classList.contains("hide");

  function setValue(id) {
    st.value = id ? String(id) : "";
    if (hidden) hidden.value = st.value;
    clear.classList.toggle("hide", !st.value);
  }
  function open() {
    root.classList.add("open");
    list.classList.remove("hide");
    input.setAttribute("aria-expanded", "true");
  }
  function close() {
    root.classList.remove("open");
    list.classList.add("hide");
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    const o = offerById(st.value);
    input.value = o ? selectedLabel(o) : ""; // вернуть подпись выбранного, если поиск бросили
    if (countEl) countEl.textContent = `Всего офферов: ${items().length}`;
  }
  function highlight(scroll = true) {
    const opts = list.querySelectorAll(".combo-opt");
    opts.forEach((el, i) => el.classList.toggle("active", i === st.active));
    const el = opts[st.active];
    if (el) {
      input.setAttribute("aria-activedescendant", el.id);
      if (scroll) el.scrollIntoView({ block: "nearest" });
    }
  }
  function render() {
    const all = items();
    const chosen = offerById(st.value);
    const raw = input.value.trim().toLowerCase();
    const q = chosen && input.value === selectedLabel(chosen) ? "" : raw; // в поле подпись выбранного — показать всё
    st.found = all.filter((o) => matchOffer(o, q));
    const shown = st.found.slice(0, st.LIMIT);
    st.active = shown.length ? Math.max(0, shown.findIndex((o) => String(o.id) === st.value)) : -1;
    list.innerHTML = shown.length
      ? shown.map((o) => {
          const p = offerParts(o);
          return `<li class="combo-opt" role="option" id="${uid}-opt-${o.id}" data-id="${o.id}" aria-selected="${String(o.id) === st.value}">
            <span class="combo-id">${hl(String(o.id), q)}</span>
            <span class="combo-title">${hl(p.title, q)}</span>
            <span class="combo-net">${hl(p.net, q)}</span>
            <span class="combo-meta">${o.country.map((c) => `<span class="chip geo">${esc(c)}</span>`).join("")}${p.tags.map((t) => `<span class="chip">${hl(t, q)}</span>`).join("")}</span>
          </li>`;
        }).join("") + (st.found.length > shown.length ? `<li class="combo-more">Ещё ${st.found.length - shown.length} — уточните поиск</li>` : "")
      : `<li class="combo-empty">Ничего не найдено</li>`;
    if (countEl) countEl.textContent = q ? `Найдено: ${st.found.length} из ${all.length}` : `Всего офферов: ${all.length}`;
    highlight(false);
  }
  function choose(id) {
    if (!offerById(id)) return;
    setValue(id);
    close();
  }

  let quiet = false; // фокус из кода (после добавления) — без раскрытия списка
  input.addEventListener("focus", () => {
    input.select();
    if (quiet) { quiet = false; return; }
    render();
    open();
  });
  input.addEventListener("mousedown", () => { if (document.activeElement === input && !isOpen()) { render(); open(); } });
  input.addEventListener("input", () => { render(); open(); });
  input.addEventListener("keydown", (e) => {
    const n = Math.min(st.found.length, st.LIMIT);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!isOpen()) { render(); open(); return; }
      if (!n) return;
      st.active = (st.active + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
      highlight();
    } else if (e.key === "Enter") {
      if (isOpen() && st.active >= 0) {
        e.preventDefault(); // Enter в открытом списке выбирает, а не отправляет форму
        choose(st.found[st.active].id);
      } else if (!isOpen() && st.value && onEnter) {
        e.preventDefault();
        onEnter();
      }
    } else if (e.key === "Escape") {
      if (isOpen()) { e.preventDefault(); close(); }
    } else if (e.key === "Tab") {
      close();
    }
  });
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest(".combo-opt");
    if (!li) return;
    e.preventDefault(); // не терять фокус до выбора
    choose(li.dataset.id);
  });
  list.addEventListener("mousemove", (e) => {
    const li = e.target.closest(".combo-opt");
    if (!li) return;
    const i = [...list.querySelectorAll(".combo-opt")].indexOf(li);
    if (i !== st.active) { st.active = i; highlight(false); }
  });
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) close(); }, 0));
  clear.onclick = () => { setValue(""); input.value = ""; input.focus(); };

  const api = {
    get value() { return st.value; },
    focus: ({ openList = true } = {}) => { quiet = !openList; input.focus(); },
  };
  root._combo = api;
  return api;
}

const offerCombo = mountCombo($("c-offer-combo"), { items: () => offers, countEl: $("c-offer-count") });

$("create-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!offerCombo.value) {
    show($("c-msg"), "Выберите оффер", "err");
    offerCombo.focus();
    return;
  }
  $("c-submit").disabled = true;
  show($("c-msg"), "");
  try {
    const c = await api("/campaigns", {
      method: "POST",
      body: { name: $("c-name").value, countries: parseCountries($("c-country").value), offer_id: +offerCombo.value },
    });
    renderCreated(c);
    $("c-name").value = "";
  } catch (err) {
    show($("c-msg"), err.message, "err");
  } finally {
    $("c-submit").disabled = false;
  }
};
$("c-msg").onclick = (e) => {
  const a = e.target.closest("[data-open]");
  if (!a) return;
  e.preventDefault();
  tab("edit", a.dataset.open);
};

// ---- список кампаний: поиск и страницы
let searchTimer;
$("e-search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    list.q = $("e-search").value.trim();
    list.page = 1;
    loadList();
  }, 300);
};
$("e-prev").onclick = () => { if (list.page > 1) { list.page--; loadList(); } };
$("e-next").onclick = () => { if (list.page * PER_PAGE < list.total) { list.page++; loadList(); } };

async function loadList() {
  show($("e-msg"), "");
  $("e-list").innerHTML = `<tr><td class="muted">Загрузка…</td></tr>`;
  try {
    const params = new URLSearchParams({ q: list.q, page: list.page, per_page: PER_PAGE });
    const res = await api("/campaigns?" + params);
    list.total = res.total;
    $("e-list").innerHTML = res.items.length
      ? `<tr><th>ID</th><th>Название</th><th>Статус</th></tr>` +
        res.items.map((c) => `<tr class="pick ${c.id === current ? "on" : ""}" data-id="${c.id}"><td>${c.id}</td><td>${esc(c.name)}</td><td>${esc(c.state)}</td></tr>`).join("")
      : `<tr><td class="muted">${list.q ? "Ничего не найдено" : "Кампаний нет"}</td></tr>`;
    const pages = Math.max(1, Math.ceil(res.total / PER_PAGE));
    $("e-page").textContent = `Стр. ${res.page} из ${pages} · всего ${res.total}`;
    $("e-prev").disabled = res.page <= 1;
    $("e-next").disabled = res.page >= pages;
    if (res.items.length === 1 && !current) current = res.items[0].id;
  } catch (err) {
    $("e-list").innerHTML = `<tr><td class="muted">Не удалось загрузить</td></tr>`;
    show($("e-msg"), err.message, "err");
    return;
  }
  await loadCampaign();
}

$("e-list").onclick = (e) => {
  const row = e.target.closest("tr[data-id]");
  if (!row) return;
  current = +row.dataset.id;
  for (const r of $("e-list").querySelectorAll("tr.pick")) r.classList.toggle("on", r === row);
  loadCampaign();
};

// ---- редактор кампании
async function loadCampaign() {
  if (!current) { $("e-streams").innerHTML = ""; return; }
  try {
    const c = await api("/campaigns/" + current);
    // старое содержимое меняем только когда пришёл ответ — без мигания пустого экрана
    $("e-streams").innerHTML = `<div class="card"><h2>${esc(c.name)} <span class="muted">#${c.id} · ${esc(c.state)}</span></h2></div>` +
      c.streams.map(renderStream).join("");
    for (const s of c.streams) mountStreamCombo(s);
  } catch (err) {
    show($("e-msg"), err.message, "err");
  }
}

const streamCard = (sid) => $("e-streams").querySelector(`[data-stream="${sid}"]`);

function mountStreamCombo(s) {
  const root = $("e-streams").querySelector(`[data-combo-stream="${s.id}"]`);
  if (!root) return null;
  const used = new Set(s.offers.map((o) => o.offer_id));
  const free = offers.filter((o) => !used.has(o.id));
  return mountCombo(root, { items: () => free, onEnter: () => root.closest(".row").querySelector("[data-add]").click() });
}

// Перерисовать одну карточку потока по свежим данным — без перезагрузки остального редактора.
async function refreshStream(sid, note) {
  const c = await api("/campaigns/" + current);
  const s = c.streams.find((x) => String(x.id) === String(sid));
  const old = streamCard(sid);
  if (!s || !old) return loadCampaign();
  const tmp = document.createElement("div");
  tmp.innerHTML = renderStream(s);
  old.replaceWith(tmp.firstElementChild);
  const picker = mountStreamCombo(s);
  if (note) showInStream(sid, note, "ok");
  picker?.focus({ openList: false }); // сразу можно искать следующий оффер, сообщение не закрыто списком
}

function showInStream(sid, text, kind) {
  const box = streamCard(sid)?.querySelector(".stream-msg");
  if (box) box.innerHTML = text ? `<div class="msg ${kind}">${esc(text)}</div>` : "";
  else show($("e-msg"), text, kind);
}

function renderStream(s) {
  const head = `<h2>${esc(s.name)} <span class="muted">${esc(s.type)} · ${esc(s.schema)}</span></h2>`;
  if (!s.editable) return `<div class="card">${head}<div class="muted">Поток без офферов (редирект)</div></div>`;
  const used = new Set(s.offers.map((o) => o.offer_id));
  const active = s.offers.filter((o) => o.state === "active");
  const lastActive = active.length === 1; // пустой landings-поток не оставляем
  const rows = s.offers.length
    ? s.offers.map((o) => {
        const paused = o.state !== "active";
        const locked = lastActive && !paused;
        return `<tr class="${paused ? "muted" : ""}"><td>${o.offer_id}</td><td>${esc(o.name)}${paused ? " (" + esc(o.state) + ")" : ""}</td>
        <td>${paused ? "—" : o.share + "%"}</td>
        <td><button class="danger" data-rm="${s.id}:${o.offer_id}" ${locked ? 'disabled title="Последний активный оффер: сначала добавьте другой"' : ""}>Удалить</button></td></tr>`;
      }).join("")
    : `<tr><td colspan="4" class="muted">Офферов нет</td></tr>`;
  const ok = active.length === 0 || s.share_total === 100;
  const free = offers.filter((o) => !used.has(o.id));
  return `<div class="card" data-stream="${s.id}" data-version="${esc(s.version)}">${head}
    <table><tr><th>ID</th><th>Оффер</th><th>Доля</th><th></th></tr>${rows}</table>
    <div class="total ${ok ? "ok" : "bad"}" style="margin:8px 0">Сумма долей: ${s.share_total}%</div>
    <div class="row">${free.length ? comboMarkup(`data-combo-stream="${s.id}"`, "Найдите оффер для добавления") : '<input disabled placeholder="Все офферы уже в потоке">'}
    <button class="primary" data-add="${s.id}" ${free.length ? "" : "disabled"}>Добавить оффер</button></div>
    <div class="stream-msg"></div></div>`;
}

$("e-streams").onclick = async (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled || !current) return;
  if (b.classList.contains("combo-clear")) return; // крестик комбобокса обрабатывает сам комбобокс
  const card = b.closest("[data-stream]");
  const sid = card?.dataset.stream;
  if (!sid) return;
  show($("e-msg"), "");
  showInStream(sid, "");
  // версия потока, который видит пользователь: сервер откажет, если поток уже изменили
  const version = card.dataset.version || "";
  b.disabled = true;
  try {
    if (b.dataset.add) {
      const picker = card.querySelector("[data-combo-stream]")._combo;
      if (!picker.value) {
        showInStream(sid, "Выберите оффер для добавления", "err");
        picker.focus();
        return;
      }
      const name = offerById(picker.value) ? selectedLabel(offerById(picker.value)) : `#${picker.value}`;
      await api(`/campaigns/${current}/streams/${sid}/offers`, { method: "POST", body: { offer_id: +picker.value, version } });
      await refreshStream(sid, `Добавлен ${name}`);
    } else if (b.dataset.rm) {
      const oid = b.dataset.rm.split(":")[1];
      if (!confirm(`Удалить оффер ${oid} из потока?`)) return;
      await api(`/campaigns/${current}/streams/${sid}/offers/${oid}?` + new URLSearchParams({ version }), { method: "DELETE" });
      await refreshStream(sid, `Удалён оффер #${oid}`);
    } else if (b.dataset.reload) {
      await refreshStream(sid);
    }
  } catch (err) {
    if (err.code === "StaleStream") {
      const box = streamCard(sid)?.querySelector(".stream-msg");
      if (box) box.innerHTML = `<div class="msg err">${esc(err.message)} <button data-reload="1">Обновить поток</button></div>`;
    } else {
      showInStream(sid, err.message, "err");
    }
  } finally {
    b.disabled = false;
  }
};

(async () => {
  try {
    offers = await api("/offers");
    // оффер не выбираем сами: кампания не должна уйти со случайным оффером
    $("c-offer-count").textContent = `Всего офферов: ${offers.length}`;
    $("prefix").textContent = "(префикс добавится автоматически)";
  } catch (err) {
    show($("c-msg"), err.message, "err");
  }
})();
