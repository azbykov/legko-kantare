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

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const actor = $("actor").value.trim();
  if (actor) headers["X-User"] = actor;
  const r = await fetch("/api" + path, { headers, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    const err = new Error(typeof d === "string" ? d : JSON.stringify(d) || r.statusText);
    err.code = data.code;
    throw err;
  }
  return data;
}

function show(el, text, kind) {
  el.innerHTML = text ? `<div class="msg ${kind}">${esc(text)}</div>` : "";
}

const offerLabel = (o) => `${o.id} — ${o.name}${o.country.length ? " [" + o.country.join(",") + "]" : ""}`;
const offerOptions = (items) => items.map((o) => `<option value="${o.id}">${esc(offerLabel(o))}</option>`).join("");

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

const combo = { found: [], active: -1, LIMIT: 80 };
const offerById = (id) => offers.find((o) => String(o.id) === String(id));
const selectedLabel = (o) => `#${o.id} · ${offerParts(o).title}`;

function openCombo() {
  $("c-offer-combo").classList.add("open");
  $("c-offer-list").classList.remove("hide");
  $("c-offer-q").setAttribute("aria-expanded", "true");
}
function closeCombo() {
  $("c-offer-combo").classList.remove("open");
  $("c-offer-list").classList.add("hide");
  $("c-offer-q").setAttribute("aria-expanded", "false");
  $("c-offer-q").removeAttribute("aria-activedescendant");
  const o = offerById($("c-offer").value);
  $("c-offer-q").value = o ? selectedLabel(o) : ""; // вернуть подпись выбранного, если поиск бросили
}

function renderOfferPicker() {
  const raw = $("c-offer-q").value.trim().toLowerCase();
  const chosen = offerById($("c-offer").value);
  const q = chosen && $("c-offer-q").value === selectedLabel(chosen) ? "" : raw; // в поле подпись выбранного — показать всё
  combo.found = offers.filter((o) => matchOffer(o, q));
  const shown = combo.found.slice(0, combo.LIMIT);
  const sel = $("c-offer").value;
  combo.active = Math.max(0, shown.findIndex((o) => String(o.id) === sel));
  if (!shown.length) combo.active = -1;
  $("c-offer-list").innerHTML = shown.length
    ? shown.map((o, i) => {
        const p = offerParts(o);
        return `<li class="combo-opt" role="option" id="c-offer-opt-${o.id}" data-id="${o.id}" aria-selected="${String(o.id) === sel}">
          <span class="combo-id">${hl(String(o.id), q)}</span>
          <span class="combo-title">${hl(p.title, q)}</span>
          <span class="combo-net">${hl(p.net, q)}</span>
          <span class="combo-meta">${o.country.map((c) => `<span class="chip geo">${esc(c)}</span>`).join("")}${p.tags.map((t) => `<span class="chip">${hl(t, q)}</span>`).join("")}</span>
        </li>`;
      }).join("") + (combo.found.length > shown.length ? `<li class="combo-more">Ещё ${combo.found.length - shown.length} — уточните поиск</li>` : "")
    : `<li class="combo-empty">Ничего не найдено</li>`;
  $("c-offer-count").textContent = q ? `Найдено: ${combo.found.length} из ${offers.length}` : `Всего офферов: ${offers.length}`;
  highlightActive(false);
}

function highlightActive(scroll = true) {
  const items = $("c-offer-list").querySelectorAll(".combo-opt");
  items.forEach((el, i) => el.classList.toggle("active", i === combo.active));
  const el = items[combo.active];
  if (el) {
    $("c-offer-q").setAttribute("aria-activedescendant", el.id);
    if (scroll) el.scrollIntoView({ block: "nearest" });
  }
}

function chooseOffer(id) {
  const o = offerById(id);
  if (!o) return;
  $("c-offer").value = String(o.id);
  $("c-offer-clear").classList.remove("hide");
  closeCombo();
}

$("c-offer-q").addEventListener("focus", () => { $("c-offer-q").select(); renderOfferPicker(); openCombo(); });
$("c-offer-q").addEventListener("input", () => { renderOfferPicker(); openCombo(); });
$("c-offer-q").addEventListener("keydown", (e) => {
  const n = Math.min(combo.found.length, combo.LIMIT);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if ($("c-offer-list").classList.contains("hide")) { renderOfferPicker(); openCombo(); return; }
    if (!n) return;
    combo.active = (combo.active + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
    highlightActive();
  } else if (e.key === "Enter") {
    if (!$("c-offer-list").classList.contains("hide") && combo.active >= 0) {
      e.preventDefault(); // Enter в открытом списке выбирает, а не отправляет форму
      chooseOffer(combo.found[combo.active].id);
    }
  } else if (e.key === "Escape") {
    if (!$("c-offer-list").classList.contains("hide")) { e.preventDefault(); closeCombo(); }
  } else if (e.key === "Tab") {
    closeCombo();
  }
});
$("c-offer-list").addEventListener("mousedown", (e) => {
  const li = e.target.closest(".combo-opt");
  if (!li) return;
  e.preventDefault(); // не терять фокус до выбора
  chooseOffer(li.dataset.id);
});
$("c-offer-list").addEventListener("mousemove", (e) => {
  const li = e.target.closest(".combo-opt");
  if (!li) return;
  const i = [...$("c-offer-list").querySelectorAll(".combo-opt")].indexOf(li);
  if (i !== combo.active) { combo.active = i; highlightActive(false); }
});
$("c-offer-q").addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== $("c-offer-q")) closeCombo(); }, 0));
$("c-offer-clear").onclick = () => {
  $("c-offer").value = "";
  $("c-offer-clear").classList.add("hide");
  $("c-offer-q").value = "";
  $("c-offer-q").focus();
};

$("create-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!$("c-offer").value) {
    show($("c-msg"), "Выберите оффер", "err");
    $("c-offer-q").focus();
    return;
  }
  $("c-submit").disabled = true;
  show($("c-msg"), "");
  try {
    const c = await api("/campaigns", {
      method: "POST",
      body: { name: $("c-name").value, countries: parseCountries($("c-country").value), offer_id: +$("c-offer").value },
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
  $("e-streams").innerHTML = "";
  if (!current) return;
  try {
    const c = await api("/campaigns/" + current);
    $("e-streams").innerHTML = `<div class="card"><h2>${esc(c.name)} <span class="muted">#${c.id} · ${esc(c.state)}</span></h2></div>` +
      c.streams.map(renderStream).join("");
  } catch (err) {
    show($("e-msg"), err.message, "err");
  }
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
    <div class="row"><select data-sel="${s.id}" ${free.length ? "" : "disabled"}>${free.length ? offerOptions(free) : "<option>Все офферы уже в потоке</option>"}</select>
    <button class="primary" data-add="${s.id}" ${free.length ? "" : "disabled"}>Добавить оффер</button></div></div>`;
}

$("e-streams").onclick = async (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled || !current) return;
  b.disabled = true;
  show($("e-msg"), "");
  // версия потока, который видит пользователь: сервер откажет, если поток уже изменили
  const version = b.closest("[data-version]")?.dataset.version || "";
  try {
    if (b.dataset.add) {
      const sel = document.querySelector(`[data-sel="${b.dataset.add}"]`);
      await api(`/campaigns/${current}/streams/${b.dataset.add}/offers`, { method: "POST", body: { offer_id: +sel.value, version } });
    } else if (b.dataset.rm) {
      const [sid, oid] = b.dataset.rm.split(":");
      if (!confirm(`Удалить оффер ${oid} из потока?`)) return;
      await api(`/campaigns/${current}/streams/${sid}/offers/${oid}?` + new URLSearchParams({ version }), { method: "DELETE" });
    }
    await loadCampaign();
  } catch (err) {
    if (err.code === "StaleStream") {
      $("e-msg").innerHTML = `<div class="msg err">${esc(err.message)} <button id="e-reload">Обновить</button></div>`;
      $("e-reload").onclick = () => { show($("e-msg"), ""); loadCampaign(); };
    } else {
      show($("e-msg"), err.message, "err");
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
