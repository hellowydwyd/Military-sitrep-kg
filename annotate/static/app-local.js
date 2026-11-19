/* 本地版控制器：读用户选中的 jsonl，不依赖服务器。
 *
 * 标注记录存在浏览器 localStorage，可随时导出 jsonl。
 * 同一份卷换机器时，导出的文件可以合并。
 */

const STORE_KEY = "annotate.local.v1";

/* docs 按任务分组：{ ner: [...], ee: [...], re: [...] } */
let docs = { ner: [], ee: [], re: [] };
let fileName = "";
let records = {};      // 「任务:doc_id」-> 提交记录
let cursor = 0;        // 当前篇在 docs[task] 里的下标

function recKey(task, docId) {
  // 三个任务共用同一批稿件，只用 doc_id 会让 NER/EE/RE 的判定互相覆盖
  return `${task || "ner"}:${docId || ""}`;
}

function loadStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {};
    records = {};
    for (const [k, v] of Object.entries(raw)) {
      if (!v || typeof v !== "object") continue;
      // 兼容旧格式（键为纯 doc_id）：按记录里的 task 重建键
      const key = k.includes(":") ? k : recKey(v.task, v.doc_id || k);
      records[key] = v;
    }
  } catch {
    records = {};
  }
}

function saveStore() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(records));
  } catch (e) {
    setHint("本地存储已满，请先导出结果");
  }
}

/* ---------- 文件解析 ---------- */

function detectTask(row) {
  if (Array.isArray(row.entities)) return "ner";
  if (Array.isArray(row.events)) return "ee";
  if (Array.isArray(row.triples)) return "re";
  return "";
}

function parseJsonl(text) {
  const out = { ner: [], ee: [], re: [] };
  const bad = [];
  for (const line of text.split(/\r?\n/)) {
    const s = line.trim();
    if (!s) continue;
    let row;
    try {
      row = JSON.parse(s);
    } catch {
      bad.push(s.slice(0, 40));
      continue;
    }
    const t = detectTask(row);
    if (!t) continue;
    if (!row.doc_id) row.doc_id = `d${out[t].length}`;
    out[t].push(row);
  }
  return { out, bad };
}

async function readFiles(files) {
  const acc = { ner: [], ee: [], re: [] };
  let bad = 0;
  const names = [];
  for (const f of files) {
    const text = await f.text();
    const r = parseJsonl(text);
    for (const t of ["ner", "ee", "re"]) acc[t].push(...r.out[t]);
    bad += r.bad.length;
    names.push(f.name);
  }
  docs = acc;
  fileName = names.join(" + ");
  cursor = 0;
  const counts = ["ner", "ee", "re"].map((t) => `${TASK_LABEL[t]} ${acc[t].length}`).join(" · ");
  setHint(`已载入 ${fileName}：${counts}${bad ? `，${bad} 行解析失败` : ""}`);
  updateFileInfo();
  pickInitialTask();
}

function updateFileInfo() {
  const el = $("file-info");
  if (!el) return;
  const t = state.task;
  const n = (docs[t] || []).length;
  el.textContent = fileName
    ? `${fileName} · ${TASK_LABEL[t] || t} ${n} 篇`
    : "未载入文件";
}

function pickInitialTask() {
  // 优先切到有内容的第一个任务
  for (const t of ["ner", "ee", "re"]) {
    if ((docs[t] || []).length) {
      state.task = t;
      for (const b of document.querySelectorAll("#task-seg .seg-btn")) {
        b.classList.toggle("on", b.dataset.task === t);
      }
      for (const b of document.querySelectorAll("#task-seg .seg-btn")) {
        b.disabled = !(docs[b.dataset.task] || []).length;
      }
      break;
    }
  }
  loadNext().catch(reportError);
}

/* ---------- 载入与提交 ---------- */

function doneIds() {
  // 只统计当前任务已标的 doc_id
  const s = new Set();
  const prefix = `${state.task}:`;
  for (const [k, r] of Object.entries(records)) {
    if (r.task === state.task || k.startsWith(prefix)) s.add(r.doc_id || k.slice(prefix.length));
  }
  return s;
}

/** 载入指定下标的一篇。已标过的稿件恢复已有判定。 */
function showAt(idx) {
  const list = docs[state.task] || [];
  if (idx < 0 || idx >= list.length) return false;
  cursor = idx;
  cur = viewDocs(state.task, list[idx]);
  added = [];
  activeIdx = -1;
  const saved = records[recKey(state.task, cur.doc_id)];
  if (saved && Array.isArray(saved.items)) {
    // 已有记录：恢复判定，按 idx 归位
    verdicts = (cur.candidates || []).map((c, i) => {
      const it = saved.items.find((x) => x.idx === i && x.action !== "add");
      if (!it) return { action: "keep" };
      const v = { action: it.action };
      for (const k of ["type", "event_type", "subtype", "kind", "relation", "stance"]) {
        if (it[k] !== undefined) v[k] = it[k];
      }
      if (it.arguments !== undefined) v.arguments = it.arguments;
      return v;
    });
    // 手工补标还原
    for (const it of saved.items) {
      if (it.action !== "add") continue;
      added.push({
        kind: it.kind || "entity",
        text: it.text || "",
        offset: it.offset || [0, 0],
        type: it.type, event_type: it.event_type, subtype: it.subtype,
        relation: it.relation, head: it.head, tail: it.tail,
      });
    }
    if ($("notes")) $("notes").value = saved.notes || "";
  } else {
    verdicts = (cur.candidates || []).map(() => ({ action: "keep" }));
    if ($("notes")) $("notes").value = "";
  }
  render();
  updateNav();
  return true;
}

/** 更新前进后退按钮的可用状态与位置提示。 */
function updateNav() {
  const list = docs[state.task] || [];
  const done = doneIds();
  const pos = $("nav-pos");
  if (pos) {
    pos.textContent = list.length ? `${cursor + 1} / ${list.length}` : "0 / 0";
  }
  const prev = $("prev");
  const next = $("next");
  if (prev) prev.disabled = cursor <= 0;
  if (next) next.disabled = cursor >= list.length - 1;
  // 已标提示
  const badge = $("cur-done");
  if (badge) {
    const isDone = cur && done.has(cur.doc_id);
    badge.hidden = !isDone;
  }
  updateRail({ total: list.length, done_count: done.size, remaining: list.length - done.size });
  updateFileInfo();
}

function goPrev() {
  if (cursor <= 0) return;
  if (showAt(cursor - 1)) setHint("上一题");
}

function goNext() {
  const list = docs[state.task] || [];
  if (cursor >= list.length - 1) return;
  if (showAt(cursor + 1)) setHint("下一题");
}

async function loadNext() {
  const list = docs[state.task] || [];
  if (!list.length) {
    renderDone(
      { title: "没有可标的稿件", body: "先在左上角选择 jsonl 文件。" },
      "",
    );
    updateRail({ total: 0, done_count: 0, remaining: 0 });
    updateFileInfo();
    return;
  }
  const done = doneIds();
  // 从当前位置起找第一篇未标的；找不到就停在原地
  let idx = -1;
  for (let k = 0; k < list.length; k++) {
    const i = (cursor + k) % list.length;
    const row = list[i];
    if (done.has(row.doc_id) || skipped.has(row.doc_id)) continue;
    idx = i;
    break;
  }
  if (idx < 0) {
    // 全部标完：停在最后一篇，允许回看
    if (showAt(list.length - 1)) {
      setHint("已全部标完，可用「上一题」回看修改");
      const b = $("cur-done");
      if (b) b.hidden = false;
    }
    return;
  }
  showAt(idx);
}

/* 与服务器版 view() 保持一致的裁剪逻辑 */
function viewDocs(task, row) {
  const base = {
    doc_id: row.doc_id,
    title: row.title,
    text: row.text || "",
    task,
  };
  // 译文稿带原文，界面可做对照
  if (row.translated || row.text_orig) {
    base.title_orig = row.title_orig || "";
    base.text_orig = row.text_orig || "";
    base.translated = !!row.translated;
  }
  if (task === "ner") {
    base.candidates = row.entities || [];
    base.schema = { types: ["国家", "机构", "人物", "装备", "地点", "作品"] };
  } else if (task === "ee") {
    base.candidates = row.events || [];
    base.schema = {
      event_types: ["对抗", "部署", "合作", "表态", "灾伤", "治理", "其他"],
      subtypes: {
        对抗: ["打击", "空袭", "拦截", "发射", "控制"],
        部署: ["投送", "机动", "战备", "演习", "撤离"],
        合作: ["签署", "谈判", "会议", "访问", "援助"],
        表态: ["声明", "呼吁", "警告", "否认", "承认", "谴责"],
        灾伤: ["伤亡", "事故"],
        治理: ["立法", "司法", "人事", "行政"],
        其他: [],
      },
      kinds: ["发生", "计划"],
      roles: ["施事", "受事", "涉及", "到场", "打击对象", "拦截对象", "发射平台", "发射物", "战果", "表态内容"],
      stances: ["支持", "反对", "谴责", "警告", "否认", "承认", "呼吁", "要求", "关切", "中立"],
    };
  } else {
    base.candidates = row.triples || [];
    base.schema = {
      relations: ["隶属", "辖属", "驻地", "部署方", "去向", "任职", "会晤", "搭载", "位于", "敌对"],
      endpoints: "两端都必须是实体，不能是事件",
    };
  }
  return base;
}

async function submit() {
  if (!cur) return;
  const key = recKey(cur.task, cur.doc_id);
  const wasDone = !!records[key];
  records[key] = {
    task: cur.task,
    annotator: ($("annotator").value || "A").toUpperCase(),
    doc_id: cur.doc_id,
    items: buildItems(),
    notes: $("notes").value || "",
    at: new Date().toISOString(),
  };
  saveStore();
  $("notes").value = "";
  setHint(wasDone ? "已更新判定" : "已提交");
  // 提交后前进到下一篇未标的；若当前已是最后一篇则留在原地
  cursor += 1;
  await loadNext();
}

function reportError(e) {
  $("text").innerHTML = `<div class="empty">出错：${esc(e.message)}</div>`;
}

/* ---------- 导出 ---------- */

function exportRecords() {
  const all = Object.values(records);
  if (!all.length) {
    setHint("还没有标注记录");
    return;
  }
  const who = ($("annotator").value || "A").toUpperCase();
  const byTask = { ner: 0, ee: 0, re: 0 };
  for (const r of all) byTask[r.task] = (byTask[r.task] || 0) + 1;
  const lines = all.map((r) => JSON.stringify(r));
  const blob = new Blob([lines.join("\n") + "\n"], { type: "application/x-ndjson" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `verdicts_${who}_${new Date().toISOString().slice(0, 10)}.jsonl`;
  a.click();
  URL.revokeObjectURL(a.href);
  const parts = [];
  for (const t of ["ner", "ee", "re"]) {
    if (byTask[t]) parts.push(`${TASK_LABEL[t]} ${byTask[t]}`);
  }
  setHint(`已导出 ${all.length} 条（${parts.join("，")}）`);
}

function clearRecords() {
  // 只清当前任务，避免误删其他任务的标注成果
  const prefix = `${state.task}:`;
  const keys = Object.keys(records).filter(
    (k) => records[k].task === state.task || k.startsWith(prefix),
  );
  if (!keys.length) {
    setHint(`${TASK_LABEL[state.task]} 还没有记录`);
    return;
  }
  if (!confirm(`确定清空「${TASK_LABEL[state.task]}」的 ${keys.length} 条记录？建议先导出。`)) return;
  for (const k of keys) delete records[k];
  saveStore();
  cursor = 0;
  skipped.clear();
  loadNext().catch(reportError);
  setHint(`已清空 ${TASK_LABEL[state.task]} 的 ${keys.length} 条`);
}

/* ---------- 启动 ---------- */

loadStore();
bindCommonButtons();
bindTaskTabs(() => {
  cursor = 0;
  skipped.clear();
  if ($("notes")) $("notes").value = "";
  loadNext().catch(reportError);
});

const on = (id, ev, fn) => {
  const el = $(id);
  if (el) el.addEventListener(ev, fn);
};

on("skip", "click", () => {
  if (cur) skipped.add(cur.doc_id);
  cursor += 1;
  loadNext().catch(reportError);
});
on("prev", "click", goPrev);
on("next", "click", goNext);
on("export", "click", exportRecords);
on("clear-all", "click", clearRecords);

/* 键盘：A / D 或 PageUp / PageDown 翻页 */
document.addEventListener("keydown", (ev) => {
  const tag = (ev.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (ev.key === "PageUp" || ev.key === "ArrowUp") {
    ev.preventDefault();
    goPrev();
  } else if (ev.key === "PageDown" || ev.key === "ArrowDown") {
    ev.preventDefault();
    goNext();
  }
});

const fileInput = $("file-input");
if (fileInput) {
  fileInput.addEventListener("change", (ev) => {
    const fs = ev.target.files;
    if (!fs || !fs.length) return;
    readFiles([...fs]).catch(reportError);
  });
}
