const COLORS = ["#3d7ea6", "#c45c4a", "#3a9a7a", "#d4a017", "#7b5ea7", "#d17a3a", "#4a8fbf", "#a84d62"];
const OTHER = "#5c6573";
const TH_ZH = {
  ukraine: "俄乌",
  iran: "美伊",
  houthi: "胡塞",
  lebanon: "黎南",
  gaza: "加沙",
  carrier: "航母",
  eastasia: "南海",
  japan: "日本",
  korea: "朝韩",
};
const OP_ZH = {
  continue: "延续",
  shift: "焦点漂移",
  split: "分裂",
  merge: "汇合",
  decay: "消退",
  emerge: "新现",
};

let S = window.SLICE;
let themes = [];
let minors = [];
let allNamed = [];
let selected = null;
let playIdx = -1;
let playTimer = null;
const tip = document.getElementById("tip");

function esc(s) {
  return String(s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
function colorOf(id) {
  if (!id) return OTHER;
  const t = allNamed.find((x) => x.theme_id === id);
  return t ? COLORS[t.color_key % COLORS.length] : OTHER;
}
function dimmed(id) {
  return selected && id !== selected;
}
function showTip(ev, html) {
  tip.innerHTML = html;
  tip.classList.add("show");
  tip.style.left = Math.min(ev.clientX + 12, window.innerWidth - 380) + "px";
  tip.style.top = Math.min(ev.clientY + 12, window.innerHeight - 90) + "px";
}
function hideTip() {
  tip.classList.remove("show");
}
function setSelect(id) {
  selected = id && id === selected ? null : id || null;
  draw();
}
function el(tag, attrs) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === "text") n.textContent = v;
    else if (v != null) n.setAttribute(k, String(v));
  });
  return n;
}
function bind(slice) {
  stopPlay();
  S = slice;
  window.SLICE = slice;
  themes = S.themes || [];
  minors = S.minors || [];
  allNamed = [...themes, ...minors];
  selected = (themes[0] && themes[0].theme_id) || null;
  playIdx = -1;
}

function sliceKeyOf(slice) {
  const a = slice.window.from.slice(0, 10);
  const b = slice.window.to.slice(0, 10);
  if (a === b) return a;
  const am = a.slice(0, 7);
  const bm = b.slice(0, 7);
  if (am === bm) {
    if (a.endsWith("-01") && /-(28|29|30|31)$/.test(b)) return am;
    return a + "~" + b;
  }
  if (a.endsWith("-01") && /-(28|29|30|31)$/.test(b)) return am + "~" + bm;
  return a + "~" + b;
}

function monthLabel(k) {
  if (k.includes("~")) {
    const [a, b] = k.split("~");
    if (a.length === 10) {
      const aM = Number(a.slice(5, 7));
      const bM = Number(b.slice(5, 7));
      const aD = Number(a.slice(8));
      const bD = Number(b.slice(8));
      if (aM === bM) return aM + "月" + aD + "–" + bD + "日";
      return aM + "月" + aD + "日–" + bM + "月" + bD + "日";
    }
    if (a === "2026-04" && b === "2026-09") {
      return "全程（4–9月）";
    }
    if (a === "2026-04" && b === "2026-08") {
      return "4–8月";
    }
    return Number(a.slice(5)) + "–" + Number(b.slice(5)) + "月对照";
  }
  if (k.length === 10) {
    return Number(k.slice(5, 7)) + "月" + Number(k.slice(8)) + "日";
  }
  return Number(k.slice(5)) + "月";
}

function sliceKeys() {
  const keys = Object.keys(window.SLICES || {});
  const months = keys.filter((k) => k.length === 7).sort();
  const days = keys.filter((k) => k.length === 10).sort();
  const spans = keys.filter((k) => k.includes("~")).sort();
  const hide = keys.includes("2026-04~2026-09") ? new Set(["2026-04~2026-08"]) : new Set();
  return months.concat(days).concat(spans.filter((k) => !hide.has(k)));
}

function nDays() {
  return (themes[0] && themes[0].daily && themes[0].daily.length) || 0;
}

function dayAt(i) {
  const d = themes[0] && themes[0].daily && themes[0].daily[i];
  return d ? d.day : "";
}

function stopPlay() {
  if (playTimer) {
    clearInterval(playTimer);
    playTimer = null;
  }
  const b = document.getElementById("play");
  if (b) b.textContent = "播放";
}

function startPlay() {
  if (playTimer) {
    stopPlay();
    return;
  }
  if (playIdx < 0) playIdx = 0;
  document.getElementById("play").textContent = "暂停";
  const step = () => {
    const n = nDays();
    if (!n) return stopPlay();
    playIdx += 1;
    if (playIdx >= n) {
      playIdx = -1;
      stopPlay();
    }
    draw();
  };
  playTimer = setInterval(step, nDays() > 60 ? 70 : 120);
  draw();
}

function daysOf() {
  if (themes[0] && themes[0].daily && themes[0].daily.length) return themes[0].daily.map((d) => d.day);
  return (S.other && S.other.daily) || [];
}

function sparkPath(daily, w, h) {
  const vals = (daily || []).map((d) => d.n_smooth != null ? d.n_smooth : d.n);
  const max = Math.max(...vals, 1);
  if (vals.length < 2) return "";
  return vals
    .map((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - (v / max) * (h - 2) - 1;
      return `${i ? "L" : "M"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

function drawMonths() {
  const host = document.getElementById("months");
  if (!host) return;
  const keys = sliceKeys();
  const cur = sliceKeyOf(S);
  const same =
    host.tagName === "SELECT" &&
    host.options.length === keys.length &&
    keys.every((k, i) => host.options[i].value === k);
  if (!same) {
    host.innerHTML = "";
    keys.forEach((k) => {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = monthLabel(k);
      host.appendChild(o);
    });
  }
  if (host.value !== cur) host.value = cur;
  if (host.tagName === "SELECT" && !host.dataset.bound) {
    host.addEventListener("change", () => {
      const slice = window.SLICES[host.value];
      if (!slice) return;
      bind(slice);
      draw();
    });
    host.dataset.bound = "1";
  }
}

function drawKpis() {
  const m = S.metrics || {};
  const items = [
    ["窗内稿", m.n_docs, ""],
    ["核心议题", m.n_core, ""],
    ["次要议题", m.n_minor || 0, ""],
    ["信源", m.n_media != null ? m.n_media : "—", ""],
  ];
  document.getElementById("kpis").innerHTML = items
    .map(
      ([lab, val, cls]) =>
        `<div class="kpi ${cls}"><b>${esc(val)}</b><span>${lab}</span></div>`
    )
    .join("");
}

function trailing(arr, win) {
  const out = [];
  let c = 0;
  for (let i = 0; i < arr.length; i++) {
    c += arr[i];
    if (i >= win) c -= arr[i - win];
    out.push(c / Math.min(i + 1, win));
  }
  return out;
}

function stacked() {
  const days = (themes[0] && themes[0].daily) || [];
  const other = Object.fromEntries(((S.other && S.other.daily) || []).map((d) => [d.day, d.n]));
  const full = themes.map((t) => ({
    id: t.theme_id,
    name: t.name,
    values: (t.daily || []).map((d) => Number(d.n_smooth != null ? d.n_smooth : d.n) || 0),
  }));
  const rawOther = days.map((d) => Number(other[d.day] || 0));
  full.push({
    id: "",
    name: "其余合计",
    values: trailing(rawOther, 7),
  });
  const ymax = Math.max(
    ...full[0].values.map((_, i) => full.reduce((s, ser) => s + (ser.values[i] || 0), 0)),
    1
  );
  const series = full.map((s) => ({
    ...s,
    values: s.values.map((v, i) => (playIdx >= 0 && i > playIdx ? 0 : v)),
  }));
  const cols = days.map((_, i) => {
    let y = 0;
    return series.map((s) => {
      const v = s.values[i] || 0;
      const rec = { id: s.id, name: s.name, y0: y, y1: y + v, v };
      y += v;
      return rec;
    });
  });
  return { days: days.map((d) => d.day), series, cols, ymax };
}

function areaFrom(cols, idx, x, y) {
  const n = cols.length;
  if (n < 2) return "";
  const top = [];
  const bot = [];
  for (let i = 0; i < n; i++) {
    const s = cols[i][idx];
    top.push([x(i), y(s.y1)]);
    bot.push([x(i), y(s.y0)]);
  }
  const t = top.map((p, i) => `${i ? "L" : "M"} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const b = bot
    .reverse()
    .map((p, i) => `${i ? "L" : "L"} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`)
    .join(" ");
  return t + " " + b + " Z";
}

function drawRiver() {
  const svg = document.getElementById("river");
  svg.innerHTML = "";
  const W = 1180;
  const H = 320;
  const pad = { l: 40, r: 16, t: 16, b: 36 };
  const { days, series, cols, ymax } = stacked();
  if (!days.length) return;
  const x = (i) => pad.l + (i / Math.max(days.length - 1, 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - v / ymax) * (H - pad.t - pad.b);
  const g = el("g");
  [0, 0.5, 1].forEach((f) => {
    const yy = y(ymax * f);
    g.appendChild(el("line", { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, stroke: "#2a3342", "stroke-width": 1 }));
    g.appendChild(
      el("text", {
        x: pad.l - 8,
        y: yy + 4,
        "text-anchor": "end",
        "font-size": 10,
        fill: "#8b97a8",
        text: String(Math.round(ymax * f)),
      })
    );
  });
  series.forEach((s, idx) => {
    const path = el("path", {
      d: areaFrom(cols, idx, x, y),
      fill: colorOf(s.id),
      "fill-opacity": dimmed(s.id) ? 0.28 : s.id ? 0.9 : 0.42,
      stroke: selected && s.id === selected ? "#f4f0e6" : "none",
      "stroke-width": selected && s.id === selected ? 1 : 0,
    });
    path.style.cursor = s.id ? "pointer" : "default";
    path.addEventListener("click", () => s.id && setSelect(s.id));
    g.appendChild(path);
  });
  const hover = el("line", { x1: 0, x2: 0, y1: pad.t, y2: H - pad.b, stroke: "#e8eef6", "stroke-opacity": 0.25, visibility: "hidden" });
  g.appendChild(hover);
  const hit = el("rect", { x: pad.l, y: pad.t, width: W - pad.l - pad.r, height: H - pad.t - pad.b, fill: "transparent" });
  hit.addEventListener("mousemove", (ev) => {
    const box = svg.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(days.length - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (days.length - 1))));
    hover.setAttribute("x1", x(i));
    hover.setAttribute("x2", x(i));
    hover.setAttribute("visibility", "visible");
    const rows = cols[i]
      .filter((s) => s.v > 0.15)
      .slice()
      .reverse()
      .map((s) => `${esc(s.name)}　${s.v.toFixed(1)}`)
      .join("<br>");
    showTip(ev, `<b>${days[i].slice(5)}</b><br>${rows}`);
  });
  hit.addEventListener("mouseleave", () => {
    hover.setAttribute("visibility", "hidden");
    hideTip();
  });
  g.appendChild(hit);
  if (playIdx >= 0) {
    g.appendChild(
      el("line", {
        x1: x(playIdx),
        x2: x(playIdx),
        y1: pad.t,
        y2: H - pad.b,
        stroke: "#d4b06a",
        "stroke-width": 1.5,
        "stroke-dasharray": "4 3",
      })
    );
  }
  days.forEach((d, i) => {
    if (i % Math.ceil(days.length / 8) && i !== days.length - 1) return;
    g.appendChild(
      el("text", {
        x: x(i),
        y: H - 10,
        "text-anchor": "middle",
        "font-size": 11,
        fill: "#8b97a8",
        text: d.slice(5),
      })
    );
  });
  svg.appendChild(g);

  const legend = document.getElementById("legend");
  legend.innerHTML = "";
  series.forEach((s) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = selected === s.id ? "on" : dimmed(s.id) ? "dim" : "";
    b.innerHTML = `<i style="background:${colorOf(s.id)}"></i>${esc(s.name)}`;
    b.onclick = () => s.id && setSelect(s.id);
    legend.appendChild(b);
  });
}

function massTo(t) {
  return (t.daily || []).reduce((s, d, i) => s + (playIdx >= 0 && i > playIdx ? 0 : d.n || 0), 0);
}

function centroidDay(t) {
  const daily = t.daily || [];
  let w = 0;
  let acc = 0;
  daily.forEach((d, i) => {
    if (playIdx >= 0 && i > playIdx) return;
    const n = d.n || 0;
    w += n;
    acc += n * i;
  });
  return w ? acc / w : 0;
}

function recentN(t, win) {
  const daily = t.daily || [];
  const end = playIdx >= 0 ? playIdx : daily.length - 1;
  let s = 0;
  let k = 0;
  for (let i = end; i >= 0 && k < win; i--, k++) s += daily[i].n || 0;
  return s;
}

function drawBubbles() {
  const svg = document.getElementById("bubbles");
  svg.innerHTML = "";
  const W = 560;
  const H = 280;
  const pad = { l: 52, r: 18, t: 22, b: 28 };
  const nDays = Math.max(...themes.map((t) => (t.daily || []).length), 1);
  const maxMass = Math.max(...themes.map((t) => massTo(t)), 1);
  const heats = themes.map((t) => recentN(t, 7));
  const maxHeat = Math.max(...heats, 1);
  const x = (i) => pad.l + (i / Math.max(nDays - 1, 1)) * (W - pad.l - pad.r);
  const y = (heat) => pad.t + (1 - heat / maxHeat) * (H - pad.t - pad.b);
  const g = el("g");
  [0, 0.5, 1].forEach((f) => {
    const yy = y(f * maxHeat);
    g.appendChild(el("line", { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, stroke: "#2a3342" }));
  });
  g.appendChild(el("text", { x: 8, y: pad.t + 4, "font-size": 10, fill: "#8b97a8", text: "近日多" }));
  g.appendChild(el("text", { x: 8, y: H - pad.b - 2, "font-size": 10, fill: "#8b97a8", text: "近日少" }));
  g.appendChild(el("text", { x: pad.l, y: H - 8, "font-size": 10, fill: "#8b97a8", text: "月初" }));
  g.appendChild(el("text", { x: W - pad.r, y: H - 8, "text-anchor": "end", "font-size": 10, fill: "#8b97a8", text: "月末" }));
  themes.forEach((t, i) => {
    const heat = heats[i];
    const cx = x(centroidDay(t));
    const cy = y(heat);
    const r = 10 + 22 * Math.sqrt(massTo(t) / maxMass);
    const c = el("circle", {
      cx,
      cy,
      r,
      fill: colorOf(t.theme_id),
      "fill-opacity": dimmed(t.theme_id) ? 0.18 : 0.82,
      stroke: selected === t.theme_id ? "#f4f0e6" : "none",
      "stroke-width": 2,
    });
    c.style.cursor = "pointer";
    c.addEventListener("click", () => setSelect(t.theme_id));
    c.addEventListener("mousemove", (ev) =>
      showTip(ev, `<b>${esc(t.name)}</b><br>${massTo(t)} 篇 · 近7日 ${heat} 篇`)
    );
    c.addEventListener("mouseleave", hideTip);
    g.appendChild(c);
  });
  svg.appendChild(g);
}

function drawCore() {
  const host = document.getElementById("core");
  host.innerHTML = themes
    .map((t, i) => {
      const cls = ["core-card", selected === t.theme_id ? "on" : "", dimmed(t.theme_id) ? "dim" : ""].join(" ");
      return `<article class="${cls}" data-id="${esc(t.theme_id)}">
        <div class="rank">${i + 1}</div>
        <i class="swatch" style="background:${colorOf(t.theme_id)}"></i>
        <div class="name">${esc(t.name)}</div>
        <div class="mass">${massTo(t)}</div>
      </article>`;
    })
    .join("");
  host.querySelectorAll(".core-card").forEach((n) => {
    n.onclick = () => setSelect(n.getAttribute("data-id"));
  });
}

function drawMinors() {
  const host = document.getElementById("minors");
  if (!minors.length) {
    host.innerHTML = '<p class="empty">这个窗没有次要议题。</p>';
    return;
  }
  host.innerHTML = minors
    .map((t) => {
      const cls = ["chip", selected === t.theme_id ? "on" : "", dimmed(t.theme_id) ? "dim" : ""].join(" ");
      return `<button type="button" class="${cls}" data-id="${esc(t.theme_id)}">
        <span class="dot" style="background:${colorOf(t.theme_id)}"></span>
        ${esc(t.name)}
        <span class="n">${t.mass}</span>
      </button>`;
    })
    .join("");
  host.querySelectorAll(".chip").forEach((n) => {
    n.onclick = () => setSelect(n.getAttribute("data-id"));
  });
}

function drawDetail() {
  const host = document.getElementById("detail");
  const t = allNamed.find((x) => x.theme_id === selected);
  if (!t) {
    host.innerHTML = '<p class="empty-detail">点河图、气泡或卡片，看这条线上发生了什么。</p>';
    return;
  }
  const cut = playIdx >= 0 ? dayAt(playIdx) : "";
  const arts = (t.articles || []).filter((a) => !cut || a.day <= cut);
  const d = sparkPath(t.daily, 520, 64);
  const narHtml = renderNarrative(t, cut);
  const byDay = {};
  arts.forEach((a) => {
    (byDay[a.day] = byDay[a.day] || []).push(a);
  });
  const days = Object.keys(byDay).sort();
  const list = days
    .map((day) => {
      const rows = byDay[day]
        .map((a) => {
          const media = a.source_name ? `<span class="media">${esc(a.source_name)}</span>` : "";
          const orig =
            a.title_orig && a.title_orig !== a.title
              ? `<span class="orig">${esc(a.title_orig)}</span>`
              : "";
          const inner = `<span class="day">${esc(day.slice(5))}</span>${media}${esc(a.title)}${orig}`;
          return a.url
            ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${inner}</a>`
            : `<a class="nolink">${inner}</a>`;
        })
        .join("");
      return `<h3>${esc(day)} · ${byDay[day].length} 篇</h3>${rows}`;
    })
    .join("");
  const th = (t.theaters || []).map((x) => TH_ZH[x] || x).join(" / ") || "未标战场";
  const kind = themes.some((x) => x.theme_id === t.theme_id) ? "核心" : "次要";
  const mediaBits = {};
  arts.forEach((a) => {
    if (a.source_name) mediaBits[a.source_name] = (mediaBits[a.source_name] || 0) + 1;
  });
  const mediaLine = Object.entries(mediaBits)
    .sort((x, y) => y[1] - x[1])
    .slice(0, 6)
    .map(([n, c]) => `${n} ${c}`)
    .join(" · ");
  host.innerHTML = `
    <p class="detail-name">${esc(t.name)}</p>
    <p class="detail-meta">${kind} · ${arts.length} 篇 · ${esc(th)}</p>
    ${mediaLine ? `<p class="detail-meta">信源 ${esc(mediaLine)}</p>` : ""}
    <svg class="spark" viewBox="0 0 520 64">${
      d ? `<path d="${d}" fill="none" stroke="${colorOf(t.theme_id)}" stroke-width="2"/>` : ""
    }</svg>
    ${narHtml}
    <div class="arts"><p class="arts-kicker">全部见报稿</p>${list || '<p class="empty">这个播放时刻还没有见报稿。</p>'}</div>
  `;
}

function massInMonth(daily, ym) {
  return (daily || []).reduce((s, d) => s + ((d.day || "").slice(0, 7) === ym ? d.n || 0 : 0), 0);
}

function monthFlow() {
  const a = ((S.window && S.window.from) || "").slice(0, 7);
  const b = ((S.window && S.window.to) || "").slice(0, 7);
  if (!a || !b) return { cols: [], migration: [] };
  const months = [];
  let y = Number(a.slice(0, 4));
  let m = Number(a.slice(5, 7));
  const y2 = Number(b.slice(0, 4));
  const m2 = Number(b.slice(5, 7));
  while (y < y2 || (y === y2 && m <= m2)) {
    months.push(y + "-" + String(m).padStart(2, "0"));
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  const cols = months.map((ym) => {
    const units = (themes || [])
      .map((t) => ({
        theme_id: t.theme_id,
        name: t.name,
        mass: massInMonth(t.daily, ym),
      }))
      .filter((u) => u.mass > 0);
    return {
      from: ym + "-01",
      to: ym,
      label: Number(ym.slice(5)) + "月",
      units,
      other: massInMonth((S.other && S.other.daily) || [], ym),
    };
  });
  const migration = [];
  for (let i = 1; i < cols.length; i++) {
    const prev = new Set((cols[i - 1].units || []).map((u) => u.theme_id));
    (cols[i].units || []).forEach((u) => {
      if (u.theme_id && prev.has(u.theme_id)) {
        migration.push({
          from_id: u.theme_id,
          to_id: u.theme_id,
          day: cols[i].from,
          op: "continue",
          name_from: u.name,
          name_to: u.name,
        });
      }
    });
  }
  return { cols, migration };
}

function drawAlluvial() {
  const svg = document.getElementById("alluvial");
  const panel = document.getElementById("flow-panel");
  const hint = document.getElementById("flow-hint");
  const weekCols = S.columns || [];
  const byMonth = weekCols.length > 8;
  const pack = byMonth ? monthFlow() : { cols: weekCols, migration: S.migration || [] };
  const cols = pack.cols || [];
  const migration = pack.migration || [];
  if (panel) panel.hidden = cols.length < 3;
  if (!svg || cols.length < 3) {
    if (svg) svg.innerHTML = "";
    return;
  }
  const W = 1180;
  const H = 280;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.style.width = "100%";
  svg.innerHTML = "";
  if (hint) {
    hint.textContent = byMonth
      ? "全程按月看稿量怎么接，不是事态因果。"
      : "7 日列之间稿量怎么接，不是事态因果。";
  }
  const n = cols.length;
  const colW = 92;
  const gap = (W - 48 - n * colW) / Math.max(n - 1, 1);
  const x0 = (i) => 24 + i * (colW + gap);
  const totals = cols.map((c) => c.units.reduce((s, u) => s + u.mass, 0) + (c.other || 0));
  const vmax = Math.max(...totals, 1);
  const innerH = 220;
  const top = 32;
  const layout = cols.map((c, i) => {
    const items = c.units.map((u) => ({ id: u.theme_id, mass: u.mass, name: u.name })).filter((u) => u.mass > 0);
    if ((c.other || 0) > 0) items.push({ id: "", mass: c.other, name: "其余合计" });
    let y = top;
    const scale = innerH / vmax;
    return items.map((u) => {
      const h = Math.max(4, u.mass * scale);
      const rec = { ...u, x: x0(i), y, h, i };
      y += h + 4;
      return rec;
    });
  });
  const g = el("g");
  const find = (i, id) => layout[i].find((u) => u.id === id);
  migration.forEach((m) => {
    if (!m.from_id || !m.to_id) return;
    const ci = cols.findIndex((c) => c.from === m.day);
    const leftI = ci - 1;
    if (leftI < 0) return;
    const a = find(leftI, m.from_id);
    const b = find(ci, m.to_id);
    if (!a || !b) return;
    const x1 = a.x + colW;
    const x2 = b.x;
    const cpx = (x1 + x2) / 2;
    const d = `M ${x1} ${a.y} C ${cpx} ${a.y}, ${cpx} ${b.y}, ${x2} ${b.y} L ${x2} ${b.y + b.h} C ${cpx} ${b.y + b.h}, ${cpx} ${a.y + a.h}, ${x1} ${a.y + a.h} Z`;
    const p = el("path", {
      d,
      fill: colorOf(m.from_id),
      "fill-opacity": dimmed(m.from_id) && dimmed(m.to_id) ? 0.04 : m.op === "continue" ? 0.22 : 0.42,
    });
    p.addEventListener("mousemove", (ev) =>
      showTip(ev, `${OP_ZH[m.op] || m.op}<br>${esc(m.name_from || "")}<br>→ ${esc(m.name_to || "")}`)
    );
    p.addEventListener("mouseleave", hideTip);
    g.appendChild(p);
  });
  layout.forEach((items, i) => {
    g.appendChild(
      el("text", {
        x: x0(i) + colW / 2,
        y: 22,
        "text-anchor": "middle",
        "font-size": 11,
        fill: "#8b97a8",
        text: cols[i].label || cols[i].from.slice(5) + "–" + cols[i].to.slice(5),
      })
    );
    items.forEach((u) => {
      const r = el("rect", {
        x: u.x,
        y: u.y,
        width: colW,
        height: u.h,
        rx: 2,
        fill: colorOf(u.id),
        "fill-opacity": dimmed(u.id) ? 0.12 : u.id ? 0.95 : 0.4,
      });
      r.style.cursor = u.id ? "pointer" : "default";
      r.addEventListener("click", () => u.id && setSelect(u.id));
      r.addEventListener("mousemove", (ev) => showTip(ev, `<b>${esc(u.name)}</b><br>${u.mass} 篇`));
      r.addEventListener("mouseleave", hideTip);
      g.appendChild(r);
      if (u.h > 18) {
        g.appendChild(
          el("text", {
            x: u.x + 6,
            y: u.y + Math.min(16, u.h - 4),
            "font-size": 11,
            fill: u.id ? "#f4f0e6" : "#d0d6de",
            text: (u.name || "").slice(0, 8),
          })
        );
      }
    });
  });
  svg.appendChild(g);
}

function beatWhen(b) {
  const a = (b.from || "").slice(5);
  const c = (b.to || "").slice(5);
  if (!a) return "";
  if (!c || a === c) return a.replace("-", "月") + "日";
  const am = a.slice(0, 2);
  const bm = c.slice(0, 2);
  if (am === bm) return Number(am) + "月" + Number(a.slice(3)) + "–" + Number(c.slice(3)) + "日";
  return Number(am) + "月" + Number(a.slice(3)) + "日–" + Number(bm) + "月" + Number(c.slice(3)) + "日";
}

function renderNarrative(t, cut) {
  const nar = (t && t.narrative) || {};
  const beats = (nar.beats || []).filter((b) => !cut || (b.from || "") <= cut);
  if (!(nar.lede || beats.length)) return "";
  const body = beats
    .map(
      (b) => `<li class="nar-beat role-${esc(b.role)}">
        <span class="when">${esc(beatWhen(b))}</span>
        <span class="op">${esc(b.role)}</span>
        <p>${esc(b.text)}</p>
      </li>`
    )
    .join("");
  return `${nar.lede ? `<p class="nar-lede">${esc(nar.lede)}</p>` : ""}
    <ol class="nar-beats">${body}</ol>`;
}

function syncScrub() {
  const scrub = document.getElementById("scrub");
  const lab = document.getElementById("play-day");
  const n = Math.max(nDays() - 1, 1);
  scrub.max = String(n);
  scrub.value = String(playIdx < 0 ? n : playIdx);
  lab.textContent = playIdx < 0 ? "全程" : (dayAt(playIdx) || "").slice(5);
}

function syncDetailHeight() {
  const left = document.getElementById("core-wrap");
  const right = document.getElementById("detail-wrap");
  if (!left || !right) return;
  right.style.maxHeight = "";
  if (window.matchMedia("(max-width: 720px)").matches) return;
  const h = Math.round(left.getBoundingClientRect().height);
  if (h > 80) right.style.maxHeight = h + "px";
}

function draw() {
  drawMonths();
  drawKpis();
  drawRiver();
  drawBubbles();
  drawCore();
  drawMinors();
  drawDetail();
  drawAlluvial();
  syncScrub();
  requestAnimationFrame(syncDetailHeight);
}

window.addEventListener("resize", () => requestAnimationFrame(syncDetailHeight));

document.getElementById("reset").onclick = () => {
  selected = (themes[0] && themes[0].theme_id) || null;
  playIdx = -1;
  stopPlay();
  draw();
};
document.getElementById("play").onclick = () => startPlay();
document.getElementById("scrub").oninput = (ev) => {
  stopPlay();
  playIdx = Number(ev.target.value);
  draw();
};
bind(window.SLICE || (window.SLICES && window.SLICES[sliceKeys().slice(-1)[0]]));
draw();
