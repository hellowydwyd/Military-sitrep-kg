/* 三任务标注：界面渲染与交互核心。
 *
 * 本文件只管「怎么画」和「怎么改」，不碰数据来源。
 * 数据由控制器实现：
 *   app-server.js  读 /api/*，标注记录落在 data/annotations/
 *   app-local.js   读用户选中的本地 jsonl，记录存 localStorage，可导出
 *
 * 控制器必须实现这几个全局函数：
 *   loadNext()        取下一篇；内部应更新 cur/verdicts/added 后调 render()
 *   submit()          提交当前篇
 *   reportError(e)    展示错误
 */

const $ = (id) => document.getElementById(id);

let cur = null;          // 当前稿件
let verdicts = [];       // 逐条判定
let added = [];          // 手工补标
let activeIdx = -1;
let selecting = false;   // 框选模式开关
let skipped = new Set(); // 本次会话跳过的 doc_id

const state = { task: "ner", split: "train", file: "" };

const TYPE_COLOR = {
  机构: "#5ec4a0", 人物: "#7eb4d4", 装备: "#b58fd0",
  地点: "#e2c17a", 作品: "#c4b07a",
};

/* 装备分两类：带型号或专名的，与纯通称的。
 * 型号类在卡片上加「型号」标记，提醒标注员核对写法是否来自原文。 */
function isModelEquip(name) {
  const s = String(name || "");
  return /[A-Z]{1,4}[- ]?\d/.test(s) || /[\u4e00-\u9fff]{2,6}号/.test(s)
    || /舰$|艇$/.test(s);
}


const ACTION_ZH = { keep: "保留", modify: "修改", drop: "删除" };
const TASK_LABEL = { ner: "NER 实体识别", ee: "EE 事件抽取", re: "RE 关系抽取" };

/* ---------- 纯文本偏移 ---------- */

function offsetAt(node, offset) {
  if (node.nodeType === Node.TEXT_NODE) {
    const parent = node.parentElement;
    const base = Number(parent?.dataset.off || 0);
    return base + offset;
  }
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
  const lead = raw.length - raw.trimStart().length;
  return { start: start + lead, end: start + lead + text.length, text };
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
      // 事件句整段高亮，触发词单独标注
      const s = c.text || "";
      if (s) marks.push({ s, kind: "event", idx: i, type: c.event_type });
      if (c.trigger && c.trigger !== s) {
        marks.push({ s: c.trigger, kind: "trigger", idx: i, type: c.event_type });
      }
      const ve = verdicts[i] || {};
      const alist = ve.arguments !== undefined ? ve.arguments : (c.arguments || []);
      for (const a of alist) {
        if (a.text) marks.push({ s: a.text, kind: "entity", idx: i, type: a.role });
      }
    });
  } else {
    (cur.candidates || []).forEach((c, i) => {
      if (c.head) marks.push({ s: c.head, kind: "entity", idx: i, type: c.relation });
      if (c.tail) marks.push({ s: c.tail, kind: "entity", idx: i, type: c.relation });
    });
  }

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

  pane.querySelectorAll("mark").forEach((el) => {
    el.addEventListener("click", (ev) => {
      if (selecting) return;
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

const TIP = $("tip");

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
    brief = `五类：${(s.types || []).join(" · ")} · 同名只列一次`;
  } else if (cur.task === "ee") {
    const sub = Object.values(s.subtypes || {}).reduce((a, v) => a + v.length, 0);
    brief = `${(s.event_types || []).length} 类性质 · ${sub} 类动作 · ${(s.kinds || []).join("/")}`;
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
    fields.push(rowSelect("type", "类型", ["国家", "机构", "人物", "装备", "地点", "作品"], a.type, 1000 + k, "add"));
  } else if (cur.task === "ee") {
    const at = a.event_type || "其他";
    const asubs = (cur.schema.subtypes || {})[at] || [];
    fields.push(rowSelect("event_type", "性质", cur.schema.event_types || [], at, 1000 + k, "add"));
    if (asubs.length) fields.push(rowSelect("subtype", "动作", ["", ...asubs], a.subtype, 1000 + k, "add"));
    fields.push(rowSelect("kind", "时序", cur.schema.kinds || [], a.kind, 1000 + k, "add"));
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
        ${t === "装备" && isModelEquip(c.text)
    ? '<span class="chip" style="--c:#c9a24a">型号</span>'
    : ""}
        ${t ? `<span class="chip" style="--c:${col}">${esc(t)}</span>` : ""}
      </div>
      ${rowSelect("type", "类型", ["国家", "机构", "人物", "装备", "地点", "作品"], t, i)}
      ${rowAction(i)}
    </div>`;
  }
  if (cur.task === "ee") {
    const t = v.event_type || c.event_type;
    const sub = v.subtype !== undefined ? v.subtype : (c.subtype || "");
    const subs = (cur.schema.subtypes || {})[t] || [];
    const slotMap = cur.schema.slots || {};
    // 每个类型一套封闭论元，没有通用层。取不到就退回空的，由 argEditor 用已有角色兜底。
    const uniq = (arr) => [...new Set(arr.filter(Boolean))];
    let slots = slotMap[t + "·" + sub] || [];
    if (!slots.length && t === "其他") {
      slots = uniq(cur.schema.other_slots || ["施动方", "对象"]);
    } else if (!slots.length && t) {
      // 二级未定：取该一级下全部槽位的并集
      const pool = [];
      for (const [k, vs] of Object.entries(slotMap)) {
        if (k.startsWith(t + "·")) pool.push(...vs);
      }
      slots = uniq(pool);
    }
    // 论元逐条渲染：角色可换、值可改、可删、可加
    const args = v.arguments !== undefined ? v.arguments : (c.arguments || []);
    const argRows = argEditor(i, args, slots, cur.text);
    return `<div class="cand ${cls}" data-idx="${i}">
      <div class="cand-head">
        <span class="cand-idx">${i + 1}</span>
        <span class="cand-main clamp" data-toggle="1">${highlightTrigger(c.text, c.trigger)}</span>
        ${t ? `<span class="chip" style="--c:#cf6b76">${esc(sub ? t + "·" + sub : t)}</span>` : ""}
      </div>
      <div class="cand-meta">${c.trigger && c.text && c.trigger !== c.text
        ? `触发词 <b class="trig">${esc(c.trigger)}</b>　`
        : ""}${timeLabel(c)}</div>
      ${argRows}
      ${rowSelect("event_type", "性质", cur.schema.event_types || [], t, i)}
      ${subs.length
        ? rowSelect("subtype", "动作", ["", ...subs], sub, i)
        : `<div class="row"><span class="lbl">动作</span><span class="cand-meta">该类无二级</span></div>`}
      ${rowSelect("kind", "时序", cur.schema.kinds || [], v.kind || c.kind, i)}
      ${t === "表态" || c.stance
        ? rowSelect("stance", "立场", ["", ...(cur.schema.stances || [])], v.stance !== undefined ? v.stance : (c.stance || ""), i)
        : ""}
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

/** 论元编辑器：每个槽位一行，角色可换、值可改、可删；末尾可加槽位。 */
function argEditor(idx, args, slots, evText) {
  const src = String(evText || "");
  const rows = args
    .map((a, k) => {
      // 当前角色的选项必须存在，否则浏览器会把 select 静默回落到第一项
      const opts = [...new Set([...(slots.length ? slots : []), a.role].filter(Boolean))]
        .map((s) => `<option value="${esc(s)}"${s === a.role ? " selected" : ""}>${esc(s)}</option>`)
        .join("");
      // 值不在正文里时给出提示，避免留下手误
      const miss = a.text && src && src.indexOf(a.text) < 0;
      return `<div class="arg-row${miss ? " arg-miss" : ""}">
        <select class="mini arg-role" data-arg-field="role" data-idx="${idx}" data-k="${k}">${opts}</select>
        <input class="arg-val" type="text" value="${esc(a.text || "")}" title="${esc(a.text || "")}"
          data-arg-field="text" data-idx="${idx}" data-k="${k}" placeholder="值">
        <span class="arg-warn"${miss ? "" : " hidden"} title="该值不在正文中">?</span>
        <button class="arg-del" type="button" data-arg-del="1" data-idx="${idx}" data-k="${k}" title="删除该槽位">×</button>
      </div>`;
    })
    .join("");
  const rest = slots.filter((s) => !args.some((a) => a.role === s));
  const adder = slots.length
    ? `<div class="arg-row">
        <select class="mini arg-role" data-arg-add="1" data-idx="${idx}">
          <option value="">+ 加槽位</option>
          ${rest.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("")}
        </select>
        ${rest.length ? "" : `<span class="arg-warn-note">槽位已用尽</span>`}
      </div>`
    : "";
  return `<div class="arg-box">
    <div class="arg-title">论元 ${args.length ? `<span class="arg-n">${args.length}</span>` : '<span class="arg-empty">（无，可加）</span>'}</div>
    ${rows}${adder}
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
      // 换一级类型时二级作废，避免留下不属于该一级的取值
      if (s.dataset.field === "event_type") {
        const allowed = (cur.schema.subtypes || {})[s.value] || [];
        if (!allowed.includes(verdicts[i].subtype)) verdicts[i].subtype = "";
      }
      renderCands();
    });
  });
  document.querySelectorAll("select[data-add-field]").forEach((s) => {
    s.addEventListener("change", () => {
      const k = Number(s.dataset.idx) - 1000;
      if (added[k]) {
        added[k][s.dataset.addField] = s.value;
        // 换一级类型时二级作废
        if (s.dataset.addField === "event_type") {
          const allowed = (cur.schema.subtypes || {})[s.value] || [];
          if (!allowed.includes(added[k].subtype)) added[k].subtype = "";
          renderCands();
          renderText();
          return;
        }
      }
      renderText();
    });
  });
  document.querySelectorAll("input[data-add-input]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const k = Number(inp.dataset.addInput) - 1000;
      if (added[k]) added[k][inp.dataset.field] = inp.value;
    });
  });
  // 论元编辑：写回 verdicts[i].arguments，不重渲染候选以保持焦点
  const setArg = (i, k, patch) => {
    const base = verdicts[i] || { action: "keep" };
    const args = (base.arguments !== undefined ? base.arguments : ((cur.candidates[i] || {}).arguments || []))
      .map((a, j) => (j === k ? { ...a, ...patch } : { ...a }));
    verdicts[i] = { ...base, arguments: args, action: base.action === "keep" ? "modify" : base.action };
  };
  document.querySelectorAll("select[data-arg-field=role]").forEach((s) => {
    s.addEventListener("change", () => {
      setArg(Number(s.dataset.idx), Number(s.dataset.k), { role: s.value });
      // 换角色后可加槽位列表会变，重绘候选
      renderCands();
      renderText();
    });
  });
  document.querySelectorAll("input[data-arg-field=text]").forEach((inp) => {
    inp.addEventListener("input", () => {
      setArg(Number(inp.dataset.idx), Number(inp.dataset.k), { text: inp.value });
      // 就地切换「不在正文」提示，不重绘以免失焦
      const row = inp.closest(".arg-row");
      const warn = row && row.querySelector(".arg-warn");
      if (warn) {
        const miss = !!inp.value && (cur.text || "").indexOf(inp.value) < 0;
        warn.hidden = !miss;
        row.classList.toggle("arg-miss", miss);
      }
    });
    // 失焦后重绘，让正文高亮跟上新值
    inp.addEventListener("change", () => renderText());
  });
  document.querySelectorAll("button[data-arg-del]").forEach((b) => {
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const i = Number(b.dataset.idx);
      const k = Number(b.dataset.k);
      const base = verdicts[i] || { action: "keep" };
      const args = (base.arguments !== undefined ? base.arguments : ((cur.candidates[i] || {}).arguments || []))
        .filter((_, j) => j !== k);
      verdicts[i] = { ...base, arguments: args, action: base.action === "keep" ? "modify" : base.action };
      renderCands();
      renderText();
    });
  });
  document.querySelectorAll("select[data-arg-add]").forEach((s) => {
    s.addEventListener("change", () => {
      if (!s.value) return;
      const i = Number(s.dataset.idx);
      const base = verdicts[i] || { action: "keep" };
      const args = (base.arguments !== undefined ? base.arguments : ((cur.candidates[i] || {}).arguments || []))
        .map((a) => ({ ...a }));
      args.push({ role: s.value, text: "" });
      verdicts[i] = { ...base, arguments: args, action: base.action === "keep" ? "modify" : base.action };
      renderCands();
      renderText();
    });
  });
  document.querySelectorAll(".del-add").forEach((b) => {
    b.addEventListener("click", () => {
      added.splice(Number(b.dataset.k), 1);
      renderCands();
      renderText();
    });
  });
  // 长事件文本：点击展开 / 收起
  document.querySelectorAll(".cand-main.clamp").forEach((el) => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      el.classList.toggle("open");
    });
  });
}

/* ---------- 提交载荷 ---------- */

function buildItems() {
  const items = (cur.candidates || []).map((c, i) => {
    const v = verdicts[i] || { action: "keep" };
    const base = { idx: i, action: v.action, source: pickSource(c) };
    for (const k of ["type", "event_type", "subtype", "kind", "relation", "stance"]) {
      if (v[k] !== undefined) base[k] = v[k];
    }
    // 论元被编辑过则覆盖 source 里的原始论元
    if (v.arguments !== undefined) base.arguments = v.arguments;
    return base;
  });
  for (const a of added) {
    const item = {
      action: "add",
      offset: a.offset,
      text: a.text,
      kind: a.kind || "entity",
    };
    for (const k of ["type", "event_type", "subtype", "kind", "role", "relation", "head", "tail"]) {
      if (a[k] !== undefined && a[k] !== "") item[k] = a[k];
    }
    items.push(item);
  }
  return items;
}

function pickSource(c) {
  if (cur.task === "ner") return { text: c.text, type: c.type };
  if (cur.task === "ee") {
    return {
      text: c.text,
      trigger: c.trigger,
      event_type: c.event_type,
      subtype: c.subtype,
      kind: c.kind,
      time_raw: c.time_raw,
      time_norm: c.time_norm,
      arguments: c.arguments,
    };
  }
  return { head: c.head, relation: c.relation, tail: c.tail };
}

/* ---------- 工具 ---------- */

/** 在事件文本中高亮触发词。text 与 trigger 都需转义后再拼。 */
function highlightTrigger(text, trigger) {
  const t = String(text || "");
  const g = String(trigger || "");
  if (!t) return "";
  if (!g) return esc(t);
  const i = t.indexOf(g);
  if (i < 0) return esc(t);
  return esc(t.slice(0, i)) + `<b class="trig">${esc(g)}</b>` + esc(t.slice(i + g.length));
}

/** 时间显示：优先归一日期，无则显示原文表述。 */
function timeLabel(c) {
  if (c.time_norm) {
    const prec = c.time_precision && c.time_precision !== "day" ? `（${c.time_precision}）` : "";
    return `时间 ${esc(c.time_norm)}${prec}`;
  }
  if (c.time_raw) return `时间 ${esc(c.time_raw)}`;
  return "时间 —";
}

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

  // 译文稿：显示「对照原文」按钮
  const origBtn = $("orig-toggle");
  if (origBtn) {
    const hasOrig = !!(cur.text_orig || cur.title_orig);
    origBtn.hidden = !hasOrig;
    if (!hasOrig) {
      const pane = getOrigPane();
      if (pane) pane.hidden = true;
      cur.dual = false;
    }
  }

  renderSchema();
  renderText();
  renderCands();
  if (cur.dual) renderOrig();
}

/* ---------- 原文对照 ---------- */

function getOrigPane() {
  return document.getElementById("orig");
}

function renderOrig() {
  const pane = getOrigPane();
  if (!pane) return;
  const show = !!(cur && (cur.text_orig || cur.title_orig));
  pane.hidden = !show || !cur.dual;
  if (!show || !cur.dual) return;
  const t = cur.title_orig || "";
  const b = esc(cur.text_orig || "").replace(/\n/g, "<br>");
  pane.innerHTML = `<div class="orig-head">英文原文</div>
    ${t ? `<div class="orig-title">${esc(t)}</div>` : ""}
    <div class="orig-body">${b}</div>`;
}

function toggleOrig() {
  if (!cur) return;
  cur.dual = !cur.dual;
  const btn = $("orig-toggle");
  if (btn) btn.classList.toggle("primary", cur.dual);
  const wrap = document.querySelector(".docpane");
  if (wrap) wrap.classList.toggle("dual", cur.dual);
  renderOrig();
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

/* ---------- 键盘 ---------- */

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

/* 通用：任务分段按钮 */
function bindTaskTabs(onChange) {
  for (const b of document.querySelectorAll("#task-seg .seg-btn")) {
    b.addEventListener("click", () => {
      if (state.task === b.dataset.task) return;
      state.task = b.dataset.task;
      skipped.clear();
      for (const x of document.querySelectorAll("#task-seg .seg-btn")) x.classList.toggle("on", x === b);
      onChange();
    });
  }
}

/* 通用：清空与提交通用绑定 */
function bindCommonButtons() {
  const on = (id, ev, fn) => {
    const el = $(id);
    if (el) el.addEventListener(ev, fn);
  };
  on("submit", "click", () => submit());
  on("select-mode", "click", toggleSelecting);
  on("orig-toggle", "click", toggleOrig);
  on("clear", "click", () => {
    if (!cur) return;
    verdicts = (cur.candidates || []).map(() => ({ action: "keep" }));
    added = [];
    renderCands();
    renderText();
  });
}

/* 通用：标完时的占位 */
function renderDone(tip, extraHtml) {
  cur = null;
  added = [];
  $("doctitle").textContent = tip.title;
  $("text").innerHTML = `<div class="empty"><p>${esc(tip.body)}</p>${extraHtml || ""}</div>`;
  $("cands").innerHTML = '<div class="empty">没有待标稿件</div>';
  const dm = $("docmeta");
  if (dm) dm.innerHTML = "";
  const ob = $("orig-toggle");
  if (ob) ob.hidden = true;
  const pane = getOrigPane();
  if (pane) pane.hidden = true;
  const wrap = document.querySelector(".docpane");
  if (wrap) wrap.classList.remove("dual");
  // 清空导航状态
  const pos = $("nav-pos");
  if (pos) pos.textContent = "0 / 0";
  const prev = $("prev");
  const next = $("next");
  if (prev) prev.disabled = true;
  if (next) next.disabled = true;
  const badge = $("cur-done");
  if (badge) badge.hidden = true;
}
