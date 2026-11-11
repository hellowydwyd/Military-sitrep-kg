/* 三任务标注前端：左侧原文高亮，右侧逐条判定 */

const $ = (id) => document.getElementById(id);
const TIP = $("tip");

let cur = null;          // 当前稿件
let verdicts = [];       // 逐条判定：{idx, action, type?, event_type?, setting?, kind?, role?, relation?, stance?}
let added = [];          // 手工补标：{kind, text, offset:[a,b], type, ...}
let activeIdx = -1;
let selecting = false;   // 框选模式开关

const TYPE_COLOR = {
  机构: "#5ec4a0", 人物: "#7eb4d4", 装备: "#b58fd0",
  地点: "#e2c17a", 作品: "#c4b07a",
};

const ACTION_ZH = { keep: "保留", modify: "修改", drop: "删除" };

/* ---------- 纯文本偏移 ---------- */

/** 取选区在纯文本中的字符区间，只认落在 #text 内的选区。 */
function offsetAt(node, offset) {
  if (node.nodeType === Node.TEXT_NODE) {
    const parent = node.parentElement;
    const base = Number(parent?.dataset.off || 0);
    return base + offset;
  }
  // 元素节点：按子树文本长度累加
  let acc = 0;
  for (const child of node.childNodes) {
    if (child === node.childNodes[offset]) break;
    acc += (child.textContent || "").length;
  }
  return (Number(node.dataset?.off || 0)) + acc;
}

function selectionRange() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  const root = $("text");
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const a = offsetAt(range.startContainer, range.startOffset);
  const b = offsetAt(range.endContainer, range.endOffset);
  const start = Math.min(a, b);
  const end = Math.max(a, b);
  if (end <= start) return null;
  const raw = (cur.text || "").slice(start, end);
  const text = raw.trim();
  if (!text) return null;
  // 修正首尾空白造成的偏移
  const lead = raw.length - raw.trimStart().length;
  return { start: start + lead, end: start + lead + text.length, text };
}

/* ---------- 请求 ---------- */

async function api(path, opts) {
  const r = await fetch(path, opts || { cache: "no-store" });
  if (!r.ok) throw new Error(path + " " + r.status);
  return r.json();
}

const params = (extra) => {
  const p = new URLSearchParams({
    task: state.task,
    split: state.split,
    annotator: ($("annotator").value || "A").toUpperCase(),
  });
  if (skipped.size) p.set("skip", [...skipped].join(","));
  for (const [k, v] of Object.entries(extra || {})) p.set(k, v);
  return p.toString();
};

const TASK_LABEL = { ner: "NER 实体识别", ee: "EE 事件抽取", re: "RE 关系抽取" };

/* 当前选择的状态，由分段按钮维护 */
const state = { task: "ner", split: "train" };
const skipped = new Set();

/* ---------- 载入 ---------- */

async function loadNext(extra) {
  const data = await api(`/api/next?${params(extra)}`);
  if (data.done) {
    cur = null;
    added = [];
    $("doctitle").textContent = skipped.size ? "本卷已全部看过" : "本卷已标完";
    const tip = skipped.size
      ? `已跳过 ${skipped.size} 篇，其余都标完了。`
      : "切换任务或卷继续，或换一个标注员编号复核。";
    $("text").innerHTML = `<div class="empty"><p>${esc(tip)}</p>${
      skipped.size ? '<button class="ghost" id="reset-skip">重新看被跳过的</button>' : ""
    }</div>`;
    const rb = $("reset-skip");
    if (rb) {
      rb.addEventListener("click", () => {
        skipped.clear();
        loadNext().catch(reportError);
      });
    }
    $("cands").innerHTML = '<div class="empty">没有待标稿件</div>';
    const dm = $("docmeta");
    if (dm) dm.innerHTML = "";
    updateRail(data);
    return;
  }
  cur = data;
  added = [];
  verdicts = (data.candidates || []).map(() => ({ action: "keep" }));
  activeIdx = -1;
  render();
  updateRail(data);
}

async function loadByDoc(docId) {
  const data = await api(`/api/doc?${params({ doc_id: docId })}`);
  cur = data;
  added = [];
  verdicts = (data.candidates || []).map(() => ({ action: "keep" }));
  activeIdx = -1;
  render();
}

/* ---------- 原文高亮 ---------- */

function renderText() {
  const text = cur.text || "";
  const marks = [];
  const seen = new Set();

  if (cur.task === "ner") {
    (cur.candidates || []).forEach((c, i) => {
      const s = c.text || "";
      if (s) marks.push({ s, kind: "entity", idx: i, type: c.type });
    });
  } else if (cur.task === "ee") {
    (cur.candidates || []).forEach((c, i) => {
      const s = c.trigger || "";
      if (s) marks.push({ s, kind: "event", idx: i, type: c.event_type });
      for (const a of c.arguments || []) {
        if (a.text) marks.push({ s: a.text, kind: "entity", idx: i, type: a.role });
      }
    });
  } else {
    (cur.candidates || []).forEach((c, i) => {
      if (c.head) marks.push({ s: c.head, kind: "entity", idx: i, type: c.relation });
      if (c.tail) marks.push({ s: c.tail, kind: "entity", idx: i, type: c.relation });
    });
  }

  // 手工新增的条目，按记录的偏移高亮，避免同形异位冲突
  const addSpans = added.map((a, k) => ({
    start: a.offset[0],
    end: a.offset[1],
    kind: a.kind === "event" ? "event" : "entity",
    idx: 1000 + k,
    type: a.type || a.event_type || "",
    manual: true,
  }));

  const spans = [...addSpans];
  for (const m of marks) {
    const key = m.s + "|" + m.kind;
    if (seen.has(key)) continue;
    seen.add(key);
    const pos = text.indexOf(m.s);
    if (pos < 0) continue;
    spans.push({ start: pos, end: pos + m.s.length, ...m });
  }
  spans.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));

  let html = "";
  let cursor = 0;
  const taken = [];
  for (const sp of spans) {
    if (sp.start < cursor) continue;
    if (taken.some((t) => sp.start < t.end && sp.end > t.start)) continue;
    html += `<span data-off="${cursor}">${esc(text.slice(cursor, sp.start))}</span>`;
    const v = sp.idx >= 1000 ? null : verdicts[sp.idx];
    const act = (v && v.action) || "keep";
    const cls = [sp.manual ? "manual" : "", act === "drop" ? "dropped" : ""].filter(Boolean).join(" ");
    const tc = TYPE_COLOR[sp.type];
    const style = tc ? ` style="--tc:${tc}"` : "";
    html += `<mark class="${cls}"${style} data-action="${act}" data-kind="${sp.kind}" data-idx="${sp.idx}" data-type="${esc(sp.type || "")}" data-off="${sp.start}">${esc(text.slice(sp.start, sp.end))}</mark>`;
    cursor = sp.end;
    taken.push(sp);
  }
  html += `<span data-off="${cursor}">${esc(text.slice(cursor))}</span>`;
  const pane = $("text");
  const keepScroll = pane.scrollTop;
  pane.innerHTML = html || '<div class="empty">（本篇无正文）</div>';
  pane.scrollTop = keepScroll;

  $("text").querySelectorAll("mark").forEach((el) => {
    el.addEventListener("click", (ev) => {
      if (selecting) return; // 框选模式下不响应点击
      ev.stopPropagation();
      activeIdx = Number(el.dataset.idx);
      highlightCand(activeIdx);
    });
    el.addEventListener("mousemove", (ev) => {
      TIP.innerHTML = `<b>${esc(el.textContent)}</b><br><span style="opacity:.7">${esc(el.dataset.type || "")}${el.dataset.idx >= 1000 ? " · 手工" : ""}</span>`;
      TIP.classList.add("show");
      TIP.style.left = Math.min(ev.clientX + 12, window.innerWidth - 320) + "px";
      TIP.style.top = Math.min(ev.clientY + 12, window.innerHeight - 80) + "px";
    });
    el.addEventListener("mouseleave", () => TIP.classList.remove("show"));
  });
}

function highlightCand(idx) {
  $("text").querySelectorAll("mark").forEach((el) => {
    el.classList.toggle("active", Number(el.dataset.idx) === idx);
  });
  document.querySelectorAll(".cand").forEach((el) => {
    const on = Number(el.dataset.idx) === idx;
    el.style.boxShadow = on ? "0 0 0 1.5px rgba(91,147,214,0.8)" : "";
    if (on) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

/* ---------- 判定面板 ---------- */

function renderSchema() {
  const s = cur.schema || {};
  let brief = "";
  if (cur.task === "ner") {
    brief = `五类：${(s.types || []).join(" · ")}`;
  } else if (cur.task === "ee") {
    brief = `${(s.event_types || []).length} 类事件 · ${(s.kinds || []).join("/")}`;
  } else {
    brief = `${(s.relations || []).length} 种结构关系`;
  }
  $("schema-brief").textContent = brief;
  $("subtitle").textContent = TASK_LABEL[cur.task] || "";
}

function renderCands() {
  const box = $("cands");
  const cands = cur.candidates || [];
  const cards = cands.map((c, i) => candCard(c, i)).join("");
  const adds = added.map((a, k) => addedCard(a, k)).join("");
  const head = added.length ? `<div class="sec-title">手工补标 · ${added.length}</div>` : "";
  const tip = cands.length
    ? ""
    : '<div class="empty">本篇无候选<br>用「框选补标」从原文中拖选新增</div>';
  box.innerHTML = (cards || tip) + head + adds;
  bindCandEvents();
  updateCounts();
}

function updateCounts() {
  const c = { keep: 0, modify: 0, drop: 0 };
  for (const v of verdicts) c[v.action || "keep"] = (c[v.action || "keep"] || 0) + 1;
  $("cnt-keep").textContent = c.keep || 0;
  $("cnt-modify").textContent = c.modify || 0;
  $("cnt-drop").textContent = c.drop || 0;
  $("cnt-add").textContent = added.length;
}

function updateRail(d) {
  const total = d.total || 0;
  const done = d.done_count || 0;
  const remain = d.remaining === undefined ? Math.max(total - done, 0) : d.remaining;
  $("remain").textContent = remain;
  $("total-docs").textContent = total;
  $("done-docs").textContent = done;
  const pct = total ? Math.round((done / total) * 100) : 0;
  $("bar").style.width = pct + "%";
  $("bar-note").textContent = `${done} / ${total} · ${pct}%`;
}

function addedCard(a, k) {
  const fields = [];
  if (cur.task === "ner") {
    fields.push(rowSelect("type", "类型", ["机构", "人物", "装备", "地点", "作品"], a.type, 1000 + k, "add"));
  } else if (cur.task === "ee") {
    fields.push(rowSelect("event_type", "类型", cur.schema.event_types || [], a.event_type, 1000 + k, "add"));
    fields.push(rowSelect("setting", "场景", cur.schema.settings || [], a.setting, 1000 + k, "add"));
    fields.push(rowSelect("kind", "话语", cur.schema.kinds || [], a.kind, 1000 + k, "add"));
  } else {
    fields.push(rowInput("head", "头实体", a.head, 1000 + k));
    fields.push(rowSelect("relation", "关系", cur.schema.relations || [], a.relation, 1000 + k, "add"));
    fields.push(rowInput("tail", "尾实体", a.tail, 1000 + k));
  }
  return `<div class="cand manual" data-add="${k}">
    <div class="cand-head">
      <span class="cand-idx">+</span>
      <span class="cand-main">${esc(a.text || "")}</span>
    </div>
    <div class="cand-meta">偏移 ${a.offset[0]}–${a.offset[1]}</div>
    ${fields.join("")}
    <div class="row"><span class="lbl">操作</span>
      <button class="del-add" data-k="${k}">删除本条</button></div>
  </div>`;
}

function rowInput(field, label, value, idx) {
  return `<div class="row"><span class="lbl">${label}</span>
    <input class="mini" data-field="${field}" data-add-input="${idx}" value="${esc(value || "")}" /></div>`;
}

function candCard(c, i) {
  const v = verdicts[i] || { action: "keep" };
  const cls = v.action === "drop" ? "drop" : v.action === "modify" ? "modify" : "keep";
  if (cur.task === "ner") {
    const t = v.type || c.type;
    const col = TYPE_COLOR[t] || "#7c8a9c";
    return `<div class="cand ${cls}" data-idx="${i}">
      <div class="cand-head">
        <span class="cand-idx">${i + 1}</span>
        <span class="cand-main">${esc(c.text || "")}</span>
        ${t ? `<span class="chip" style="--c:${col}">${esc(t)}</span>` : ""}
      </div>
      ${rowSelect("type", "类型", ["机构", "人物", "装备", "地点", "作品"], t, i)}
      ${rowAction(i)}
    </div>`;
  }
  if (cur.task === "ee") {
    const args = (c.arguments || []).map((a) => `${a.role}·${a.text}`).join("　");
    const st = c.stance ? `　立场·${c.stance}` : "";
    const t = v.event_type || c.event_type;
    return `<div class="cand ${cls}" data-idx="${i}">
      <div class="cand-head">
        <span class="cand-idx">${i + 1}</span>
        <span class="cand-main">${esc(c.trigger || "")}</span>
        ${t ? `<span class="chip" style="--c:#cf6b76">${esc(t)}</span>` : ""}
      </div>
      <div class="cand-meta">${esc(args || "无论元")}${esc(st)}</div>
      ${rowSelect("event_type", "类型", cur.schema.event_types || [], t, i)}
      ${rowSelect("setting", "场景", cur.schema.settings || [], v.setting || c.setting, i)}
      ${rowSelect("kind", "话语", cur.schema.kinds || [], v.kind || c.kind, i)}
      ${rowAction(i)}
    </div>`;
  }
  return `<div class="cand ${cls}" data-idx="${i}">
    <div class="cand-head">
      <span class="cand-idx">${i + 1}</span>
      <span class="cand-main">${esc(c.head || "")} <span class="rel-arrow">—${esc(c.relation || "")}→</span> ${esc(c.tail || "")}</span>
      <span class="chip" style="--c:#d9b978">${esc(c.relation || "")}</span>
    </div>
    ${c.evidence ? `<div class="cand-meta">${esc(clip(c.evidence, 90))}</div>` : ""}
    ${rowSelect("relation", "关系", cur.schema.relations || [], v.relation || c.relation, i)}
    ${rowAction(i)}
  </div>`;
}

function rowSelect(field, label, options, value, idx, mode) {
  const opts = options
    .map((o) => `<option value="${esc(o)}"${o === value ? " selected" : ""}>${esc(o)}</option>`)
    .join("");
  const attr = mode === "add" ? `data-add-field="${field}"` : `data-field="${field}"`;
  return `<div class="row"><span class="lbl">${label}</span>
    <select class="mini" ${attr} data-idx="${idx}">${opts}</select></div>`;
}

function rowAction(idx) {
  const cur0 = (verdicts[idx] || {}).action || "keep";
  return `<div class="row">
    <span class="lbl">判定</span>
    <div class="act-group">
    ${["keep", "modify", "drop"]
      .map((a) => {
        const on = cur0 === a ? ` on-${a}` : "";
        return `<button class="act${on}" data-act="${a}" data-idx="${idx}">${ACTION_ZH[a]}</button>`;
      })
      .join("")}
    </div></div>`;
}

function bindCandEvents() {
  document.querySelectorAll(".cand").forEach((el) => {
    if (el.dataset.add !== undefined) return;
    el.addEventListener("click", (ev) => {
      if (ev.target.closest("button") || ev.target.closest("select") || ev.target.closest("input")) return;
      activeIdx = Number(el.dataset.idx);
      highlightCand(activeIdx);
    });
  });
  document.querySelectorAll(".act").forEach((b) => {
    b.addEventListener("click", () => {
      const i = Number(b.dataset.idx);
      verdicts[i] = { ...(verdicts[i] || {}), action: b.dataset.act };
      renderCands();
      renderText();
    });
  });
  document.querySelectorAll("select[data-field]").forEach((s) => {
    s.addEventListener("change", () => {
      const i = Number(s.dataset.idx);
      verdicts[i] = { ...(verdicts[i] || { action: "modify" }), [s.dataset.field]: s.value };
      if (verdicts[i].action === "keep") verdicts[i].action = "modify";
      renderCands();
    });
  });
  // 手工条目的字段编辑
  document.querySelectorAll("select[data-add-field]").forEach((s) => {
    s.addEventListener("change", () => {
      const k = Number(s.dataset.idx) - 1000;
      if (added[k]) added[k][s.dataset.addField] = s.value;
      renderText();
    });
  });
  document.querySelectorAll("input[data-add-input]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const k = Number(inp.dataset.addInput) - 1000;
      if (added[k]) added[k][inp.dataset.field] = inp.value;
    });
  });
  document.querySelectorAll(".del-add").forEach((b) => {
    b.addEventListener("click", () => {
      added.splice(Number(b.dataset.k), 1);
      renderCands();
      renderText();
    });
  });
}

/* ---------- 提交 ---------- */

async function submit() {
  if (!cur) return;
  const items = (cur.candidates || []).map((c, i) => {
    const v = verdicts[i] || { action: "keep" };
    const base = { idx: i, action: v.action, source: pickSource(c) };
    for (const k of ["type", "event_type", "setting", "kind", "relation", "stance"]) {
      if (v[k] !== undefined) base[k] = v[k];
    }
    return base;
  });
  // 手工补充的条目以 add 动作提交，带偏移
  for (const a of added) {
    const item = {
      action: "add",
      offset: a.offset,
      text: a.text,
      kind: a.kind || "entity",
    };
    for (const k of ["type", "event_type", "setting", "kind", "role", "relation", "head", "tail"]) {
      if (a[k] !== undefined && a[k] !== "") item[k] = a[k];
    }
    items.push(item);
  }
  const payload = {
    task: cur.task,
    doc_id: cur.doc_id,
    annotator: ($("annotator").value || "A").toUpperCase(),
    items,
    notes: $("notes").value || "",
  };
  const r = await api("/api/judge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (r.ok) {
    $("notes").value = "";
    await loadNext();
  } else {
    alert("提交失败：" + (r.error || "未知"));
  }
}

function pickSource(c) {
  if (cur.task === "ner") return { text: c.text, type: c.type };
  if (cur.task === "ee") return { trigger: c.trigger, arguments: c.arguments };
  return { head: c.head, relation: c.relation, tail: c.tail };
}

/* ---------- 工具 ---------- */

function esc(s) {
  return String(s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function clip(s, n) {
  const t = String(s || "");
  return t.length > n ? t.slice(0, n) + "…" : t;
}

function render() {
  $("doctitle").textContent = cur.title || cur.doc_id || "";
  const meta = [];
  const n = (cur.candidates || []).length;
  const chars = (cur.text || "").replace(/\s/g, "").length;
  meta.push(`<span>${TASK_LABEL[cur.task] || cur.task}</span>`);
  meta.push(`<span>${chars} 字</span>`);
  meta.push(`<span>预标 ${n} 条</span>`);
  if (cur.doc_id) meta.push(`<span>${String(cur.doc_id).slice(0, 8)}</span>`);
  const dm = $("docmeta");
  if (dm) dm.innerHTML = meta.join("");
  renderSchema();
  renderText();
  renderCands();
}

/* ---------- 框选新增 ---------- */

function setHint(msg) {
  const el = $("hint");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("show", !!msg);
  clearTimeout(setHint._t);
  if (msg) setHint._t = setTimeout(() => el.classList.remove("show"), 3200);
}

function addFromSelection() {
  const r = selectionRange();
  if (!r) {
    setHint("先在原文里拖选一段文字");
    return false;
  }
  const item = { kind: cur.task === "ee" ? "event" : "entity", text: r.text, offset: [r.start, r.end] };
  if (cur.task === "ner") item.type = "机构";
  if (cur.task === "ee") {
    item.event_type = "其他";
    item.setting = "其他";
    item.kind = "发生";
  }
  if (cur.task === "re") {
    item.head = r.text;
    item.relation = (cur.schema.relations || ["隶属"])[0];
    item.tail = "";
  }
  added.push(item);
  setHint(`已补标：${r.text.slice(0, 24)}`);
  window.getSelection()?.removeAllRanges();
  renderCands();
  renderText();
  return true;
}

function toggleSelecting() {
  selecting = !selecting;
  const btn = $("select-mode");
  btn.classList.toggle("primary", selecting);
  btn.textContent = selecting ? "框选中…" : "框选补标";
  $("text").classList.toggle("selecting", selecting);
  setHint(selecting ? "在原文中拖选要补标的片段" : "");
}

/* ---------- 启动 ---------- */

const on = (id, ev, fn) => {
  const el = $(id);
  if (el) el.addEventListener(ev, fn);
};

on("submit", "click", () => submit());
on("skip", "click", () => {
  if (cur) skipped.add(cur.doc_id);
  loadNext().catch(reportError);
});
on("select-mode", "click", toggleSelecting);
on("clear", "click", () => {
  if (!cur) return;
  verdicts = (cur.candidates || []).map(() => ({ action: "keep" }));
  added = [];
  renderCands();
  renderText();
});

/* 任务切换 */
for (const b of document.querySelectorAll("#task-seg .seg-btn")) {
  b.addEventListener("click", () => {
    if (state.task === b.dataset.task) return;
    state.task = b.dataset.task;
    skipped.clear();
    for (const x of document.querySelectorAll("#task-seg .seg-btn")) x.classList.toggle("on", x === b);
    loadNext().catch(reportError);
  });
}

/* 卷切换 */
for (const b of document.querySelectorAll("#split-seg .pill")) {
  b.addEventListener("click", () => {
    if (state.split === b.dataset.split) return;
    state.split = b.dataset.split;
    skipped.clear();
    for (const x of document.querySelectorAll("#split-seg .pill")) x.classList.toggle("on", x === b);
    loadNext().catch(reportError);
  });
}

on("annotator", "change", () => loadNext().catch(reportError));

/* 键盘：1/2/3 判定当前卡片，Enter 提交，Esc 退出框选 */
document.addEventListener("keydown", (ev) => {
  const tag = (ev.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (ev.key === "Escape") {
    if (selecting) toggleSelecting();
    return;
  }
  if (ev.key === "Enter") {
    ev.preventDefault();
    submit();
    return;
  }
  if (ev.key === "/") {
    ev.preventDefault();
    toggleSelecting();
    return;
  }
  const map = { "1": "keep", "2": "modify", "3": "drop" };
  if (map[ev.key] && activeIdx >= 0 && verdicts[activeIdx] !== undefined) {
    ev.preventDefault();
    verdicts[activeIdx] = { ...(verdicts[activeIdx] || {}), action: map[ev.key] };
    renderCands();
    renderText();
    return;
  }
  if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
    const n = (cur && cur.candidates ? cur.candidates.length : 0) + added.length;
    if (!n) return;
    ev.preventDefault();
    const step = ev.key === "ArrowRight" ? 1 : -1;
    activeIdx = activeIdx < 0 ? (step > 0 ? 0 : n - 1) : (activeIdx + step + n) % n;
    highlightCand(activeIdx);
  }
});

// 框选模式下用 mouseup 捕获，避免与点击高亮冲突
document.addEventListener("mouseup", () => {
  if (!selecting || !cur) return;
  if (!selectionRange()) return;
  addFromSelection();
});

function reportError(e) {
  $("text").innerHTML = `<div class="empty">加载失败：${esc(e.message)}<br>请确认服务已启动：python3 -m annotate.task_server --port 8767</div>`;
}

loadNext().catch(reportError);
