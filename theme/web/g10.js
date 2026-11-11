/* 十类知识图谱：单图 + 按节点加载邻域。
   画布沿用主体页做法：世界坐标系、相机缩放平移、按类型区分节点形状、
   关系曲线带箭头与标签、力导向去重叠。 */

const SVG = document.getElementById("graph");
const TIP = document.getElementById("tip");

const VW = 1180;
const VH = 820;
const NODE_FONT = 22;
const WORLD_PAD = 900;

const TYPE_COLOR = {
  事件: "#d97a6c",
  机构: "#5ec4a0",
  人物: "#7eb4d4",
  装备: "#b58fd0",
  地点: "#e2c17a",
  观点: "#8ba9c4",
  立场: "#d08a9c",
  场景: "#7ec4b0",
  作品: "#c4b07a",
  平台: "#8b97a8",
};
const TYPE_SHAPE = {
  事件: "diamond",
  机构: "hexagon",
  人物: "ellipse",
  装备: "rect",
  地点: "parallelogram",
  观点: "rect",
  立场: "ellipse",
  场景: "parallelogram",
  作品: "rect",
  平台: "parallelogram",
};
const ZH_ORDER = ["事件", "机构", "人物", "装备", "地点", "观点", "立场", "场景", "作品", "平台"];

const REL_ZH = {
  参与: "参与", 实施: "实施", 搭载: "搭载", 拦截: "拦截", 发射: "发射",
  展示: "展示", 生产: "生产", 部署: "部署", 从属: "从属", 去向: "去向",
  发表: "发表", 针对: "针对", 表明: "表明", 关于: "关于", 处于: "处于",
  涉及: "涉及", 发布: "发布",
};
const REL_COLOR = {
  参与: "#7eb4d4", 实施: "#5ec4a0", 搭载: "#b58fd0", 拦截: "#c45c6a",
  发射: "#d4846a", 展示: "#9ab88f", 生产: "#c4b07a", 部署: "#5c8fd4",
  从属: "#8b97a8", 去向: "#e2c17a", 发表: "#8ba9c4", 针对: "#a08ac4",
  表明: "#d08a9c", 关于: "#c48a9c", 处于: "#7ec4b0", 涉及: "#c4a06a",
  发布: "#7a8fa6",
};

const cam = { x: 0, y: 0, k: 1 };
const layoutCache = new Map();
let cur = null;
let filterType = "";
let filterRel = "";
let selectedNeighbor = null;
let nodeIndex = {};
let drag = null;

/* ---------- 基础工具 ---------- */
function el(tag, attrs) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === "text") n.textContent = v;
    else if (v != null) n.setAttribute(k, String(v));
  });
  return n;
}
function esc(s) {
  return String(s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function clip(s, n) {
  const t = String(s || "");
  return t.length > n ? t.slice(0, n) + "…" : t;
}
function brief(s, max) {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  if (!t) return "";
  const m = t.match(/^.{12,}?[。！？；]/);
  let out = m ? m[0] : t;
  const lim = max || 64;
  return out.length > lim ? out.slice(0, lim - 1) + "…" : out;
}
function showTip(ev, html) {
  TIP.innerHTML = html;
  TIP.classList.add("show");
  TIP.style.left = Math.min(ev.clientX + 12, window.innerWidth - 380) + "px";
  TIP.style.top = Math.min(ev.clientY + 12, window.innerHeight - 90) + "px";
}
function hideTip() { TIP.classList.remove("show"); }

/* ---------- 节点外形 ---------- */
function pillOf(name) {
  const label = clip(name, 10);
  const w = Math.max(96, label.length * NODE_FONT * 1.04 + 34);
  return { label, pw: w, ph: 56 };
}

function shapeD(shape, x, y, pw, ph) {
  const hw = pw / 2, hh = ph / 2;
  if (shape === "diamond") {
    return `M ${x} ${y - hh} L ${x + hw} ${y} L ${x} ${y + hh} L ${x - hw} ${y} Z`;
  }
  if (shape === "hexagon") {
    const ox = hw * 0.4;
    return `M ${x - hw + ox} ${y - hh} L ${x + hw - ox} ${y - hh} L ${x + hw} ${y} L ${x + hw - ox} ${y + hh} L ${x - hw + ox} ${y + hh} L ${x - hw} ${y} Z`;
  }
  if (shape === "parallelogram") {
    const skew = hw * 0.2;
    return `M ${x - hw + skew} ${y - hh} L ${x + hw} ${y - hh} L ${x + hw - skew} ${y + hh} L ${x - hw} ${y + hh} Z`;
  }
  return null;
}

function appendShape(g, type, p, fill, stroke, sw) {
  const shape = TYPE_SHAPE[type] || "rect";
  const attrs = { fill, stroke, "stroke-width": sw, "data-nid": p.id };
  if (shape === "ellipse") {
    g.appendChild(el("ellipse", { cx: p.x, cy: p.y, rx: p.pw / 2, ry: p.ph / 2, ...attrs }));
    return;
  }
  const d = shapeD(shape, p.x, p.y, p.pw, p.ph);
  if (d) {
    g.appendChild(el("path", { d, ...attrs }));
    return;
  }
  g.appendChild(el("rect", {
    x: p.x - p.pw / 2, y: p.y - p.ph / 2, width: p.pw, height: p.ph, rx: 9, ...attrs,
  }));
}

function edgePoint(p, qx, qy) {
  const dx = qx - p.x, dy = qy - p.y;
  const hw = p.pw / 2 + 5, hh = p.ph / 2 + 5;
  const shape = TYPE_SHAPE[p.type] || "rect";
  if (shape === "ellipse") {
    const d = Math.hypot(dx / hw, dy / hh) || 1;
    return { x: p.x + dx / d, y: p.y + dy / d };
  }
  if (shape === "diamond") {
    const t = 1 / Math.max(Math.abs(dx) / hw + Math.abs(dy) / hh, 0.001);
    return { x: p.x + dx * t, y: p.y + dy * t };
  }
  const t = 1 / Math.max(Math.abs(dx) / hw, Math.abs(dy) / hh, 0.001);
  return { x: p.x + dx * t, y: p.y + dy * t };
}

/* ---------- 布局：以当前节点为中心，力导向去重叠 ---------- */
function positionsFor(nodes, links) {
  const key = (cur ? cur.node.id : "") + "|" + nodes.map((n) => n.id).join(",") + "|" + filterType;
  if (layoutCache.has(key)) return layoutCache.get(key);
  const n = Math.max(nodes.length, 1);
  const pts = nodes.map((nd, i) => {
    const pill = pillOf(nd.name);
    const isCenter = cur && nd.id === cur.node.id;
    const a = (i / n) * Math.PI * 2;
    const rad = isCenter ? 0 : 300 + (i % 3) * 130;
    return {
      id: nd.id,
      x: Math.cos(a) * rad,
      y: Math.sin(a) * rad * 0.86,
      vx: 0, vy: 0,
      pw: pill.pw, ph: pill.ph, label: pill.label,
      type: nd.type || "机构",
      center: !!isCenter,
    };
  });
  const idx = Object.fromEntries(pts.map((p, i) => [p.id, i]));
  const ls = (links || []).map((e) => ({ s: idx[e.other], t: idx[(cur && cur.node.id) || ""] })).filter((l) => l.s != null && l.t != null);
  const steps = n > 120 ? 90 : n > 60 ? 160 : 300;
  for (let k = 0; k < steps; k++) {
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        let dx = pts[j].x - pts[i].x, dy = pts[j].y - pts[i].y;
        let d = Math.hypot(dx, dy) || 1;
        const minD = (pts[i].pw + pts[j].pw) / 2 + 40;
        if (d > minD * 2.4) continue;
        const force = (d < minD ? (minD - d) * 0.6 : 0) + 52000 / (d * d);
        const fx = (dx / d) * force, fy = (dy / d) * force;
        pts[i].vx -= fx; pts[i].vy -= fy;
        pts[j].vx += fx; pts[j].vy += fy;
      }
    }
    for (const l of ls) {
      const a = pts[l.s], b = pts[l.t];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1;
      const f = (d - 420) * 0.006;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }
    for (const p of pts) {
      if (p.center) { p.x = 0; p.y = 0; p.vx = 0; p.vy = 0; continue; }
      p.vx *= 0.66; p.vy *= 0.66;
      p.x += p.vx * 5; p.y += p.vy * 5;
    }
  }
  // 硬约束：消除重叠
  for (let k = 0; k < 90; k++) {
    let moved = false;
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        let dx = pts[j].x - pts[i].x, dy = pts[j].y - pts[i].y;
        let d = Math.hypot(dx, dy);
        if (d < 1) { dx = 1; dy = 0; d = 1; }
        const minD = (pts[i].pw + pts[j].pw) / 2 + 30;
        if (d >= minD) continue;
        const push = (minD - d) / 2;
        const ux = dx / d, uy = dy / d;
        if (!pts[i].center) { pts[i].x -= ux * push; pts[i].y -= uy * push; }
        if (!pts[j].center) { pts[j].x += ux * push; pts[j].y += uy * push; }
        moved = true;
      }
    }
    if (!moved) break;
  }
  const pos = {};
  pts.forEach((p) => {
    pos[p.id] = { x: p.x, y: p.y, pw: p.pw, ph: p.ph, label: p.label, type: p.type };
  });
  layoutCache.set(key, pos);
  return pos;
}

/* ---------- 相机 ---------- */
function applyCam() {
  const g = document.getElementById("graph-cam");
  if (g) g.setAttribute("transform", `translate(${cam.x} ${cam.y}) scale(${cam.k})`);
  const showLabel = cam.k >= 0.5;
  document.querySelectorAll("#graph-world .elabel").forEach((n) => {
    n.setAttribute("opacity", showLabel ? "1" : "0");
  });
}

function viewPt(ev) {
  const p = SVG.createSVGPoint();
  p.x = ev.clientX; p.y = ev.clientY;
  return p.matrixTransform(SVG.getScreenCTM().inverse());
}
function worldPt(ev) {
  const p = viewPt(ev);
  return { x: (p.x - cam.x) / cam.k, y: (p.y - cam.y) / cam.k };
}
function zoomAt(vp, factor) {
  const k2 = Math.min(4, Math.max(0.12, cam.k * factor));
  const wx = (vp.x - cam.x) / cam.k, wy = (vp.y - cam.y) / cam.k;
  cam.k = k2;
  cam.x = vp.x - wx * cam.k;
  cam.y = vp.y - wy * cam.k;
  applyCam();
}
function fitCam(pos, nodes) {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  nodes.forEach((nd) => {
    const p = pos[nd.id];
    if (!p) return;
    minx = Math.min(minx, p.x - p.pw / 2 - 40);
    miny = Math.min(miny, p.y - p.ph / 2 - 40);
    maxx = Math.max(maxx, p.x + p.pw / 2 + 40);
    maxy = Math.max(maxy, p.y + p.ph / 2 + 40);
  });
  if (!isFinite(minx)) { cam.k = 0.7; cam.x = VW / 2; cam.y = VH / 2; applyCam(); return; }
  const w = maxx - minx, h = maxy - miny;
  cam.k = Math.min(1.5, Math.max(0.24, Math.min(VW / (w + 120), VH / (h + 120))));
  cam.x = VW / 2 - cam.k * ((minx + maxx) / 2);
  cam.y = VH / 2 - cam.k * ((miny + maxy) / 2);
  applyCam();
}

/* ---------- 画布 ---------- */
function ensureStage() {
  if (SVG.dataset.canvas === "1") return;
  SVG.dataset.canvas = "1";
  SVG.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
  SVG.innerHTML = "";
  const defs = el("defs");
  Object.entries(REL_COLOR).forEach(([rel, col]) => {
    const m = el("marker", {
      id: "arr-" + rel, viewBox: "0 0 10 10", refX: 9, refY: 5,
      markerWidth: 10, markerHeight: 10, orient: "auto-start-reverse",
      markerUnits: "userSpaceOnUse",
    });
    m.appendChild(el("path", { d: "M 0 1.4 L 10 5 L 0 8.6 Z", fill: col }));
    defs.appendChild(m);
  });
  SVG.appendChild(defs);
  const g = el("g", { id: "graph-cam" });
  g.appendChild(el("rect", { x: -20000, y: -20000, width: 50000, height: 50000, fill: "transparent", id: "graph-bg" }));
  g.appendChild(el("g", { id: "graph-world" }));
  SVG.appendChild(g);
  bindGraphUi();
}

function bindGraphUi() {
  SVG.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    zoomAt(viewPt(ev), ev.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });

  SVG.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    const hit = ev.target.closest("[data-nid]");
    drag = { mode: hit ? "node" : "pan", sx: ev.clientX, sy: ev.clientY, cx: cam.x, cy: cam.y, moved: false };
    SVG.setPointerCapture(ev.pointerId);
  });
  SVG.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    if (drag.mode === "pan") {
      cam.x = drag.cx + dx;
      cam.y = drag.cy + dy;
      applyCam();
    }
  });
  SVG.addEventListener("pointerup", (ev) => {
    if (!drag) return;
    const moved = drag.moved;
    const hit = ev.target.closest("[data-nid]");
    drag = null;
    if (!moved && hit) {
      const id = hit.getAttribute("data-nid");
      if (cur && id === cur.node.id) return;
      openNode(id);
    }
  });
  SVG.addEventListener("pointercancel", () => { drag = null; });

  const zin = document.getElementById("g-zoom-in");
  const zout = document.getElementById("g-zoom-out");
  const fit = document.getElementById("g-fit");
  if (zin && zin.dataset.ui !== "1") {
    zin.dataset.ui = "1";
    zin.onclick = () => zoomAt({ x: VW / 2, y: VH / 2 }, 1.25);
    zout.onclick = () => zoomAt({ x: VW / 2, y: VH / 2 }, 1 / 1.25);
    fit.onclick = () => { layoutCache.clear(); render(); };
  }
}

/* ---------- 请求 ---------- */
async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(path + " " + r.status);
  return r.json();
}

async function openNode(id) {
  const p = await api(`/api/node?id=${encodeURIComponent(id)}`);
  cur = p;
  selectedNeighbor = null;
  nodeIndex[p.node.id] = p.node;
  for (const e of p.edges) {
    if (!nodeIndex[e.other]) nodeIndex[e.other] = { id: e.other, name: e.other_name, type: e.other_type };
  }
  render();
  renderRank();
  renderDetail();
  renderRels();
}

/* ---------- 渲染 ---------- */
function visibleEdges() {
  if (!cur) return [];
  return cur.edges.filter((e) =>
    (!filterType || e.other_type === filterType) && (!filterRel || e.rel === filterRel));
}

function render() {
  const wrap = document.getElementById("graph-world");
  const cap = document.getElementById("graph-caption");
  if (!wrap) return;
  if (!cur) {
    wrap.innerHTML = "";
    if (cap) cap.textContent = "在右上搜索或从右侧事件进入";
    return;
  }
  wrap.innerHTML = "";
  const edges = visibleEdges();
  const center = cur.node;
  const nodes = [center];
  const seen = new Set([center.id]);
  for (const e of edges) {
    if (seen.has(e.other)) continue;
    seen.add(e.other);
    nodes.push(nodeIndex[e.other] || { id: e.other, name: e.other_name, type: e.other_type });
  }
  const pos = positionsFor(nodes, edges);
  if (cap) cap.textContent = `${clip(center.name, 18)} · ${center.type} · 度 ${center.degree} · 显示 ${edges.length} 条`;

  // 关系曲线：同一对节点之间只画一条，多种关系合成一个标签
  const byOther = new Map();
  edges.forEach((e) => {
    const slot = byOther.get(e.other) || { other: e.other, rels: [], count: 0, sample: e };
    if (!slot.rels.includes(e.rel)) slot.rels.push(e.rel);
    slot.count += 1;
    byOther.set(e.other, slot);
  });

  byOther.forEach((slot) => {
    const a = pos[center.id], b = pos[slot.other];
    if (!a || !b) return;
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 1;
    const nx = -dy / d, ny = dx / d;
    const mx = (a.x + b.x) / 2 + nx * 26;
    const my = (a.y + b.y) / 2 + ny * 26;
    const p1 = edgePoint(a, mx, my);
    const p2 = edgePoint(b, mx, my);
    const col = REL_COLOR[slot.rels[0]] || "#8b97a8";
    const hot = selectedNeighbor === slot.other;
    const path = el("path", {
      d: `M ${p1.x} ${p1.y} Q ${mx} ${my} ${p2.x} ${p2.y}`,
      fill: "none", stroke: col,
      "stroke-width": hot ? 3.2 : 2,
      "marker-end": `url(#arr-${slot.rels[0]})`,
      opacity: selectedNeighbor && !hot ? 0.15 : 0.72,
      "data-nid": slot.other,
    });
    path.style.cursor = "pointer";
    const relText = slot.rels.map((r) => REL_ZH[r] || r).join(" · ");
    const tip = `<b>${esc(center.name)}</b> — ${esc(relText)} — <b>${esc(slot.sample.other_name)}</b>` +
      (slot.count > 1 ? `<br>共 ${slot.count} 条主张` : "") +
      (slot.sample.title ? "<br>" + esc(clip(slot.sample.title, 30)) : "") +
      (slot.sample.time ? "<br>见报 " + esc(slot.sample.time) : "");
    path.addEventListener("mousemove", (ev) => showTip(ev, tip));
    path.addEventListener("mouseleave", hideTip);
    path.addEventListener("click", (ev) => {
      ev.stopPropagation();
      selectedNeighbor = slot.other;
      render(); renderRels();
    });
    wrap.appendChild(path);

    // 边上的关系标签：细边小字，不用实心色块，避免被看成节点
    const gLab = el("g", { class: "elabel", "pointer-events": "none" });
    const textW = relText.length * 12 + (slot.count > 1 ? 22 : 0);
    gLab.appendChild(el("rect", {
      x: mx - textW / 2, y: my - 9, width: textW, height: 18, rx: 9,
      fill: "#0c1016", stroke: col, "stroke-width": 1, opacity: 0.92,
    }));
    const txt = el("text", {
      x: mx, y: my + 4, "text-anchor": "middle", "font-size": 10.5, fill: col,
    });
    txt.textContent = relText + (slot.count > 1 ? ` ×${slot.count}` : "");
    gLab.appendChild(txt);
    wrap.appendChild(gLab);
  });

  // 节点
  nodes.forEach((nd) => {
    const p = pos[nd.id];
    if (!p) return;
    const isCenter = nd.id === center.id;
    const col = TYPE_COLOR[nd.type] || "#8b97a8";
    const g = el("g");
    g.style.cursor = "pointer";
    appendShape(g, nd.type, p, isCenter ? "rgba(255,255,255,0.06)" : "rgba(12,16,22,0.72)", col, isCenter ? 2.4 : 1.6);
    const t = el("text", {
      x: p.x, y: p.y + 5, "text-anchor": "middle",
      "font-size": isCenter ? 15 : 13.5,
      fill: isCenter ? "#fff" : "#cdd8e6",
      "font-weight": isCenter ? 600 : 400,
      "pointer-events": "none",
    });
    t.textContent = p.label;
    g.appendChild(t);
    g.addEventListener("mousemove", (ev) => showTip(ev,
      `<b>${esc(nodeIndex[nd.id] ? nodeIndex[nd.id].name : nd.name)}</b><br>` +
      `<span style="opacity:.7">${esc(nd.type)}${nodeIndex[nd.id] && nodeIndex[nd.id].degree ? " · " + nodeIndex[nd.id].degree + " 条关系" : ""}</span>`));
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (!isCenter) openNode(nd.id);
    });
    wrap.appendChild(g);
  });

  if (!SVG.dataset.fitted || SVG.dataset.fitted !== cur.node.id) {
    fitCam(pos, nodes);
    SVG.dataset.fitted = cur.node.id;
  }
}

/* ---------- 侧栏 ---------- */
function renderRank() {
  const box = document.getElementById("rank");
  if (!cur) { box.innerHTML = ""; return; }
  const counts = {};
  for (const e of cur.edges) counts[e.other_type] = (counts[e.other_type] || 0) + 1;
  box.innerHTML = ZH_ORDER
    .filter((t) => counts[t])
    .map((t) => `<button class="rank-row${filterType === t ? " on" : ""}" data-t="${t}">
      <span class="dot" style="background:${TYPE_COLOR[t] || "#888"}"></span>
      <span class="nm">${t}</span><span class="cnt">${counts[t]}</span></button>`)
    .join("");
  box.querySelectorAll(".rank-row").forEach((b) => {
    b.addEventListener("click", () => {
      filterType = filterType === b.dataset.t ? "" : b.dataset.t;
      layoutCache.clear();
      render(); renderRank(); renderRels();
    });
  });
}

function renderDetail() {
  const box = document.getElementById("detail");
  if (!cur) {
    box.innerHTML = '<p class="empty-detail">点图上的节点，或使用右上搜索。</p>';
    return;
  }
  const n = cur.node;
  const byRel = {};
  for (const e of cur.edges) (byRel[e.rel] = byRel[e.rel] || []).push(e);
  box.innerHTML = `
    <p class="detail-name">${esc(n.name)}</p>
    <p class="detail-meta">${esc(n.type)}${n.subtype ? " · " + esc(n.subtype) : ""} · 关联 ${n.degree} 条 · 涉及 ${n.doc_count} 篇</p>
    ${n.aliases && n.aliases.length ? `<p class="detail-meta">别名 ${esc(n.aliases.slice(0, 8).join("、"))}</p>` : ""}
    <div class="rel-minis">
      ${Object.entries(byRel).sort((a, b) => b[1].length - a[1].length)
        .map(([k, v]) => `<button type="button" class="rel-mini" data-r="${esc(k)}">
          <span class="rel-mini-k">${esc(REL_ZH[k] || k)} ${v.length}</span></button>`).join("")}
    </div>`;
  box.querySelectorAll(".rel-mini").forEach((b) => {
    b.addEventListener("click", () => {
      filterRel = filterRel === b.dataset.r ? "" : b.dataset.r;
      layoutCache.clear();
      render(); renderRels();
    });
  });
}

function renderRels() {
  const box = document.getElementById("rel-list");
  if (!cur) { box.innerHTML = ""; return; }
  const rows = visibleEdges()
    .sort((a, b) => (b.sources || []).length - (a.sources || []).length || (b.evidence || "").length - (a.evidence || "").length)
    .slice(0, 60);
  const total = cur.edges.length;
  const head = `<div class="list-title">关联 ${rows.length} 条${rows.length < total ? `（共 ${total}）` : ""}</div>`;
  box.innerHTML = head + rows.map((e) => {
    const arrow = e.direction === "out" ? "→" : "←";
    return `<div class="edge-row" data-id="${esc(e.other)}" title="点击切到该节点">
      <div class="head">
        <span class="rel" style="border-left:3px solid ${REL_COLOR[e.rel] || "#888"}">${esc(REL_ZH[e.rel] || e.rel)}${arrow}</span>
        <span class="other">${esc(e.other_name)}</span>
      </div>
      <div class="ev">${esc(brief(e.evidence, 76))}</div>
      <span class="more">${esc(e.time)}${e.title ? " · " + esc(clip(e.title, 24)) : ""}</span>
    </div>`;
  }).join("");
  box.querySelectorAll(".edge-row").forEach((row) => {
    row.addEventListener("click", () => openNode(row.dataset.id));
  });
}

/* ---------- 搜索与事件 ---------- */
function bindSearch() {
  const q = document.getElementById("q");
  let t = 0;
  q.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const v = q.value.trim();
      const box = document.getElementById("detail");
      if (!v) { renderDetail(); return; }
      const p = await api(`/api/search?q=${encodeURIComponent(v)}`);
      box.innerHTML = `<p class="detail-meta">搜索「${esc(v)}」 ${p.total} 个</p><div class="rel-minis">` +
        p.items.map((it) => `<button class="rel-mini" data-go="${esc(it.id)}">
          <span class="rel-mini-k">${esc(clip(it.name, 14))}</span>
          <span class="rel-mini-doc">${esc(it.type)} · ${it.degree}</span></button>`).join("") +
        `</div>`;
      box.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => openNode(b.dataset.go)));
    }, 220);
  });
}

async function bindEvents() {
  const p = await api("/api/events?limit=30");
  const box = document.getElementById("detail");
  box.innerHTML = `<p class="detail-meta">热点事件 ${p.total} 个，按关联数排序</p><div class="rel-minis">` +
    p.items.map((it) => `<button class="rel-mini" data-ev="${esc(it.id)}">
      <span class="rel-mini-k">${esc(clip(it.name, 16))}</span>
      <span class="rel-mini-doc">${esc(it.kind || "事件")} · ${it.degree}</span></button>`).join("") +
    `</div>`;
  box.querySelectorAll("[data-ev]").forEach((b) => b.addEventListener("click", () => openNode(b.dataset.ev)));
}

/* ---------- 启动 ---------- */
(async function boot() {
  try {
    const s = await api("/api/summary");
    document.getElementById("lede").textContent =
      `${s.years} · ${s.summary.n_docs} 篇 · ${s.summary.n_nodes} 节点 · ${s.summary.n_edges} 边`;
  } catch (e) {
    document.getElementById("lede").textContent = "接口未启动：python3 -m graph.serve10 --port 8765";
  }
  ensureStage();
  bindSearch();
  await bindEvents();
  render();
  // 默认打开关联最多的事件，页面一进来就有图
  try {
    const p = await api("/api/events?limit=1");
    if (p.items && p.items.length) {
      await openNode(p.items[0].id);
    }
  } catch (e) {
    console.error("默认事件加载失败", e);
  }
})();
