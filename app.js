import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm";
import { SUPABASE_URL, SUPABASE_ANON_KEY } from "./config.js";

// ------------------------------------------------------------------ types
// Colour says visited / not visited; the white glyph says what kind of place.
const CATS = {
  house:    { label: "Historic house", glyph: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9v12h14V9"/><path d="M10 21v-6h4v6"/>' },
  castle:   { label: "Castle & fort", glyph: '<path d="M4 21V5h3v3h2.5V5h5v3H17V5h3v16z"/><path d="M10 21v-4a2 2 0 0 1 4 0v4"/>' },
  garden:   { label: "Garden & park", glyph: '<circle cx="12" cy="7.5" r="3.5"/><path d="M12 11v10"/><path d="M12 17c-3.5 0-5.5-2-5.5-4.5 3 0 5.5 2 5.5 4.5z"/><path d="M12 17c3.5 0 5.5-2 5.5-4.5-3 0-5.5 2-5.5 4.5z"/>' },
  nature:   { label: "Countryside & woods", glyph: '<path d="M12 2.5 5 13h4l-3.5 5h13L15 13h4z"/><path d="M12 18v4"/>' },
  coast:    { label: "Coast & beach", glyph: '<circle cx="12" cy="7" r="3"/><path d="M2 14c2.5-2 4.5-2 7 0s4.5 2 6 0 4.5-2 7 0"/><path d="M2 19c2.5-2 4.5-2 7 0s4.5 2 6 0 4.5-2 7 0"/>' },
  ancient:  { label: "Ancient site", glyph: '<path d="M5 21V9h4v12"/><path d="M15 21V9h4v12"/><path d="M3 5.5h18v3H3z"/>' },
  industry: { label: "Mill & industry", glyph: '<path d="M12 11 5 4"/><path d="M12 11l7-7"/><path d="M12 11l-5 5"/><path d="M12 11l5 5"/><path d="M9.5 21l1.5-7h2l1.5 7"/>' },
  church:   { label: "Abbey & church", glyph: '<path d="M12 2v5"/><path d="M9.5 4h5"/><path d="M5 22V12l7-5 7 5v10z"/><path d="M10 22v-4a2 2 0 0 1 4 0v4"/>' },
  other:    { label: "Museum & other", glyph: '<path d="M5 22V3"/><path d="M5 4h13l-3 4.5 3 4.5H5"/>' },
};
// The National Trust's members get in free at National Trust for Scotland
// places and vice versa, so both are on the map.
const ORGS = {
  nt:  { label: "National Trust", short: "National Trust" },
  nts: { label: "National Trust for Scotland", short: "NT for Scotland" },
};
const COL = { todo: "#b2472f", done: "#2e7d3a", gold: "#e3b341" };
const STROKE = 'fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"';
const PIN = "M16 1C8.3 1 2 7.1 2 14.6 2 24.5 16 41 16 41s14-16.5 14-26.4C30 7.1 23.7 1 16 1z";

const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}

function glyphBadge(cat) {
  const span = h("span", { class: "glyph", "aria-hidden": "true" });
  span.innerHTML = `<svg viewBox="0 0 24 24" ${STROKE}>${CATS[cat].glyph}</svg>`;   // trusted constant
  return span;
}

const iconCache = new Map();
// A place the Trust only owns (kept for its notes) gets a faded pin.
function pinIcon(cat, visited, listed = true) {
  const key = cat + ":" + visited + ":" + listed;
  if (!iconCache.has(key)) {
    const tick = visited
      ? `<circle cx="26" cy="6.5" r="5.5" fill="${COL.gold}" stroke="white" stroke-width="1.5"/>` +
        `<path d="M23.4 6.6l1.8 1.8 3.3-3.5" fill="none" stroke="#1d2320" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>`
      : "";
    iconCache.set(key, L.divIcon({
      className: listed ? "pin" : "pin owned",
      html: `<svg width="32" height="42" viewBox="0 0 32 42"><path d="${PIN}" fill="${visited ? COL.done : COL.todo}" stroke="white" stroke-width="1.5"/>` +
            `<g transform="translate(7 5.6) scale(.75)" ${STROKE}>${CATS[cat].glyph}</g>${tick}</svg>`,
      iconSize: [32, 42], iconAnchor: [16, 41], popupAnchor: [0, -36],
    }));
  }
  return iconCache.get(key);
}

// ------------------------------------------------------------ small store
const store = {
  get(k, dflt) { try { const v = localStorage.getItem("nt:" + k); return v == null ? dflt : JSON.parse(v); } catch { return dflt; } },
  set(k, v) { try { localStorage.setItem("nt:" + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ------------------------------------------------------------------- auth
const configured = SUPABASE_URL && SUPABASE_ANON_KEY;
const sb = configured ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY) : null;
let recovering = /type=recovery/.test(location.hash);
let started = false;

function showGate(which) {
  $("app").hidden = true;
  $("gate").hidden = false;
  for (const id of ["login", "newpass", "setup"]) $(id).hidden = id !== which;
}

function say(el, text, err = false) { el.textContent = text; el.classList.toggle("err", err); }

if (!configured) {
  showGate("setup");
} else {
  sb.auth.onAuthStateChange((event, session) => {
    // Defer: calling Supabase from inside this callback can deadlock the client.
    setTimeout(() => {
      if (event === "PASSWORD_RECOVERY" || (recovering && session)) { showGate("newpass"); return; }
      if (session && !started) start(session.user);
      else if (!session) { started = false; showGate("login"); }
    }, 0);
  });

  $("login").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter; btn.disabled = true;
    say($("login-msg"), "Signing in…");
    const { error } = await sb.auth.signInWithPassword({ email: $("email").value.trim(), password: $("password").value });
    btn.disabled = false;
    if (error) say($("login-msg"), error.message, true);
  });

  $("forgot").addEventListener("click", async () => {
    const email = $("email").value.trim();
    if (!email) { say($("login-msg"), "Type your email above first.", true); return; }
    const { error } = await sb.auth.resetPasswordForEmail(email, { redirectTo: location.origin + location.pathname });
    say($("login-msg"), error ? error.message : "If that address has an account, a reset link is on its way.", !!error);
  });

  $("newpass").addEventListener("submit", async (e) => {
    e.preventDefault();
    const { data, error } = await sb.auth.updateUser({ password: $("newpass-1").value });
    if (error) { say($("newpass-msg"), error.message, true); return; }
    recovering = false;
    history.replaceState(null, "", location.pathname);
    start(data.user);
  });

  $("signout").addEventListener("click", async () => { await sb.auth.signOut(); location.reload(); });
}

// -------------------------------------------------------------------- app
let map, cluster, user;
const places = [];                  // rows from public.places
const byId = new Map();             // id -> place
const markers = new Map();          // id -> L.Marker
const visits = new Map();           // id -> { visited, rating, notes, by, at } - shared by all users
let filtersDirty = false;

const isVisited = (id) => !!visits.get(id)?.visited;

async function fetchAll(table, cols) {
  const out = [];
  for (let from = 0; ; from += 1000) {
    const { data, error } = await sb.from(table).select(cols).range(from, from + 999);
    if (error) throw error;
    out.push(...data);
    if (data.length < 1000) return out;
  }
}

const visitFromRow = (v) => ({ visited: v.visited, rating: v.rating, notes: v.notes || "", by: v.updated_by, at: v.updated_at });

// When the other person ticks, rates or writes a note, their change arrives
// here and the pin (and the card, if it is open and not being typed in) updates.
function listenForChanges() {
  sb.channel("shared-visits")
    .on("postgres_changes", { event: "*", schema: "public", table: "shared_visits" }, ({ new: row }) => {
      if (!row?.place_id || !byId.has(row.place_id)) return;
      if (row.updated_by === user.email && visits.has(row.place_id)) return;   // our own save coming back
      const was = isVisited(row.place_id);
      visits.set(row.place_id, visitFromRow(row));
      if (was !== isVisited(row.place_id)) refreshMarker(row.place_id);
      const pop = markers.get(row.place_id).getPopup();
      if (pop?.isOpen() && !pop.getElement()?.contains(document.activeElement)) pop.update();
    })
    .subscribe();
}

async function start(u) {
  if (started) return;
  started = true;
  user = u;
  $("gate").hidden = true;
  $("app").hidden = false;
  $("who").textContent = u.email;
  initMap();
  try {
    const [pl, vs] = await Promise.all([
      fetchAll("places", "*"),
      fetchAll("shared_visits", "place_id,visited,rating,notes,updated_by,updated_at"),
    ]);
    for (const p of pl) {
      if (!CATS[p.cat]) p.cat = "other";
      if (!ORGS[p.org]) p.org = "nt";
      places.push(p); byId.set(p.id, p);
    }
    for (const v of vs) visits.set(v.place_id, visitFromRow(v));
    $("loading").hidden = true;
    if (!places.length) { $("loading").hidden = false; $("loading").textContent = "No places yet - run the Update places action on GitHub."; }
    buildMarkers();
    buildFilters();
    applyFilters();
    listenForChanges();
  } catch (err) {
    $("loading").textContent = "Could not load places: " + (err.message || err);
  }
}

function initMap() {
  map = L.map("map", { minZoom: 5, maxBounds: [[46, -16], [63, 8]], zoomControl: false });
  L.control.zoom({ position: "topleft" }).addTo(map);
  const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  });
  const satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics",
  });
  (store.get("layer", "Map") === "Satellite" ? satellite : streets).addTo(map);
  L.control.layers({ Map: streets, Satellite: satellite }, null, { position: "bottomleft" }).addTo(map);
  map.on("baselayerchange", (e) => store.set("layer", e.name));

  const view = store.get("view", null);
  if (view) map.setView(view.c, view.z); else map.fitBounds([[49.9, -8.2], [59.5, 1.8]]);
  map.on("moveend", () => store.set("view", { c: map.getCenter(), z: map.getZoom() }));

  // "Where am I?" button
  const Locate = L.Control.extend({
    onAdd() {
      const div = L.DomUtil.create("div", "leaflet-bar leaflet-control leaflet-control-locate");
      const a = L.DomUtil.create("a", "", div);
      a.href = "#"; a.title = "Show where I am"; a.setAttribute("role", "button"); a.textContent = "◎";
      L.DomEvent.on(a, "click", (e) => { L.DomEvent.preventDefault(e); map.locate({ setView: true, maxZoom: 11 }); });
      return div;
    },
  });
  new Locate({ position: "topleft" }).addTo(map);
  let me;
  map.on("locationfound", (e) => {
    me?.remove();
    me = L.circleMarker(e.latlng, { radius: 8, color: "#fff", weight: 3, fillColor: "#2a7de1", fillOpacity: 1 }).addTo(map);
  });
  map.on("locationerror", (e) => alert("Could not find your location: " + e.message));

  cluster = L.markerClusterGroup({
    showCoverageOnHover: false, maxClusterRadius: 45, disableClusteringAtZoom: 11, chunkedLoading: true,
    iconCreateFunction(c) {
      const ms = c.getAllChildMarkers();
      const done = ms.filter((m) => isVisited(m.options.placeId)).length;
      return L.divIcon({
        className: listed ? "pin" : "pin owned", iconSize: [40, 40],
        html: `<div class="cluster" style="--p:${Math.round((100 * done) / ms.length)}" title="${done} of ${ms.length} visited"><span>${ms.length}</span></div>`,
      });
    },
  });
  map.addLayer(cluster);
  map.on("popupclose", () => { if (filtersDirty) applyFilters(); });
  map.on("click", () => toggleFilters(false));
}

function buildMarkers() {
  for (const p of places) {
    const m = L.marker([p.lat, p.lon], { icon: pinIcon(p.cat, isVisited(p.id), p.listed !== false), title: p.name, placeId: p.id });
    m.bindPopup(() => popup(p), { maxWidth: 320, autoPanPaddingTopLeft: [10, 70], autoPanPaddingBottomRight: [10, 10] });
    markers.set(p.id, m);
  }
}

function refreshMarker(id) {
  const m = markers.get(id);
  const p = byId.get(id);
  m.setIcon(pinIcon(p.cat, isVisited(id), p.listed !== false));
  if (cluster.hasLayer(m)) cluster.refreshClusters(m);
  updateCounts();
  filtersDirty = true;
}

// ------------------------------------------------------------------ popup
const safeUrl = (u) => (/^https?:\/\//i.test(u) ? u : null);

function popup(p) {
  const v = visits.get(p.id) || { visited: false, rating: null, notes: "" };
  const status = h("div", { class: "status", "aria-live": "polite" });

  const links = h("div", { class: "links" },
    (p.links || []).filter((l) => safeUrl(l.url)).map((l, i) =>          // a Trust place's own page is always first
      h("a", { href: l.url, target: "_blank", rel: "noopener", class: i === 0 && p.listed !== false ? "nt" : null }, l.label)),
    h("a", { href: `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}`, target: "_blank", rel: "noopener" }, "Directions"));

  const walks = (p.walks || []).filter((w) => safeUrl(w.url));
  const walkList = walks.length
    ? h("details", {}, h("summary", {}, `${walks.length} National Trust walk${walks.length > 1 ? "s" : ""}`),
        h("ul", {}, walks.map((w) => h("li", {}, h("a", { href: w.url, target: "_blank", rel: "noopener" }, w.name),
          w.miles ? ` (${w.miles} mi)` : ""))))
    : null;

  const tick = h("input", { type: "checkbox", checked: v.visited });
  tick.addEventListener("change", () => save(p.id, { visited: tick.checked }, status));

  const buttons = [];
  const paintRating = (r) => buttons.forEach((b, i) => {
    b.classList.toggle("on", i === r);
    b.classList.toggle("in", r != null && i < r);
    b.setAttribute("aria-pressed", String(i === r));
  });
  for (let i = 0; i <= 10; i++) {
    buttons.push(h("button", { type: "button", title: `Rate ${i} out of 10`, onclick: () => {
      const cur = visits.get(p.id)?.rating ?? null;
      const r = cur === i ? null : i;                         // tap the chosen score again to clear it
      paintRating(r);
      const patch = { rating: r };
      if (r != null && !tick.checked) { tick.checked = true; patch.visited = true; }   // rating it means you went
      save(p.id, patch, status);
    } }, String(i)));
  }
  paintRating(v.rating);

  const notes = h("textarea", { placeholder: "Notes (you both see these): who went, what to see next time…", rows: 3 });
  notes.value = v.notes || "";
  let timer;
  const flush = () => { clearTimeout(timer); if (notes.value !== (visits.get(p.id)?.notes || "")) save(p.id, { notes: notes.value }, status); };
  notes.addEventListener("input", () => { clearTimeout(timer); say(status, "…"); timer = setTimeout(flush, 800); });
  notes.addEventListener("blur", flush);
  markers.get(p.id).once("popupclose", flush);

  const changed = v.by
    ? h("div", { class: "changed" }, `Last changed by ${v.by === user.email ? "you" : v.by.split("@")[0]}` +
        (v.at ? ", " + new Date(v.at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : ""))
    : null;

  return h("div", { class: "pop" },
    h("div", { class: "type" }, glyphBadge(p.cat), CATS[p.cat].label + (p.org === "nts" ? " · " + ORGS.nts.short : "")),
    h("h3", {}, p.name),
    p.listed === false
      ? h("p", { class: "owned-note" }, `Not a ${ORGS[p.org].label} place to visit: the Trust owns it but has no visitor page for it. ` +
          "It stays on the map because it has your notes.")
      : null,
    h("p", {}, p.descr || ""),
    links,
    walkList,
    h("div", { class: "mine" },
      h("label", { class: "visited" }, tick, "Visited"),
      h("div", {}, h("div", { class: "rating-label" }, h("span", {}, "Our rating"), h("span", {}, "0 = poor · 10 = superb")),
        h("div", { class: "rating", role: "group", "aria-label": "Rating 0 to 10" }, buttons)),
      notes,
      h("div", { class: "foot" }, changed, status)));
}

// Saves go out one at a time per place and always send the latest state, so a
// slow early request can never land after, and undo, a later one.
const queues = new Map();
function save(id, patch, statusEl) {
  const cur = { ...(visits.get(id) || { visited: false, rating: null, notes: "" }), ...patch,
                by: user.email, at: new Date().toISOString() };
  const was = isVisited(id);
  visits.set(id, cur);
  if (was !== cur.visited) refreshMarker(id);
  say(statusEl, "Saving…");
  const q = (queues.get(id) || Promise.resolve()).then(async () => {
    const s = visits.get(id);
    const { error } = await sb.from("shared_visits").upsert({
      place_id: id, visited: s.visited, rating: s.rating, notes: s.notes,
      updated_at: s.at, updated_by: user.email,
    });
    say(statusEl, error ? "Not saved: " + error.message : "Saved ✓", !!error);
  });
  queues.set(id, q);
}

// ---------------------------------------------------------------- filters
let filter = { vis: "all", cats: Object.keys(CATS), orgs: Object.keys(ORGS), ...store.get("filter", {}) };

function buildFilters() {
  $("orgs").replaceChildren(...Object.entries(ORGS).map(([key, o]) => {
    const box = h("input", { type: "checkbox", value: key, checked: filter.orgs.includes(key) });
    box.addEventListener("change", readFilters);
    return h("li", {}, h("label", {}, box, o.label, h("span", { class: "count", id: "count-org-" + key })));
  }));
  const ul = $("cats");
  ul.replaceChildren(...Object.entries(CATS).map(([key, c]) => {
    const box = h("input", { type: "checkbox", value: key, checked: filter.cats.includes(key) });
    box.addEventListener("change", readFilters);
    return h("li", {}, h("label", {}, box, glyphBadge(key), c.label, h("span", { class: "count", id: "count-" + key })));
  }));
  for (const r of document.querySelectorAll('input[name="vis"]')) {
    r.checked = r.value === filter.vis;
    r.addEventListener("change", readFilters);
  }
  $("cats-all").onclick = () => { ul.querySelectorAll("input").forEach((b) => (b.checked = true)); readFilters(); };
  $("cats-none").onclick = () => { ul.querySelectorAll("input").forEach((b) => (b.checked = false)); readFilters(); };
  $("filters-btn").onclick = (e) => { e.stopPropagation(); toggleFilters(); };
  updateCounts();
}

function toggleFilters(force) {
  const open = force ?? $("filters").hidden;
  $("filters").hidden = !open;
  $("filters-btn").setAttribute("aria-expanded", String(open));
}

function readFilters() {
  filter = {
    vis: document.querySelector('input[name="vis"]:checked').value,
    cats: [...$("cats").querySelectorAll("input:checked")].map((b) => b.value),
    orgs: [...$("orgs").querySelectorAll("input:checked")].map((b) => b.value),
  };
  store.set("filter", filter);
  applyFilters();
}

function passes(p) {
  if (!filter.cats.includes(p.cat) || !filter.orgs.includes(p.org)) return false;
  if (filter.vis === "todo") return !isVisited(p.id);
  if (filter.vis === "done") return isVisited(p.id);
  return true;
}

function applyFilters() {
  filtersDirty = false;
  cluster.clearLayers();
  cluster.addLayers(places.filter(passes).map((p) => markers.get(p.id)));
}

function updateCounts() {
  const done = places.filter((p) => isVisited(p.id)).length;
  $("progress").textContent = `${done} / ${places.length}`;
  const count = (id, of) => {
    const el = $(id);
    if (el) el.textContent = `${of.filter((p) => isVisited(p.id)).length} / ${of.length}`;
  };
  for (const key of Object.keys(CATS)) count("count-" + key, places.filter((p) => p.cat === key));
  for (const key of Object.keys(ORGS)) count("count-org-" + key, places.filter((p) => p.org === key));
}

// ----------------------------------------------------------------- search
const norm = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
let hits = [], active = -1;

$("search").addEventListener("input", () => {
  const q = norm($("search").value.trim());
  const ul = $("results");
  if (q.length < 2) { ul.hidden = true; return; }
  hits = places.filter((p) => norm(p.name).includes(q))
    .sort((a, b) => norm(b.name).startsWith(q) - norm(a.name).startsWith(q) || a.name.localeCompare(b.name))
    .slice(0, 10);
  active = -1;
  ul.replaceChildren(...(hits.length ? hits.map((p, i) =>
    h("li", { onmousedown: (e) => { e.preventDefault(); goTo(p); } }, glyphBadge(p.cat), p.name,
      h("small", {}, isVisited(p.id) ? "✓ visited" : CATS[p.cat].label))) : [h("li", {}, "No matching places")]));
  ul.hidden = false;
});
$("search").addEventListener("keydown", (e) => {
  const items = $("results").children;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    active = Math.max(0, Math.min(hits.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
    [...items].forEach((li, i) => li.classList.toggle("active", i === active));
  } else if (e.key === "Enter" && hits.length) {
    goTo(hits[Math.max(active, 0)]);
  } else if (e.key === "Escape") {
    $("results").hidden = true;
  }
});
$("search").addEventListener("blur", () => setTimeout(() => ($("results").hidden = true), 150));

function goTo(p) {
  $("results").hidden = true;
  $("search").value = "";
  $("search").blur();
  if (!passes(p)) {                     // make sure the chosen place is on the map
    filter = { vis: "all", cats: [...new Set([...filter.cats, p.cat])], orgs: [...new Set([...filter.orgs, p.org])] };
    store.set("filter", filter);
    buildFilters();
    applyFilters();
  }
  const m = markers.get(p.id);
  map.setView([p.lat, p.lon], Math.max(map.getZoom(), 12));
  cluster.zoomToShowLayer(m, () => m.openPopup());
}
