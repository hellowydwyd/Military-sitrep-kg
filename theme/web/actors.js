const TYPE_ZH = {
  Person: "人物",
  Org: "机构",
  Country: "国家",
  MilitaryEntity: "军事实体",
  Media: "媒体",
};
const TYPE_COLOR = {
  Person: "#7eb4d4",
  Org: "#5ec4a0",
  Country: "#e2c17a",
  MilitaryEntity: "#d97a6c",
  Media: "#8b97a8",
};
const TYPE_SHAPE = {
  Person: "circle",
  Org: "hexagon",
  Country: "rect",
  MilitaryEntity: "diamond",
  Media: "parallelogram",
};
const TYPE_ORDER = ["Country", "Org", "Person", "MilitaryEntity", "Media"];
const KIND_ZH = { attacks: "打击", opposes: "对立", cooperates: "合作", belongs_to: "从属" };
const KIND_COLOR = {
  attacks: "#e06a5c",
  opposes: "#e0a15c",
  cooperates: "#4fb38a",
  belongs_to: "#6d7788",
};
const KIND_ORDER = ["attacks", "opposes", "cooperates", "belongs_to"];
const VW = 1180;
const VH = 820;
const WORLD_W = 6400;
const WORLD_H = 4800;
const NODE_FONT = 32;
const layoutCache = new Map();
const cam = { x: 0, y: 0, k: 1 };
let fittedSlice = "";
let graphDrag = null;

let S = window.ACTOR_SLICE;
let selected = null;
let typeTab = "Country";
let kindFilter = null;
const tip = document.getElementById("tip");

function esc(s) {
  return String(s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
function el(tag, attrs) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === "text") n.textContent = v;
    else if (v != null) n.setAttribute(k, String(v));
  });
  return n;
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
    return Number(a.slice(5)) + "–" + Number(b.slice(5)) + "月对照";
  }
  if (k.length === 10) {
    return Number(k.slice(5, 7)) + "月" + Number(k.slice(8)) + "日";
  }
  return Number(k.slice(5)) + "月";
}
function sliceKeys() {
  const keys = Object.keys(window.ACTOR_SLICES || {});
  const months = keys.filter((k) => k.length === 7).sort();
  const days = keys.filter((k) => k.length === 10).sort();
  const spans = keys.filter((k) => k.includes("~")).sort();
  return months.concat(days).concat(spans);
}
function entityOf(id) {
  return (S.entities || []).find((e) => e.id === id);
}
function setSelect(id) {
  selected = id && id === selected ? null : id || null;
  draw();
}
function pickDefault() {
  const action = ((S.graph && S.graph.edges) || [])
    .filter((e) => e.kind !== "belongs_to")
    .sort((a, b) => b.weight - a.weight);
  if (action[0]) return action[0].source;
  const countries = (S.ranks && S.ranks.Country) || [];
  return (countries[0] && countries[0].id) || null;
}

function bind(slice) {
  S = slice;
  window.ACTOR_SLICE = slice;
  kindFilter = null;
  fittedSlice = "";
  selected = pickDefault();
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
      const slice = window.ACTOR_SLICES[host.value];
      if (!slice) return;
      bind(slice);
      draw();
    });
    host.dataset.bound = "1";
  }
}

function drawKpis() {
  const host = document.getElementById("lede");
  if (!host) return;
  const m = S.metrics || {};
  const rel = m.n_rel || {};
  const nRel = ["attacks", "opposes", "cooperates"].reduce((s, k) => s + (rel[k] || 0), 0);
  const top = visibleEdges()[0];
  const bits = [`${m.n_docs || 0} 篇`];
  if (nRel) bits.push(`${nRel} 条打击/对立/合作`);
  else if (rel.belongs_to) bits.push(`${rel.belongs_to} 条从属`);
  if (m.n_media) bits.push(`${m.n_media} 家信源`);
  if (top) bits.push(`最显著：${nameOf(top.source)} ${KIND_ZH[top.kind] || ""} ${nameOf(top.target)}`);
  host.textContent = bits.join("  ·  ");
}

function nameOf(id) {
  const n = entityOf(id) || ((S.graph && S.graph.nodes) || []).find((x) => x.id === id);
  return n ? n.name : id;
}

function visibleEdges() {
  const edges = [...((S.graph && S.graph.edges) || [])];
  const filtered = kindFilter ? edges.filter((e) => e.kind === kindFilter) : edges;
  const action = filtered.filter((e) => e.kind !== "belongs_to");
  if (!kindFilter && action.length) return action.sort((a, b) => b.weight - a.weight);
  return filtered.sort((a, b) => b.weight - a.weight);
}

function drawLegend() {
  const legend = document.getElementById("legend");
  const kindsPresent = KIND_ORDER.filter((k) => ((S.graph && S.graph.edges) || []).some((e) => e.kind === k));
  legend.innerHTML =
    `<button type="button" class="${kindFilter ? "" : "on"}" data-k=""><i style="background:#8b97a8"></i>全部关系</button>` +
    kindsPresent
      .map(
        (k) =>
          `<button type="button" class="${kindFilter === k ? "on" : ""}" data-k="${k}"><i style="background:${KIND_COLOR[k]}"></i>${KIND_ZH[k]}</button>`
      )
      .join("");
  legend.querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      kindFilter = b.getAttribute("data-k") || null;
      draw();
    };
  });
}

function zhDay(iso) {
  if (!iso || iso.length < 10) return "";
  return `${Number(iso.slice(5, 7))}月${Number(iso.slice(8, 10))}日`;
}
function zhSpan(e) {
  const a = zhDay(e.first_day);
  const b = zhDay(e.last_day);
  if (!a) return "";
  if (!b || a === b) return a;
  return a + "–" + b;
}

function drawRels() {
  const host = document.getElementById("rel-list");
  const rows = visibleEdges().slice(0, 6);
  if (!rows.length) {
    host.innerHTML = '<p class="empty-detail">这个窗还没有打击 / 对立 / 合作。点右上角「从属」看军队隶属。</p>';
    return;
  }
  host.innerHTML = rows
    .map((e) => {
      const on = selected && (selected === e.source || selected === e.target);
      const dim = selected && !on;
      const lab = KIND_ZH[e.kind] || e.label || "";
      const when = zhSpan(e);
      return `<button type="button" class="rel-row ${on ? "on" : dim ? "dim" : ""}" data-src="${esc(e.source)}" data-tgt="${esc(e.target)}">
        <span class="who left">${esc(nameOf(e.source))}</span>
        <span class="mid"><span class="badge" style="background:${KIND_COLOR[e.kind]}">${esc(lab)}</span>${when ? `<span class="when">${esc(when)}</span>` : ""}</span>
        <span class="who right">${esc(nameOf(e.target))}</span>
      </button>`;
    })
    .join("");
  host.querySelectorAll(".rel-row").forEach((n) => {
    n.onclick = () => setSelect(n.getAttribute("data-src"));
  });
}

function pillOf(name, type) {
  const label = String(name || "").length > 8 ? String(name).slice(0, 8) + "…" : String(name || "");
  let pw = Math.max(120, 56 + label.length * NODE_FONT);
  let ph = 56;
  if (type === "Person") {
    ph = Math.max(70, pw * 0.48);
    pw = Math.max(pw, ph + 12);
  } else if (type === "MilitaryEntity") {
    pw += 48;
    ph = 88;
  } else if (type === "Org") {
    pw += 28;
    ph = 68;
  } else if (type === "Media") {
    pw += 24;
    ph = 58;
  } else {
    ph = 54;
  }
  return { label, pw, ph, type: type || "Org" };
}

function shapeD(type, x, y, pw, ph) {
  const hw = pw / 2;
  const hh = ph / 2;
  if (type === "MilitaryEntity") {
    return `M ${x} ${y - hh} L ${x + hw} ${y} L ${x} ${y + hh} L ${x - hw} ${y} Z`;
  }
  if (type === "Org") {
    const ox = hw * 0.42;
    return `M ${x - hw + ox} ${y - hh} L ${x + hw - ox} ${y - hh} L ${x + hw} ${y} L ${x + hw - ox} ${y + hh} L ${x - hw + ox} ${y + hh} L ${x - hw} ${y} Z`;
  }
  if (type === "Media") {
    const skew = hw * 0.22;
    return `M ${x - hw + skew} ${y - hh} L ${x + hw} ${y - hh} L ${x + hw - skew} ${y + hh} L ${x - hw} ${y + hh} Z`;
  }
  return null;
}

function appendNodeShape(g, type, p, fill, stroke, sw) {
  const attrs = { fill, stroke, "stroke-width": sw };
  if (type === "Person") {
    g.appendChild(el("ellipse", { cx: p.x, cy: p.y, rx: p.pw / 2, ry: p.ph / 2, ...attrs }));
    return;
  }
  if (type === "Country") {
    g.appendChild(
      el("rect", {
        x: p.x - p.pw / 2,
        y: p.y - p.ph / 2,
        width: p.pw,
        height: p.ph,
        rx: 8,
        ...attrs,
      })
    );
    return;
  }
  const d = shapeD(type, p.x, p.y, p.pw, p.ph);
  if (d) {
    g.appendChild(el("path", { d, ...attrs }));
    return;
  }
  g.appendChild(
    el("rect", {
      x: p.x - p.pw / 2,
      y: p.y - p.ph / 2,
      width: p.pw,
      height: p.ph,
      rx: 8,
      ...attrs,
    })
  );
}

function graphNodes() {
  return [...((S.graph && S.graph.nodes) || [])];
}
function graphEdges() {
  const edges = [...((S.graph && S.graph.edges) || [])];
  return kindFilter ? edges.filter((e) => e.kind === kindFilter) : edges;
}

function positionsFor(nodes) {
  const key = "shape1:" + sliceKeyOf(S) + ":" + nodes.map((n) => n.id).join(",");
  if (layoutCache.has(key)) return layoutCache.get(key);
  const n = Math.max(nodes.length, 1);
  const ring = 900 + n * 28;
  const pts = nodes.map((node, i) => {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    const jitter = ((i * 19) % 13) - 6;
    const pill = pillOf(node.name, node.type);
    return {
      id: node.id,
      x: WORLD_W / 2 + Math.cos(a) * (ring + jitter * 40),
      y: WORLD_H / 2 + Math.sin(a) * (ring * 0.72 + jitter * 32),
      vx: 0,
      vy: 0,
      pw: pill.pw,
      ph: pill.ph,
      label: pill.label,
      r: Math.hypot(pill.pw / 2, pill.ph / 2),
      type: node.type || "Org",
    };
  });
  const idx = Object.fromEntries(pts.map((p, i) => [p.id, i]));
  const links = ((S.graph && S.graph.edges) || [])
    .map((e) => ({ s: idx[e.source], t: idx[e.target], kind: e.kind }))
    .filter((l) => l.s != null && l.t != null);
  for (let k = 0; k < 360; k++) {
    const alpha = 0.16 * (1 - k / 360);
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        let dx = pts[j].x - pts[i].x;
        let dy = pts[j].y - pts[i].y;
        let d2 = dx * dx + dy * dy || 1;
        let d = Math.sqrt(d2);
        const minD = (pts[i].pw + pts[j].pw) / 2 + 90;
        const force = 160000 / d2 + (d < minD ? (minD - d) * 0.35 : 0);
        const fx = (dx / d) * force;
        const fy = (dy / d) * force;
        pts[i].vx -= fx;
        pts[i].vy -= fy;
        pts[j].vx += fx;
        pts[j].vy += fy;
      }
    }
    for (const l of links) {
      const a = pts[l.s];
      const b = pts[l.t];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = l.kind === "belongs_to" ? 280 : 560;
      const f = (d - rest) * 0.012;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    }
    for (const p of pts) {
      p.vx += (WORLD_W / 2 - p.x) * 0.0012;
      p.vy += (WORLD_H / 2 - p.y) * 0.0012;
      p.vx *= 0.64;
      p.vy *= 0.64;
      p.x += p.vx * 14 * (alpha / 0.16);
      p.y += p.vy * 14 * (alpha / 0.16);
      p.x = Math.max(220, Math.min(WORLD_W - 220, p.x));
      p.y = Math.max(220, Math.min(WORLD_H - 220, p.y));
    }
  }
  const pos = {};
  pts.forEach((p) => {
    pos[p.id] = { x: p.x, y: p.y, r: p.r, pw: p.pw, ph: p.ph, label: p.label, type: p.type };
  });
  layoutCache.set(key, pos);
  return pos;
}

function applyCam() {
  const g = document.getElementById("graph-cam");
  if (g) g.setAttribute("transform", `translate(${cam.x} ${cam.y}) scale(${cam.k})`);
  const showEdge = cam.k >= 0.58;
  document.querySelectorAll("#graph-world .elabel").forEach((n) => {
    const keep = n.getAttribute("data-hot") === "1";
    n.setAttribute("opacity", showEdge || keep ? "1" : "0");
  });
}

function eventView(ev) {
  const svg = document.getElementById("graph");
  const pt = svg.createSVGPoint();
  pt.x = ev.clientX;
  pt.y = ev.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

function eventWorld(ev) {
  const p = eventView(ev);
  return { x: (p.x - cam.x) / cam.k, y: (p.y - cam.y) / cam.k };
}

function zoomAt(viewPt, factor) {
  const k2 = Math.min(5, Math.max(0.08, cam.k * factor));
  const wx = (viewPt.x - cam.x) / cam.k;
  const wy = (viewPt.y - cam.y) / cam.k;
  cam.k = k2;
  cam.x = viewPt.x - wx * cam.k;
  cam.y = viewPt.y - wy * cam.k;
  applyCam();
}

function edgePoint(p, qx, qy) {
  const dx = qx - p.x;
  const dy = qy - p.y;
  const hw = (p.pw || 80) / 2 + 6;
  const hh = (p.ph || 40) / 2 + 6;
  const type = p.type;
  if (type === "Person") {
    const d = Math.hypot(dx / hw, dy / hh) || 1;
    return { x: p.x + dx / d, y: p.y + dy / d };
  }
  if (type === "MilitaryEntity") {
    const t = 1 / Math.max(Math.abs(dx) / hw + Math.abs(dy) / hh, 0.001);
    return { x: p.x + dx * t, y: p.y + dy * t };
  }
  const ax = Math.abs(dx) / hw;
  const ay = Math.abs(dy) / hh;
  const t = 1 / Math.max(ax, ay, 0.001);
  return { x: p.x + dx * t, y: p.y + dy * t };
}

function fitCam() {
  const nodes = graphNodes();
  const pos = positionsFor(nodes);
  let minx = Infinity;
  let miny = Infinity;
  let maxx = -Infinity;
  let maxy = -Infinity;
  let minD = Infinity;
  nodes.forEach((n) => {
    const p = pos[n.id];
    if (!p) return;
    const hw = (p.pw || 80) / 2;
    const hh = (p.ph || 40) / 2;
    minx = Math.min(minx, p.x - hw - 24);
    miny = Math.min(miny, p.y - hh - 24);
    maxx = Math.max(maxx, p.x + hw + 24);
    maxy = Math.max(maxy, p.y + hh + 24);
  });
  for (let i = 0; i < nodes.length; i++) {
    const a = pos[nodes[i].id];
    if (!a) continue;
    for (let j = i + 1; j < nodes.length; j++) {
      const b = pos[nodes[j].id];
      if (!b) continue;
      minD = Math.min(minD, Math.hypot(a.x - b.x, a.y - b.y));
    }
  }
  if (!isFinite(minx)) {
    cam.k = 0.6;
    cam.x = VW / 2 - (WORLD_W / 2) * cam.k;
    cam.y = VH / 2 - (WORLD_H / 2) * cam.k;
    applyCam();
    return;
  }
  const kRead = 18 / NODE_FONT;
  const kSpace = 150 / Math.max(minD, 1);
  cam.k = Math.min(1.35, Math.max(0.52, kRead, kSpace));
  cam.x = VW / 2 - cam.k * ((minx + maxx) / 2);
  cam.y = VH / 2 - cam.k * ((miny + maxy) / 2);
  applyCam();
}

function bindGraphUi(svg) {
  if (svg.dataset.ui === "1") return;
  svg.dataset.ui = "1";
  svg.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      zoomAt(eventView(ev), ev.deltaY < 0 ? 1.12 : 1 / 1.12);
    },
    { passive: false }
  );
  svg.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    const nidEl = ev.target.closest("[data-nid]");
    const p = eventView(ev);
    if (nidEl) {
      const id = nidEl.getAttribute("data-nid");
      const w = eventWorld(ev);
      const pos = positionsFor(graphNodes())[id];
      graphDrag = {
        mode: "node",
        id,
        ox: w.x - pos.x,
        oy: w.y - pos.y,
        sx: p.x,
        sy: p.y,
        moved: false,
      };
    } else {
      graphDrag = { mode: "pan", sx: p.x, sy: p.y, cx: cam.x, cy: cam.y, moved: false };
      svg.classList.add("panning");
    }
    svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener("pointermove", (ev) => {
    if (!graphDrag) return;
    const p = eventView(ev);
    if (Math.hypot(p.x - graphDrag.sx, p.y - graphDrag.sy) > 5) graphDrag.moved = true;
    if (graphDrag.mode === "pan") {
      cam.x = graphDrag.cx + (p.x - graphDrag.sx);
      cam.y = graphDrag.cy + (p.y - graphDrag.sy);
      applyCam();
    } else {
      const w = eventWorld(ev);
      const pos = positionsFor(graphNodes())[graphDrag.id];
      pos.x = w.x - graphDrag.ox;
      pos.y = w.y - graphDrag.oy;
      paintWorld();
    }
    hideTip();
  });
  const endDrag = (ev) => {
    if (!graphDrag) return;
    const d = graphDrag;
    graphDrag = null;
    svg.classList.remove("panning");
    try {
      svg.releasePointerCapture(ev.pointerId);
    } catch (err) {}
    if (d.mode === "node" && !d.moved) setSelect(d.id);
  };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("dblclick", (ev) => {
    if (ev.target.closest("[data-nid]")) return;
    fitCam();
  });
}

function ensureStage() {
  const svg = document.getElementById("graph");
  if (svg.dataset.canvas === "1") return svg;
  svg.dataset.canvas = "1";
  svg.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
  svg.innerHTML = "";
  const defs = el("defs");
  KIND_ORDER.forEach((k) => {
    const m = el("marker", {
      id: "arr-" + k,
      viewBox: "0 0 10 10",
      refX: 9,
      refY: 5,
      markerWidth: 11,
      markerHeight: 11,
      orient: "auto-start-reverse",
      markerUnits: "userSpaceOnUse",
    });
    m.appendChild(el("path", { d: "M 0 1.2 L 10 5 L 0 8.8 Z", fill: KIND_COLOR[k] }));
    defs.appendChild(m);
  });
  svg.appendChild(defs);
  const camG = el("g", { id: "graph-cam" });
  camG.appendChild(
    el("rect", {
      id: "graph-bg",
      x: -12000,
      y: -12000,
      width: 30000,
      height: 30000,
      fill: "transparent",
    })
  );
  camG.appendChild(el("g", { id: "graph-world" }));
  svg.appendChild(camG);
  bindGraphUi(svg);
  const zin = document.getElementById("g-zoom-in");
  const zout = document.getElementById("g-zoom-out");
  const fit = document.getElementById("g-fit");
  if (zin && zin.dataset.ui !== "1") {
    zin.dataset.ui = "1";
    zin.onclick = () => zoomAt({ x: VW / 2, y: VH / 2 }, 1.22);
    zout.onclick = () => zoomAt({ x: VW / 2, y: VH / 2 }, 1 / 1.22);
    fit.onclick = () => fitCam();
  }
  return svg;
}

function paintWorld() {
  const wrap = document.getElementById("graph-world");
  const cap = document.getElementById("graph-caption");
  if (!wrap) return;
  wrap.innerHTML = "";
  const nodes = graphNodes();
  const edges = graphEdges();
  if (cap) cap.textContent = nodes.length ? `${nodes.length} 个主体 · ${edges.length} 条关系 · 拖动画布查看` : "";
  if (!nodes.length || !edges.length) {
    wrap.appendChild(
      el("text", {
        x: WORLD_W / 2,
        y: WORLD_H / 2,
        "text-anchor": "middle",
        "font-size": 42,
        fill: "#8b97a8",
        text: "这个窗里还没有关系边。4–6 月和 9 月可点「从属」。",
      })
    );
    return;
  }
  const pos = positionsFor(nodes);
  const hot = new Set();
  if (selected) {
    hot.add(selected);
    edges.forEach((e) => {
      if (e.source === selected || e.target === selected) {
        hot.add(e.source);
        hot.add(e.target);
      }
    });
  }
  const pairCount = {};
  const drawn = edges.map((e) => {
    const k = e.source + ">" + e.target;
    pairCount[k] = (pairCount[k] || 0) + 1;
    return { e, bendN: pairCount[k] };
  });
  drawn.forEach(({ e, bendN }) => {
    const a = pos[e.source];
    const b = pos[e.target];
    if (!a || !b) return;
    const on = !selected || e.source === selected || e.target === selected;
    const col = KIND_COLOR[e.kind] || "#8b97a8";
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const nx = -dy / d;
    const ny = dx / d;
    const bend = (bendN - 1) * 48;
    const mx = (a.x + b.x) / 2 + nx * (40 + bend);
    const my = (a.y + b.y) / 2 + ny * (40 + bend);
    const p1 = edgePoint(a, mx, my);
    const p2 = edgePoint(b, mx, my);
    const path = el("path", {
      d: `M ${p1.x} ${p1.y} Q ${mx} ${my} ${p2.x} ${p2.y}`,
      fill: "none",
      stroke: col,
      "stroke-width": e.kind === "belongs_to" ? 2.2 : 4,
      "stroke-dasharray": e.kind === "belongs_to" ? "10 8" : "",
      "marker-end": `url(#arr-${e.kind})`,
      opacity: selected && !on ? 0.1 : e.kind === "belongs_to" ? 0.45 : 0.95,
    });
    const hit = el("path", {
      d: `M ${p1.x} ${p1.y} Q ${mx} ${my} ${p2.x} ${p2.y}`,
      fill: "none",
      stroke: "transparent",
      "stroke-width": 28,
    });
    hit.style.cursor = "pointer";
    const tipHtml = `${esc(nameOf(e.source))} ${esc(KIND_ZH[e.kind] || e.kind)} ${esc(nameOf(e.target))}${zhSpan(e) ? "<br>见报 " + esc(zhSpan(e)) : ""}`;
    hit.addEventListener("mousemove", (ev) => showTip(ev, tipHtml));
    hit.addEventListener("mouseleave", hideTip);
    wrap.appendChild(path);
    wrap.appendChild(hit);
    if (e.kind !== "belongs_to") {
      const lab = KIND_ZH[e.kind] || "";
      const tw = 28 + lab.length * 22;
      const gLab = el("g");
      gLab.setAttribute("class", "elabel");
      gLab.setAttribute("data-hot", selected && on ? "1" : "0");
      gLab.setAttribute("pointer-events", "none");
      gLab.appendChild(
        el("rect", {
          x: mx - tw / 2,
          y: my - 18,
          width: tw,
          height: 36,
          rx: 18,
          fill: col,
        })
      );
      gLab.appendChild(
        el("text", {
          x: mx,
          y: my + 8,
          "text-anchor": "middle",
          "font-size": 22,
          "font-weight": 700,
          fill: "#0c1016",
          text: lab,
        })
      );
      wrap.appendChild(gLab);
    }
  });
  const incident = new Set();
  edges.forEach((e) => {
    incident.add(e.source);
    incident.add(e.target);
  });
  nodes.forEach((n) => {
    const p = pos[n.id];
    if (!p) return;
    if (!p.pw) Object.assign(p, pillOf(n.name, n.type));
    p.type = n.type || p.type;
    const on = incident.has(n.id) && (!selected || hot.has(n.id));
    const fill = TYPE_COLOR[n.type] || "#8b97a8";
    const gnode = el("g");
    gnode.setAttribute("data-nid", n.id);
    gnode.style.cursor = "grab";
    gnode.setAttribute("opacity", selected && !on ? "0.16" : "1");
    appendNodeShape(
      gnode,
      n.type,
      p,
      fill,
      selected === n.id ? "#f4f0e6" : "#0c1016",
      selected === n.id ? 4 : 1.8
    );
    gnode.appendChild(
      el("text", {
        x: p.x,
        y: p.y + 11,
        "text-anchor": "middle",
        "font-size": NODE_FONT,
        "font-weight": 700,
        fill: "#0c1016",
        text: p.label || n.name,
      })
    );
    gnode.addEventListener("mousemove", (ev) =>
      showTip(ev, `${esc(n.name)} · ${esc(TYPE_ZH[n.type] || n.type)} · ${n.mass} 篇`)
    );
    gnode.addEventListener("mouseleave", hideTip);
    wrap.appendChild(gnode);
  });
  applyCam();
}
function drawGraph() {
  ensureStage();
  const svg = document.getElementById("graph");
  if (svg) svg.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
  paintWorld();
  const key = sliceKeyOf(S);
  if (fittedSlice !== key) {
    fittedSlice = key;
    fitCam();
  } else {
    applyCam();
  }
}

function drawTabs() {
  const host = document.getElementById("type-tabs");
  const types = TYPE_ORDER.filter((t) => ((S.ranks || {})[t] || []).length);
  if (types.length && !types.includes(typeTab)) typeTab = types[0];
  host.innerHTML = types.map((t) => {
    const n = ((S.ranks || {})[t] || []).length;
    return `<button type="button" class="${t === typeTab ? "on" : ""}" data-t="${t}">${TYPE_ZH[t]} ${n}</button>`;
  }).join("");
  host.querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      typeTab = b.getAttribute("data-t");
      draw();
    };
  });
}

function drawRank() {
  const rows = (S.ranks || {})[typeTab] || [];
  const host = document.getElementById("rank");
  if (!rows.length) {
    host.innerHTML = '<p class="empty-detail">这一类在窗内没有足够提及。</p>';
    return;
  }
  host.innerHTML = rows
    .map(
      (r, i) => `<button type="button" class="core-card ${selected === r.id ? "on" : selected ? "dim" : ""}" data-id="${esc(r.id)}">
        <span class="rank">${i + 1}</span>
        <span class="name">${esc(r.name)}</span>
        <span class="mass">${r.mass} 篇</span>
        <span class="stage">${r.n_source}/${r.n_target} · 度 ${r.degree}</span>
      </button>`
    )
    .join("");
  host.querySelectorAll("[data-id]").forEach((n) => {
    n.onclick = () => setSelect(n.getAttribute("data-id"));
  });
}

function neighborsOf(id) {
  return ((S.graph && S.graph.edges) || [])
    .filter((e) => e.source === id || e.target === id)
    .map((e) => {
      const out = e.source === id;
      const other = out ? e.target : e.source;
      const ent = entityOf(other) || ((S.graph.nodes || []).find((n) => n.id === other));
      return {
        id: other,
        name: ent ? ent.name : other,
        n: e.n_docs,
        w: e.weight,
        kind: e.kind,
        trigger: e.trigger || e.label,
        first_day: e.first_day,
        last_day: e.last_day,
        dir: out ? "→" : "←",
      };
    })
    .sort((a, b) => b.w - a.w)
    .slice(0, 14);
}

function drawDetail() {
  const host = document.getElementById("detail");
  const t = entityOf(selected);
  if (!t) {
    host.innerHTML =
      '<p class="empty-detail">点图上的名字、底下一条关系，或上面排行。</p>';
    return;
  }
  const neigh = neighborsOf(t.id)
    .map(
      (n) =>
        `<li class="branch"><span>${esc(KIND_ZH[n.kind] || n.kind)} ${n.dir}</span> ${esc(n.name)} <span class="n">${esc(zhSpan(n) || n.trigger)}</span></li>`
    )
    .join("");
  const arts = (t.articles || [])
    .map((a) => {
      const media = a.source_name ? `<span class="media">${esc(a.source_name)}</span>` : "";
      const inner = `<span class="day">${esc((a.day || "").slice(5))}</span>${media}${esc(a.title)}`;
      return a.url
        ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${inner}</a>`
        : `<span class="nolink">${inner}</span>`;
    })
    .join("");
  const mediaLine = (t.media || [])
    .slice(0, 6)
    .map((x) => `${x.name} ${x.n}`)
    .join(" · ");
  host.innerHTML = `
    <p class="detail-name">${esc(t.name)}</p>
    <p class="detail-meta">${TYPE_ZH[t.type] || t.type} · ${t.mass} 篇 · 主体 ${t.n_source} / 对象 ${t.n_target} · 关系度 ${t.degree}</p>
    ${mediaLine ? `<p class="detail-meta">信源 ${esc(mediaLine)}</p>` : ""}
    ${t.aliases && t.aliases.length ? `<p class="detail-meta">别名 ${esc(t.aliases.join("、"))}</p>` : ""}
    ${neigh ? `<ul class="tree">${neigh}</ul>` : ""}
    <div class="arts"><p class="arts-kicker">全部见报稿</p>${arts || '<p class="empty">没有稿。</p>'}</div>
  `;
}

function drawShapeLegend() {
  const host = document.getElementById("shape-legend");
  if (!host) return;
  const icon = (t) => {
    const fill = TYPE_COLOR[t];
    if (t === "Person")
      return `<ellipse cx="12" cy="10" rx="10" ry="8" fill="${fill}" stroke="#0c1016" stroke-width="1.2"/>`;
    if (t === "Country")
      return `<rect x="2" y="4" width="20" height="12" rx="2" fill="${fill}" stroke="#0c1016" stroke-width="1.2"/>`;
    if (t === "Org")
      return `<path d="M8 3 L16 3 L21 10 L16 17 L8 17 L3 10 Z" fill="${fill}" stroke="#0c1016" stroke-width="1.2"/>`;
    if (t === "MilitaryEntity")
      return `<path d="M12 2 L22 10 L12 18 L2 10 Z" fill="${fill}" stroke="#0c1016" stroke-width="1.2"/>`;
    return `<path d="M6 3 L22 3 L18 17 L2 17 Z" fill="${fill}" stroke="#0c1016" stroke-width="1.2"/>`;
  };
  host.innerHTML = TYPE_ORDER.map(
    (t) =>
      `<span class="shape-item"><svg width="24" height="20" viewBox="0 0 24 20" aria-hidden="true">${icon(t)}</svg>${TYPE_ZH[t]}</span>`
  ).join("");
}

function draw() {
  drawMonths();
  drawKpis();
  drawLegend();
  drawShapeLegend();
  drawRels();
  drawGraph();
  drawTabs();
  drawRank();
  drawDetail();
}

bind(
  window.ACTOR_SLICE ||
    (window.ACTOR_SLICES && window.ACTOR_SLICES[sliceKeys().slice(-1)[0]])
);
draw();
