/* Veille parlementaire sécurité : affichage et filtres.
   Les données viennent de data/evenements.json, produit chaque jour par scripts/collecte.py. */
(function () {
  "use strict";

  const TYPE_COLORS = {
    "Audition": "var(--t-audition)",
    "Réunion de commission": "var(--t-reunion)",
    "Séance publique": "var(--t-seance)",
    "Étape d'un texte": "var(--t-texte)",
    "Amendements": "var(--t-amendement)",
    "Question": "var(--t-question)",
  };
  const CHAMBRE_COLORS = { "AN": "var(--an)", "Sénat": "var(--senat)" };
  const PAGE = 150;

  let ALL = [];
  let shown = PAGE;
  const $ = (id) => document.getElementById(id);
  const state = { q: "", texte: "", theme: "", chambres: new Set(), types: new Set(), avenir: true, view: "liste" };

  const norm = (s) => (s || "").toString().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  const esc = (s) => (s == null ? "" : String(s)).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function dayKey(e) { return e.debut.slice(0, 10); }
  function hour(e) { return e.journee || e.debut.length <= 10 ? "" : e.debut.slice(11, 16).replace(":", "h"); }
  function fmtDay(k) {
    const d = new Date(k + "T12:00:00");
    return d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  }
  function todayKey() { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }

  // ---------- chargement ----------
  fetch("data/evenements.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(init)
    .catch((err) => {
      $("maj").textContent = "données indisponibles";
      $("v-liste").innerHTML = '<p class="empty">Les données ne sont pas encore disponibles (' + esc(err.message) + "). La première collecte automatique les créera.</p>";
    });

  function init(data) {
    ALL = (data.evenements || []).map((e) => Object.assign(e, { _s: norm([e.titre, e.description, e.organe, e.texte_titre, (e.themes || []).join(" ")].join(" ")) }));
    ALL.sort((a, b) => a.debut.localeCompare(b.debut));
    const d = data.genere_le ? new Date(data.genere_le) : null;
    $("maj").textContent = d ? "mis à jour le " + d.toLocaleString("fr-FR", { dateStyle: "long", timeStyle: "short" }) : "";
    const ko = (data.sources || []).filter((s) => !s.ok);
    if (ko.length) $("maj").textContent += " · source(s) en échec : " + ko.map((s) => s.nom).join(", ");

    buildChips("chambres", ["AN", "Sénat"], CHAMBRE_COLORS, "chambres", { "AN": "Assemblée nationale", "Sénat": "Sénat" });
    buildChips("types", Object.keys(TYPE_COLORS), TYPE_COLORS, "types");
    buildSelect("texte", uniq(ALL.filter((e) => e.texte_id).map((e) => [e.texte_id, e.texte_titre])));
    buildSelect("theme", uniq(ALL.flatMap((e) => (e.themes || []).map((t) => [t, t]))));

    readHash();
    bind();
    bindCal();
    render();
  }

  function uniq(pairs) {
    const m = new Map();
    pairs.forEach(([k, v]) => { if (k && !m.has(k)) m.set(k, v || k); });
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1], "fr"));
  }
  function buildSelect(id, pairs) {
    const sel = $(id);
    pairs.forEach(([k, v]) => { const o = document.createElement("option"); o.value = k; o.textContent = v.length > 90 ? v.slice(0, 88) + "…" : v; o.title = v; sel.appendChild(o); });
  }
  function buildChips(id, values, colors, key, labels) {
    const box = $(id);
    values.forEach((v) => {
      const n = ALL.filter((e) => (key === "chambres" ? e.chambre : e.type) === v).length;
      const lab = document.createElement("label");
      lab.className = "chip";
      lab.innerHTML = '<input type="checkbox" value="' + esc(v) + '"><span class="dot" style="background:' + colors[v] + '"></span>' + esc((labels && labels[v]) || v) + ' <span class="n">' + n + "</span>";
      box.appendChild(lab);
    });
  }

  // ---------- état <-> URL (pour partager une vue filtrée) ----------
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    state.q = p.get("q") || "";
    state.texte = p.get("texte") || "";
    state.theme = p.get("theme") || "";
    state.chambres = new Set((p.get("chambre") || "").split(",").filter(Boolean));
    state.types = new Set((p.get("type") || "").split(",").filter(Boolean));
    state.avenir = p.get("tout") !== "1";
    state.view = p.get("vue") === "calendrier" ? "calendrier" : "liste";
    $("q").value = state.q; $("texte").value = state.texte; $("theme").value = state.theme; $("avenir").checked = state.avenir;
    document.querySelectorAll("#chambres input").forEach((i) => (i.checked = state.chambres.has(i.value)));
    document.querySelectorAll("#types input").forEach((i) => (i.checked = state.types.has(i.value)));
  }
  function writeHash() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.texte) p.set("texte", state.texte);
    if (state.theme) p.set("theme", state.theme);
    if (state.chambres.size) p.set("chambre", [...state.chambres].join(","));
    if (state.types.size) p.set("type", [...state.types].join(","));
    if (!state.avenir) p.set("tout", "1");
    if (state.view !== "liste") p.set("vue", state.view);
    history.replaceState(null, "", p.toString() ? "#" + p.toString() : location.pathname);
  }

  function bind() {
    let t;
    $("q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = $("q").value.trim(); update(); }, 200); });
    $("texte").addEventListener("change", () => { state.texte = $("texte").value; if (state.texte) state.avenir = $("avenir").checked = false; update(); });
    $("theme").addEventListener("change", () => { state.theme = $("theme").value; update(); });
    $("avenir").addEventListener("change", () => { state.avenir = $("avenir").checked; update(); });
    $("chambres").addEventListener("change", () => { state.chambres = new Set([...document.querySelectorAll("#chambres input:checked")].map((i) => i.value)); update(); });
    $("types").addEventListener("change", () => { state.types = new Set([...document.querySelectorAll("#types input:checked")].map((i) => i.value)); update(); });
    $("reset").addEventListener("click", () => { location.hash = ""; readHash(); update(); });
    $("ics").addEventListener("click", exportIcs);
    $("share").addEventListener("click", () => {
      navigator.clipboard.writeText(location.href).then(() => { $("share").textContent = "Lien copié ✓"; setTimeout(() => ($("share").textContent = "Copier le lien de cette vue"), 1800); });
    });
    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { state.view = b.dataset.view; update(); }));
    $("v-liste").addEventListener("click", (ev) => {
      if (ev.target.closest("a")) return;
      if (ev.target.id === "more") { shown += PAGE; renderList(filtered()); return; }
      const el = ev.target.closest(".ev"); if (el) openDetail(el.dataset.id);
    });
  }

  function update() { shown = PAGE; writeHash(); render(); }

  function filtered() {
    const words = norm(state.q).split(/\s+/).filter(Boolean);
    const limit = state.avenir ? new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10) : "";
    return ALL.filter((e) =>
      (!limit || dayKey(e) >= limit) &&
      (!state.chambres.size || state.chambres.has(e.chambre)) &&
      (!state.types.size || state.types.has(e.type)) &&
      (!state.texte || e.texte_id === state.texte) &&
      (!state.theme || (e.themes || []).includes(state.theme)) &&
      words.every((w) => e._s.includes(w))
    );
  }

  function render() {
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === state.view)));
    $("v-liste").hidden = state.view !== "liste";
    $("v-calendrier").hidden = state.view !== "calendrier";
    const list = filtered();
    $("count").textContent = list.length + " événement" + (list.length > 1 ? "s" : "");
    if (state.view === "liste") renderList(list); else renderCal(list);
  }

  function tags(e) {
    let h = '<span class="tag ' + (e.chambre === "AN" ? "AN" : "Senat") + '">' + (e.chambre === "AN" ? "Assemblée" : "Sénat") + "</span>";
    h += '<span class="tag">' + esc(e.type) + "</span>";
    if (e.huis_clos === true) h += '<span class="tag hc">huis clos</span>';
    (e.themes || []).slice(0, 3).forEach((t) => (h += '<span class="tag kw">' + esc(t) + "</span>"));
    return h;
  }

  function renderList(list) {
    const box = $("v-liste");
    if (!list.length) { box.innerHTML = '<p class="empty">Aucun événement ne correspond à ces filtres.</p>'; return; }
    // à venir d'abord (ordre chronologique), puis le passé récent (le plus récent en premier)
    const tk = todayKey();
    const fut = list.filter((e) => dayKey(e) >= tk);
    const past = list.filter((e) => dayKey(e) < tk).reverse();
    const ordered = fut.concat(past).slice(0, shown);
    let html = "", cur = null, pastShown = false;
    ordered.forEach((e) => {
      const k = dayKey(e);
      if (k < tk && !pastShown) { pastShown = true; html += (cur ? "</div>" : "") + '<h2 style="margin-top:24px">Passé récent</h2>'; cur = null; }
      if (k !== cur) {
        if (cur) html += "</div>";
        html += '<div class="day"><h2 class="' + (k === tk ? "today" : "") + '">' + (k === tk ? "Aujourd'hui · " : "") + fmtDay(k) + "</h2>";
        cur = k;
      }
      html += '<article class="ev" data-id="' + esc(e.id) + '" style="--c:' + (TYPE_COLORS[e.type] || "var(--accent)") + '">' +
        '<div class="h">' + (hour(e) || "—") + "</div><div>" +
        '<div class="t">' + esc(e.titre) + "</div>" +
        '<div class="m">' + tags(e) + (e.organe ? " " + esc(e.organe) : "") + "</div>" +
        (e.texte_titre && e.type !== "Étape d'un texte" ? '<div class="m">Texte : ' + esc(e.texte_titre) + "</div>" : "") +
        "</div></article>";
    });
    html += "</div>";
    if (fut.length + past.length > shown) html += '<button id="more" class="btn ghost more">Afficher plus</button>';
    box.innerHTML = html;
  }

  // ---------- calendrier (mois / semaine), sans bibliothèque externe ----------
  const calState = { mode: "mois", ref: new Date() };
  const DOW = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."];
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  function lundi(d) { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
  let calList = [];

  function renderCal(list) {
    calList = list;
    const byDay = new Map();
    list.forEach((e) => { const k = dayKey(e); if (!byDay.has(k)) byDay.set(k, []); byDay.get(k).push(e); });
    const ref = calState.ref, mois = calState.mode === "mois";
    let start, nb;
    if (mois) {
      const first = new Date(ref.getFullYear(), ref.getMonth(), 1);
      start = lundi(first);
      const last = new Date(ref.getFullYear(), ref.getMonth() + 1, 0);
      nb = Math.ceil(((last - start) / 864e5 + 1) / 7) * 7;
      $("cal-titre").textContent = first.toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
    } else {
      start = lundi(ref); nb = 7;
      const fin = new Date(start); fin.setDate(fin.getDate() + 6);
      $("cal-titre").textContent = "Semaine du " + start.toLocaleDateString("fr-FR", { day: "numeric", month: "long" }) + " au " + fin.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
    }
    const max = mois ? 4 : 99, tk = todayKey();
    let h = '<div class="grid">' + DOW.map((d) => '<div class="dow">' + d + "</div>").join("");
    for (let i = 0; i < nb; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      const k = iso(d), evs = byDay.get(k) || [];
      const out = mois && d.getMonth() !== ref.getMonth();
      h += '<div class="cell' + (out ? " out" : "") + (k === tk ? " today" : "") + '"><div class="num">' + (mois ? d.getDate() : d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric" })) + "</div>";
      evs.slice(0, max).forEach((e) => {
        h += '<button class="pill" data-id="' + esc(e.id) + '" style="--c:' + (TYPE_COLORS[e.type] || "var(--accent)") + '" title="' + esc(e.titre) + '">' +
          (hour(e) ? "<b>" + hour(e) + "</b> " : "") + (e.chambre === "AN" ? "AN · " : "Sénat · ") + esc(e.titre) + "</button>";
      });
      if (evs.length > max) h += '<button class="plus" data-day="' + k + '">+ ' + (evs.length - max) + " autre" + (evs.length - max > 1 ? "s" : "") + "</button>";
      h += "</div>";
    }
    $("cal").innerHTML = h + "</div>";
    $("cal").className = mois ? "" : "week";
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === calState.mode)));
  }

  function bindCal() {
    const move = (n) => {
      const r = calState.ref;
      calState.ref = calState.mode === "mois" ? new Date(r.getFullYear(), r.getMonth() + n, 1) : new Date(r.getFullYear(), r.getMonth(), r.getDate() + 7 * n);
      renderCal(calList);
    };
    $("cal-prev").addEventListener("click", () => move(-1));
    $("cal-next").addEventListener("click", () => move(1));
    $("cal-today").addEventListener("click", () => { calState.ref = new Date(); renderCal(calList); });
    document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => { calState.mode = b.dataset.mode; renderCal(calList); }));
    $("cal").addEventListener("click", (ev) => {
      const p = ev.target.closest(".pill"); if (p) { openDetail(p.dataset.id); return; }
      const m = ev.target.closest(".plus"); if (m) openDay(m.dataset.day);
    });
    $("detail-body").addEventListener("click", (ev) => { const el = ev.target.closest(".ev"); if (el) openDetail(el.dataset.id); });
    if (window.innerWidth < 700) calState.mode = "semaine";
  }

  function openDay(k) {
    const evs = calList.filter((e) => dayKey(e) === k);
    $("detail-body").innerHTML = "<h3>" + fmtDay(k) + '</h3><div class="daylist">' + evs.map((e) =>
      '<article class="ev" data-id="' + esc(e.id) + '" style="--c:' + (TYPE_COLORS[e.type] || "var(--accent)") + '"><div class="h">' + (hour(e) || "—") + '</div><div><div class="t">' + esc(e.titre) + '</div><div class="m">' + tags(e) + "</div></div></article>").join("") + "</div>";
    if (!$("detail").open) $("detail").showModal();
  }

  function openDetail(id) {
    const e = ALL.find((x) => x.id === id); if (!e) return;
    const row = (k, v) => (v ? "<dt>" + k + "</dt><dd>" + v + "</dd>" : "");
    $("detail-body").innerHTML = "<h3>" + esc(e.titre) + "</h3><p>" + tags(e) + "</p><dl>" +
      row("Date", fmtDay(dayKey(e)) + (hour(e) ? " à " + hour(e) : "")) +
      row("Chambre", e.chambre === "AN" ? "Assemblée nationale" : "Sénat") +
      row("Organe", esc(e.organe)) +
      row("Lieu", esc(e.lieu)) +
      row("Accès", e.huis_clos === true ? "Huis clos" : e.huis_clos === false ? "Ouvert à la presse" : "") +
      row("Texte", e.texte_titre ? (e.texte_url ? '<a href="' + esc(e.texte_url) + '" target="_blank" rel="noopener">' + esc(e.texte_titre) + "</a>" : esc(e.texte_titre)) : "") +
      row("Mots-clés", esc((e.themes || []).join(", "))) +
      row("Source", e.url ? '<a href="' + esc(e.url) + '" target="_blank" rel="noopener">Voir la page officielle ↗</a>' : "") +
      "</dl>" + (e.description ? '<div class="desc">' + esc(e.description) + "</div>" : "");
    if (!$("detail").open) $("detail").showModal();
  }

  // ---------- export iCalendar ----------
  function exportIcs() {
    const list = filtered();
    const pad = (s) => s.replace(/[-:]/g, "");
    const escI = (s) => (s || "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/([,;])/g, "\\$1");
    const fold = (l) => { const out = []; while (l.length > 74) { out.push(l.slice(0, 74)); l = " " + l.slice(74); } out.push(l); return out.join("\r\n"); };
    const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Veille parlementaire securite//FR", "CALSCALE:GREGORIAN", "X-WR-CALNAME:Veille parlementaire sécurité"];
    list.forEach((e) => {
      lines.push("BEGIN:VEVENT", "UID:" + e.id + "@veille-securite");
      if (e.journee || e.debut.length <= 10) lines.push("DTSTART;VALUE=DATE:" + pad(e.debut.slice(0, 10)));
      else {
        lines.push("DTSTART;TZID=Europe/Paris:" + pad(e.debut.slice(0, 16)) + "00");
        if (e.fin) lines.push("DTEND;TZID=Europe/Paris:" + pad(e.fin.slice(0, 16)) + "00");
      }
      lines.push(fold("SUMMARY:" + escI((e.chambre === "AN" ? "[AN] " : "[Sénat] ") + e.titre)));
      lines.push(fold("DESCRIPTION:" + escI([e.type, e.organe, e.texte_titre, e.url].filter(Boolean).join("\n"))));
      if (e.url) lines.push(fold("URL:" + e.url));
      lines.push("END:VEVENT");
    });
    lines.push("END:VCALENDAR");
    const blob = new Blob([lines.join("\r\n")], { type: "text/calendar;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "veille-securite.ics"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
})();
