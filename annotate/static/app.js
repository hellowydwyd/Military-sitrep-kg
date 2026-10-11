(() => {
  const state = {
    annotator: localStorage.getItem("annotator") || "A",
    queue: [],
    chunkId: null,
    data: null,
    events: [],
    activeEvent: 0,
    activeArg: 0,
  };

  const $ = (id) => document.getElementById(id);
  const toast = (msg) => {
    const el = $("toast");
    el.hidden = false;
    el.textContent = msg;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 2500);
  };

  function selectionText() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return "";
    const root = $("chunk-text");
    if (!root.contains(sel.anchorNode) || !root.contains(sel.focusNode)) return "";
    return sel.toString();
  }

  function locate(hay, needle) {
    if (!needle) return null;
    const i = hay.indexOf(needle);
    if (i < 0) return null;
    return [i, i + needle.length];
  }

  function isInText(text, span) {
    return !!(span && locate(text, span));
  }

  async function api(path, opts) {
    const r = await fetch(path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok && data.ok === false) return data;
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }

  function setAnnotator(a) {
    state.annotator = a;
    localStorage.setItem("annotator", a);
    $("annotator").value = a;
  }

  async function loadQueue() {
    const q = await api(`/api/queue?annotator=${state.annotator}`);
    state.queue = q.items || [];
    $("progress").textContent = `${q.n_done}/${q.n} 完成`;
    const ul = $("queue-list");
    ul.innerHTML = "";
    state.queue.forEach((it, idx) => {
      const li = document.createElement("li");
      li.dataset.id = it.chunk_id;
      li.dataset.idx = String(idx);
      if (it.chunk_id === state.chunkId) li.classList.add("active");
      if (it.status === "done") li.classList.add("done");
      if (it.status === "skip") li.classList.add("skip");
      li.innerHTML = `<div>${escapeHtml(it.title || it.chunk_id)}</div>
        <div class="st">${escapeHtml(it.stratum || "")} · ${escapeHtml(it.status)}${it.has_annotation ? " · 已存" : ""}</div>`;
      li.onclick = () => openChunk(it.chunk_id);
      ul.appendChild(li);
    });
  }

  async function openChunk(chunkId) {
    state.chunkId = chunkId;
    const data = await api(`/api/chunk/${encodeURIComponent(chunkId)}?annotator=${state.annotator}`);
    state.data = data;
    state.events = (data.events || []).map(cloneEvent);
    state.activeEvent = 0;
    state.activeArg = 0;
    $("title").textContent = data.title || chunkId;
    $("pub").textContent = data.publish_time || "";
    $("stratum").textContent = data.stratum || "";
    $("source").textContent = data.source === "annotation" ? "定稿" : "预标";
    const url = $("url");
    if (data.url) {
      url.href = data.url;
      url.textContent = "原文链接";
      url.hidden = false;
    } else {
      url.hidden = true;
    }
    $("notes").value = data.notes || "";
    $("errors").hidden = true;
    renderText();
    renderEvents();
    await loadQueue();
  }

  function cloneEvent(ev) {
    return {
      trigger: ev.trigger || "",
      kind: ev.kind || "发生",
      raw_time: ev.raw_time || "",
      evidence: ev.evidence || "",
      arguments: (ev.arguments || []).map((a) => ({
        role: a.role || "主体",
        text: a.text || "",
      })),
    };
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[c]);
  }

  function renderText() {
    const text = state.data?.text || "";
    const ev = state.events[state.activeEvent];
    if (!ev || !text) {
      $("chunk-text").textContent = text;
      return;
    }
    const spans = [];
    const tloc = locate(text, ev.trigger);
    if (tloc) spans.push({ ...{ a: tloc[0], b: tloc[1] }, cls: "trig" });
    (ev.arguments || []).forEach((arg) => {
      const loc = locate(text, arg.text);
      if (loc) spans.push({ a: loc[0], b: loc[1], cls: "arg" });
    });
    spans.sort((x, y) => x.a - y.a || y.b - x.b);
    // merge non-overlapping prefer earlier
    const kept = [];
    let last = -1;
    for (const s of spans) {
      if (s.a < last) continue;
      kept.push(s);
      last = s.b;
    }
    let html = "";
    let i = 0;
    for (const s of kept) {
      html += escapeHtml(text.slice(i, s.a));
      html += `<mark class="${s.cls}">${escapeHtml(text.slice(s.a, s.b))}</mark>`;
      i = s.b;
    }
    html += escapeHtml(text.slice(i));
    $("chunk-text").innerHTML = html || escapeHtml(text);
  }

  function renderEvents() {
    const box = $("events");
    box.innerHTML = "";
    const text = state.data?.text || "";
    state.events.forEach((ev, idx) => {
      const card = document.createElement("article");
      card.className = "event-card" + (idx === state.activeEvent ? " active" : "");
      card.onclick = (e) => {
        if (e.target.closest("button,input,select,textarea")) return;
        state.activeEvent = idx;
        renderText();
        renderEvents();
      };

      const trigBad = !isInText(text, ev.trigger);
      const timeBad = ev.raw_time && !isInText(text, ev.raw_time);

      card.innerHTML = `
        <div class="row ${trigBad ? "bad" : ""}"><label>trigger</label>
          <input data-f="trigger" data-i="${idx}" value="${escapeHtml(ev.trigger)}" /></div>
        <div class="row"><label>kind</label>
          <select data-f="kind" data-i="${idx}">
            ${["发生", "表态", "计划"].map((k) =>
              `<option value="${k}" ${ev.kind === k ? "selected" : ""}>${k}</option>`
            ).join("")}
          </select></div>
        <div class="row ${timeBad ? "bad" : ""}"><label>raw_time</label>
          <input data-f="raw_time" data-i="${idx}" value="${escapeHtml(ev.raw_time)}" /></div>
        <div class="row"><label>evidence</label>
          <textarea data-f="evidence" data-i="${idx}">${escapeHtml(ev.evidence)}</textarea></div>
        <div class="args"></div>
        <div class="event-actions">
          <button type="button" data-act="add-arg" data-i="${idx}">+ 论元</button>
          <button type="button" data-act="dup" data-i="${idx}">复制</button>
          <button type="button" data-act="del" data-i="${idx}">删除</button>
        </div>
      `;
      const argsBox = card.querySelector(".args");
      (ev.arguments || []).forEach((arg, j) => {
        const bad = !isInText(text, arg.text);
        const row = document.createElement("div");
        row.className = "arg-row" + (bad ? " bad" : "");
        row.innerHTML = `
          <select data-arg-role data-i="${idx}" data-j="${j}">
            ${["主体", "对象", "地点", "装备"].map((r) =>
              `<option value="${r}" ${arg.role === r ? "selected" : ""}>${r}</option>`
            ).join("")}
          </select>
          <input data-arg-text data-i="${idx}" data-j="${j}" value="${escapeHtml(arg.text)}" />
          <button type="button" data-act="del-arg" data-i="${idx}" data-j="${j}">×</button>
        `;
        row.querySelector("input").onclick = () => { state.activeEvent = idx; state.activeArg = j; };
        argsBox.appendChild(row);
      });

      card.querySelectorAll("input[data-f], textarea[data-f], select[data-f]").forEach((el) => {
        el.addEventListener("input", () => {
          const i = +el.dataset.i;
          state.events[i][el.dataset.f] = el.value;
          state.activeEvent = i;
          if (el.dataset.f === "trigger" || el.dataset.f === "raw_time") renderText();
        });
        el.addEventListener("focus", () => { state.activeEvent = +el.dataset.i; renderText(); renderEvents(); });
      });
      card.querySelectorAll("[data-arg-role]").forEach((el) => {
        el.addEventListener("change", () => {
          state.events[+el.dataset.i].arguments[+el.dataset.j].role = el.value;
        });
      });
      card.querySelectorAll("[data-arg-text]").forEach((el) => {
        el.addEventListener("input", () => {
          const i = +el.dataset.i, j = +el.dataset.j;
          state.events[i].arguments[j].text = el.value;
          state.activeEvent = i;
          state.activeArg = j;
          renderText();
        });
      });
      card.querySelectorAll("button[data-act]").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const act = btn.dataset.act;
          const i = +btn.dataset.i;
          if (act === "del") {
            state.events.splice(i, 1);
            state.activeEvent = Math.max(0, Math.min(state.activeEvent, state.events.length - 1));
          } else if (act === "dup") {
            state.events.splice(i + 1, 0, cloneEvent(state.events[i]));
            state.activeEvent = i + 1;
          } else if (act === "add-arg") {
            state.events[i].arguments.push({ role: "主体", text: "" });
            state.activeEvent = i;
            state.activeArg = state.events[i].arguments.length - 1;
          } else if (act === "del-arg") {
            state.events[i].arguments.splice(+btn.dataset.j, 1);
          }
          renderText();
          renderEvents();
        });
      });
      box.appendChild(card);
    });
  }

  function fillSelection(kind) {
    const sel = selectionText().trim();
    if (!sel) {
      toast("请先在正文中选中文本");
      return;
    }
    if (!state.events.length) {
      state.events.push(cloneEvent({ kind: "发生", arguments: [{ role: "主体", text: "" }] }));
      state.activeEvent = 0;
    }
    const ev = state.events[state.activeEvent];
    if (kind === "trigger") ev.trigger = sel;
    else if (kind === "raw_time") ev.raw_time = sel;
    else if (kind === "evidence") ev.evidence = sel;
    else if (kind === "arg") {
      if (!ev.arguments.length) ev.arguments.push({ role: "主体", text: "" });
      const j = Math.min(state.activeArg, ev.arguments.length - 1);
      ev.arguments[j].text = sel;
    }
    renderText();
    renderEvents();
  }

  async function save(status) {
    if (!state.chunkId) return;
    $("errors").hidden = true;
    const payload = {
      events: state.events,
      notes: $("notes").value,
      status: status || "done",
    };
    const res = await api(`/api/chunk/${encodeURIComponent(state.chunkId)}?annotator=${state.annotator}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      $("errors").hidden = false;
      $("errors").textContent = (res.errors || [res.error || "保存失败"]).join("\n");
      toast("校验未通过");
      return;
    }
    toast(status === "skip" ? "已跳过" : "已保存");
    state.events = (res.chunk.events || []).map(cloneEvent);
    state.data = res.chunk;
    renderText();
    renderEvents();
    await loadQueue();
  }

  function moveChunk(delta) {
    if (!state.queue.length) return;
    let idx = state.queue.findIndex((x) => x.chunk_id === state.chunkId);
    if (idx < 0) idx = 0;
    else idx = Math.max(0, Math.min(state.queue.length - 1, idx + delta));
    openChunk(state.queue[idx].chunk_id);
  }

  function bind() {
    $("annotator").value = state.annotator;
    $("annotator").onchange = async () => {
      setAnnotator($("annotator").value);
      await loadQueue();
      if (state.chunkId) openChunk(state.chunkId);
    };
    $("btn-save").onclick = () => save("done");
    $("btn-skip").onclick = () => save("skip");
    $("btn-add-event").onclick = () => {
      state.events.push(cloneEvent({
        kind: "发生",
        arguments: [{ role: "主体", text: "" }],
      }));
      state.activeEvent = state.events.length - 1;
      renderEvents();
      renderText();
    };
    $("btn-export").onclick = async () => {
      const res = await api(`/api/export?annotator=${state.annotator}`, { method: "POST", body: "{}" });
      toast(res.path ? `已导出 ${res.path}` : "导出完成");
    };
    document.querySelectorAll("[data-fill]").forEach((btn) => {
      btn.onclick = () => fillSelection(btn.dataset.fill);
    });
    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea, select")) {
        if (e.key === "s" && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          save("done");
        }
        return;
      }
      if (e.key === "j") moveChunk(1);
      if (e.key === "k") moveChunk(-1);
      if (e.key === "s") save("done");
    });
  }

  async function init() {
    bind();
    await loadQueue();
    if (state.queue.length) await openChunk(state.queue[0].chunk_id);
  }

  init().catch((err) => {
    console.error(err);
    toast(String(err));
  });
})();
