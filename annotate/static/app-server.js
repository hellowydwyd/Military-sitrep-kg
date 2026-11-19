/* 服务器版控制器：数据走 /api/*，标注记录落在 data/annotations/ */

let fileList = [];   // 服务端可选的卷文件

async function api(path, opts) {
  const r = await fetch(path, opts || { cache: "no-store" });
  if (!r.ok) throw new Error(path + " " + r.status);
  return r.json();
}

function params() {
  const p = new URLSearchParams({
    task: state.task,
    split: state.split || "train",
    annotator: ($("annotator").value || "A").toUpperCase(),
  });
  if (state.file) p.set("file", state.file);
  if (skipped.size) p.set("skip", [...skipped].join(","));
  return p.toString();
}

/** 当前选中的卷是否属于当前任务。不属于时退回「全部卷」。 */
function fileMatchesTask() {
  if (!state.file) return true;
  const f = fileList.find((x) => x.name === state.file);
  return !f || f.task === state.task;
}

let curIndex = -1;      // 当前篇在本卷中的位置
let curTotal = 0;

/** 把服务端返回的 saved 判定恢复到界面。 */
function applySaved(data) {
  const saved = data.saved;
  if (saved && Array.isArray(saved.items)) {
    verdicts = (data.candidates || []).map((c, i) => {
      const it = saved.items.find((x) => x.idx === i && x.action !== "add");
      if (!it) return { action: "keep" };
      const v = { action: it.action };
      for (const k of ["type", "event_type", "subtype", "kind", "relation", "stance"]) {
        if (it[k] !== undefined) v[k] = it[k];
      }
      if (it.arguments !== undefined) v.arguments = it.arguments;
      return v;
    });
    added = [];
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
    verdicts = (data.candidates || []).map(() => ({ action: "keep" }));
    added = [];
    if ($("notes")) $("notes").value = "";
  }
}

/** 更新导航按钮状态。 */
function updateNav() {
  const pos = $("nav-pos");
  if (pos) pos.textContent = curTotal ? `${curIndex + 1} / ${curTotal}` : "0 / 0";
  const prev = $("prev");
  const next = $("next");
  if (prev) prev.disabled = curIndex <= 0;
  if (next) next.disabled = curIndex >= curTotal - 1;
  const badge = $("cur-done");
  if (badge) badge.hidden = !(cur && cur.done_count !== undefined && !cur.isNew);
}

function goPrev() {
  if (curIndex <= 0) return;
  loadAt(curIndex - 1).catch(reportError);
}

function goNext() {
  if (curIndex >= curTotal - 1) return;
  loadAt(curIndex + 1).catch(reportError);
}

async function loadAt(index) {
  const data = await api(`/api/at?${params()}&index=${index}`);
  cur = data;
  curIndex = data.index ?? index;
  curTotal = data.total ?? 0;
  cur.isNew = !data.saved;
  activeIdx = -1;
  applySaved(data);
  render();
  updateNav();
  updateRail(data);
}

async function loadNext() {
  const offset = Math.max(0, curIndex + 1);
  const data = await api(`/api/next?${params()}&offset=${offset}`);
  if (data.done) {
    // 全部标完：停在最后一篇，允许回看修改
    if (curTotal > 0) {
      await loadAt(curTotal - 1);
      setHint("已全部标完，可用「上一题」回看修改");
      const b = $("cur-done");
      if (b) b.hidden = false;
      return;
    }
    const tip = skipped.size
      ? { title: "本卷已全部看过", body: `已跳过 ${skipped.size} 篇，其余都标完了。` }
      : { title: "本卷已标完", body: "切换任务或卷继续，或换一个标注员编号复核。" };
    const btn = skipped.size
      ? '<button class="ghost" id="reset-skip">重新看被跳过的</button>'
      : "";
    renderDone(tip, btn);
    const rb = $("reset-skip");
    if (rb) rb.addEventListener("click", () => {
      skipped.clear();
      loadNext().catch(reportError);
    });
    updateRail(data);
    return;
  }
  cur = data;
  curIndex = data.index ?? 0;
  curTotal = data.total ?? 0;
  cur.isNew = true;
  added = [];
  activeIdx = -1;
  applySaved(data);
  render();
  updateNav();
  updateRail(data);
}

async function submit() {
  if (!cur) return;
  const wasDone = !cur.isNew;
  const payload = {
    task: cur.task,
    doc_id: cur.doc_id,
    annotator: ($("annotator").value || "A").toUpperCase(),
    items: buildItems(),
    notes: $("notes").value || "",
  };
  const r = await api("/api/judge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (r.ok) {
    setHint(wasDone ? "已更新判定" : "已提交");
    await loadNext();
  } else {
    alert("提交失败：" + (r.error || "未知"));
  }
}

function reportError(e) {
  $("text").innerHTML = `<div class="empty">加载失败：${esc(e.message)}<br>请确认服务已启动：python3 -m annotate --port 8767</div>`;
}

/* ---------- 文件选择 ---------- */

async function loadFiles() {
  const d = await api("/api/files");
  fileList = d.files || [];
  renderFileSelect();
}

function renderFileSelect() {
  const sel = $("file-select");
  if (!sel) return;
  const groups = {};
  for (const f of fileList) {
    (groups[f.task] = groups[f.task] || []).push(f);
  }
  let html = '<option value="">全部卷</option>';
  for (const [task, files] of Object.entries(groups)) {
    html += `<optgroup label="${TASK_LABEL[task] || task}">`;
    for (const f of files) {
      const mark = f.done > 0 ? ` (${f.done}/${f.total})` : "";
      html += `<option value="${esc(f.name)}">${esc(f.name)} · ${f.total} 篇${mark}</option>`;
    }
    html += "</optgroup>";
  }
  sel.innerHTML = html;
  sel.value = state.file || "";
}

/* ---------- 启动 ---------- */

bindCommonButtons();
bindTaskTabs(() => {
  // 换任务后，若原来选的卷不属于新任务，退回全部卷
  if (!fileMatchesTask()) {
    state.file = "";
    const sel = $("file-select");
    if (sel) sel.value = "";
  }
  // 位置重置，避免沿用上一个任务的下标
  curIndex = -1;
  if ($("notes")) $("notes").value = "";
  loadNext().catch(reportError);
});

const onEl = (id, ev, fn) => {
  const el = $(id);
  if (el) el.addEventListener(ev, fn);
};

onEl("skip", "click", () => {
  if (cur) skipped.add(cur.doc_id);
  loadNext().catch(reportError);
});

onEl("prev", "click", goPrev);
onEl("next", "click", goNext);

onEl("annotator", "change", () => {
  curIndex = -1;
  loadNext().catch(reportError);
});

const fileSel = $("file-select");
if (fileSel) {
  fileSel.addEventListener("change", () => {
    state.file = fileSel.value;
    skipped.clear();
    curIndex = -1;
    loadNext().catch(reportError);
  });
}

/* 键盘：PageUp / PageDown 或 ↑ / ↓ 翻页 */
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

loadFiles()
  .then(() => loadNext())
  .catch(reportError);
