// 仕様道場の画面。S1 プロジェクト一覧 と S2 作業画面(基本設計書 2 画面項目)。
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const PHASE_LABEL = {requirements: "要件定義", concept: "構想設計", basic: "基本設計", done: "完了"};
const MARK = {decided: "✔", needs_check: "⚠", undecided: "□"};

let S = null;          // 作業画面の状態(サーバーから取った内容)
let view = {mode: "guided", tab: "talk", cur: null, doc: "requirements", hint: false, advice: null,
            selected: [], free: "", editing: false, reviewText: null, chatStream: null, candidates: []};
let qJobs = {};        // 項目ごとの「質問を作っている最中」の仕事(モードを切り替えても続く)
let busyTimer = null;

// ---- 共通 ----
async function api(method, path, body) {
  const res = await fetch(path, {method, headers: body ? {"Content-Type": "application/json"} : {},
                                 body: body ? JSON.stringify(body) : undefined});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `エラーが起きました(${res.status})`);
  return data;
}
function toast(msg, action) {
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label; b.onclick = () => { t.hidden = true; action.run(); };
    t.appendChild(b);
  }
  t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, action ? 8000 : 3500);
}
function busy(label) {
  clearInterval(busyTimer);
  const b = $("#busy");
  if (!label) { b.textContent = ""; return; }
  let s = 0;
  const draw = () => b.innerHTML = `<span class="busy">${esc(label)}…(経過 ${s}秒)</span>`;
  draw(); busyTimer = setInterval(() => { s++; draw(); }, 1000);
}
function runJob(jobId, h) {
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  es.addEventListener("text", e => h.onText && h.onText(JSON.parse(e.data)));
  es.addEventListener("reset", () => h.onReset && h.onReset());
  es.addEventListener("result", e => { es.close(); h.onResult(JSON.parse(e.data)); });
  es.addEventListener("error", e => {
    if (!e.data) return; // 接続が一時的に切れただけなら、ブラウザが続きからつなぎ直す
    es.close(); h.onError(JSON.parse(e.data).message);
  });
  return es;
}
let lintTimer = null;
function attachLint(el, box) {
  el.addEventListener("input", () => {
    clearTimeout(lintTimer);
    lintTimer = setTimeout(async () => {
      const ws = el.value.trim() ? await api("POST", "/api/lint", {text: el.value}) : [];
      box.innerHTML = ws.map(w => `<div class="warnline">${esc(w.message)}</div>`).join("");
    }, 300);
  });
}
function mdToHtml(md) {
  // 資料のプレビュー用の小さな変換(見出し・表・箇条書きだけ)
  const lines = md.split("\n"); let html = ""; let table = null;
  const flush = () => { if (table) { html += "<table class='doc'>" + table.join("") + "</table>"; table = null; } };
  for (const ln of lines) {
    if (ln.startsWith("|")) {
      if (/^\|[-| ]+\|$/.test(ln)) continue;
      const cells = ln.slice(1, -1).split("|").map(c => c.trim());
      table = table || [];
      const tag = table.length ? "td" : "th";
      table.push("<tr>" + cells.map(c => `<${tag}>${esc(c)}</${tag}>`).join("") + "</tr>");
      continue;
    }
    flush();
    if (ln.startsWith("### ")) html += `<h3>${esc(ln.slice(4))}</h3>`;
    else if (ln.startsWith("## ")) html += `<h2>${esc(ln.slice(3))}</h2>`;
    else if (ln.startsWith("# ")) html += `<h1>${esc(ln.slice(2))}</h1>`;
    else if (ln.startsWith("- ")) html += `<div>・${esc(ln.slice(2))}</div>`;
    else if (ln.trim()) html += `<p>${esc(ln)}</p>`;
  }
  flush();
  return html;
}

// ---- 画面の切り替え ----
function route() {
  const m = location.hash.match(/^#\/p\/(\d+)/);
  $("#s1").hidden = !!m; $("#s2").hidden = !m;
  if (m) openProject(+m[1]); else loadList();
}
window.addEventListener("hashchange", route);

// ---- S1 プロジェクト一覧 ----
async function loadList() {
  const archived = $("#showArchived").checked;
  const rows = await api("GET", `/api/projects?archived=${archived ? 1 : 0}`);
  $("#emptyList").hidden = rows.length > 0 || archived;
  $("#projectRows").innerHTML = rows.map(p => `<tr data-id="${p.id}">
    <td>${esc(p.name)}</td><td>${PHASE_LABEL[p.current_phase]}</td>
    <td>${p.current_phase === "done" ? "完了" : p.progress}</td><td>${p.updated_at.slice(0, 10)}</td>
    <td><button data-act="${archived ? "restore" : "archive"}">${archived ? "元に戻す" : "しまう"}</button></td></tr>`).join("");
}
$("#projectRows").addEventListener("click", async e => {
  const tr = e.target.closest("tr"); if (!tr) return;
  const act = e.target.dataset.act;
  if (act) { await api("POST", `/api/projects/${tr.dataset.id}/${act}`); toast(act === "archive" ? "しまいました。「アーカイブを表示」から元に戻せます" : "元に戻しました"); loadList(); return; }
  location.hash = `#/p/${tr.dataset.id}`;
});
$("#showArchived").onchange = loadList;
$("#createBtn").onclick = async () => {
  try {
    const r = await api("POST", "/api/projects", {name: $("#newName").value});
    $("#newName").value = ""; location.hash = `#/p/${r.id}`;
  } catch (e) { toast(e.message); }
};
$("#newName").addEventListener("keydown", e => { if (e.key === "Enter") $("#createBtn").click(); });

// ---- S2 作業画面 ----
async function openProject(pid) {
  if (!S || S.project.id !== pid) {
    view = {...view, cur: null, advice: null, selected: [], free: "", reviewText: null, candidates: [], chatStream: null};
    qJobs = {};
  }
  await reload(pid);
}
async function reload(pid) {
  S = await api("GET", `/api/projects/${pid ?? S.project.id}`);
  if (!view.cur || !findItem(view.cur)) view.cur = S.current_item;
  $("#projName").textContent = "/ " + S.project.name;
  $("#unrev").textContent = S.unreviewed ? `(未 ${S.unreviewed})` : "";
  render();
}
function findItem(key) {
  for (const p of S.phases) for (const it of p.items) if (it.key === key) return {...it, phase: p};
  return null;
}
function renderAside() {
  let h = "";
  for (const p of S.phases) {
    const done = p.items.filter(i => i.status === "decided").length;
    const isCur = p.key === S.project.current_phase;
    h += `<p class="phase ${isCur ? "cur" : ""}"><span>${p.label}</span><span>${done}/${p.items.length}</span></p><ul class="check">`;
    for (const it of p.items) {
      const cur = it.key === view.cur;
      h += `<li data-key="${it.key}" tabindex="0" class="${cur ? "cur" : ""}"><span class="mk ${cur ? "" : it.status}">${cur ? "▶" : MARK[it.status]}</span>${esc(it.label)}</li>`;
    }
    h += "</ul>";
    if (isCur) h += `<button id="completeBtn">${p.label}を完了</button>`;
  }
  if (S.project.current_phase === "done") h += `<p class="gloss">すべての工程が完了しました。「出力」から資料を書き出せます。</p>`;
  $("#aside").innerHTML = h;
  $("#aside").querySelectorAll("li").forEach(li => {
    const go = () => { view.cur = li.dataset.key; view.advice = null; view.selected = []; view.free = ""; view.hint = false; view.editing = false; view.tab = "talk"; render(); };
    li.onclick = go; li.onkeydown = e => { if (e.key === "Enter") go(); };
  });
  const cb = $("#completeBtn"); if (cb) cb.onclick = completePhase;
}
function render() {
  renderAside();
  document.querySelectorAll(".tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === view.tab));
  $("#mGuided").setAttribute("aria-pressed", view.mode === "guided");
  $("#mFree").setAttribute("aria-pressed", view.mode === "free");
  if (view.tab === "doc") return drawDoc();
  if (view.tab === "tree") return drawTree();
  if (view.tab === "gloss") return drawGloss();
  if (view.reviewText !== null) return drawReview();
  view.mode === "guided" ? drawGuided() : drawFree();
}

// ---- ガイド付きモード ----
function startQuestion(key) {
  if (qJobs[key]) return;
  const job = {text: "", turn: null, error: null};
  qJobs[key] = job;
  busy("考え中");
  api("POST", "/api/jobs", {project_id: S.project.id, kind: "question", item_key: key}).then(r => {
    if (r.turn) { job.turn = r.turn; busy(null); delete qJobs[key]; S.open_question = r.turn; render(); return; }
    runJob(r.job_id, {
      onText: t => { job.text += t; if (view.mode === "guided" && view.tab === "talk" && view.cur === key && view.reviewText === null) drawGuided(); },
      onReset: () => { job.text = ""; },
      onResult: res => { busy(null); delete qJobs[key]; S.open_question = res.turn; S.glossary = null; if (view.cur === key) { reload(); } },
      onError: msg => { busy(null); job.error = msg; if (view.cur === key) render(); },
    });
  }).catch(e => { busy(null); job.error = e.message; render(); });
}
function drawGuided() {
  const p = $("#pane"), key = view.cur;
  if (!key) { p.innerHTML = `<div class="senpai"><span class="who">先輩AI</span>この工程の項目はすべて決まりました。左の「完了」ボタンで次の工程に進めます。</div>`; return; }
  const it = findItem(key);
  if (it.status === "needs_check" && !view.editing) {
    p.innerHTML = `<div class="senpai"><span class="who">先輩AI</span>「${esc(it.label)}」に要確認の印が付いています。前提にしている項目(${it.depends_on.map(k => esc(findItem(k).label)).join("、")})が変わったからです。今の内容のままでいいか確かめてください。</div>
      <div class="q"><h3>今の内容:${esc(it.value)}</h3><div class="row"><button class="primary" id="keep">このままでいい</button><button id="redo">決め直す</button></div></div>`;
    $("#keep").onclick = async () => { await api("POST", `/api/projects/${S.project.id}/decisions/${key}/keep`); toast(`「${it.label}」の要確認を外しました`); view.cur = null; reload(); };
    $("#redo").onclick = () => { view.editing = true; drawGuided(); };
    return;
  }
  if (it.status === "decided" && !view.editing) {
    p.innerHTML = `<div class="senpai"><span class="who">先輩AI</span>「${esc(it.label)}」は決定済みです。変えると、これを前提にしている項目に要確認の印が付きます。</div>
      <div class="q"><h3>${esc(it.value)}</h3>${it.reason ? `<div class="gloss">理由:${esc(it.reason)}</div>` : ""}<div class="row"><button id="redo">変更する</button></div></div>`;
    $("#redo").onclick = () => { view.editing = true; drawGuided(); };
    return;
  }
  const oq = S.open_question && S.open_question.item_key === key ? S.open_question : null;
  const job = qJobs[key];
  if (!oq && !job) { startQuestion(key); return drawGuided(); }
  if (!oq) {
    p.innerHTML = `<div class="senpai"><span class="who">先輩AI(${esc(it.label)})</span>${esc(job.text)}<span class="caret"></span></div>` +
      (job.error ? `<div class="errline">${esc(job.error)}</div><div class="row"><button id="retry">もう一度試す</button></div>` : "");
    if (job.error) $("#retry").onclick = () => { delete qJobs[key]; startQuestion(key); drawGuided(); };
    return;
  }
  const q = oq.question;
  const answered = !!view.advice;
  let h = `<div class="senpai"><span class="who">先輩AI(${esc(it.label)})</span>${esc(oq.ai_text)}</div>`;
  if (S.glossary && S.glossary.length) {
    const used = S.glossary.filter(g => oq.ai_text.includes(g.term) || q.question.includes(g.term));
    if (used.length) h += `<div class="gloss" style="margin:-8px 0 12px">${used.map(g => `${esc(g.term)}(ひとことで言うと:${esc(g.explanation)})`).join(" / ")}</div>`;
  }
  h += `<div class="q"><h3>${esc(q.question)}<small>いくつでも選べます</small></h3>`;
  for (const o of q.options) h += `<label class="opt"><input type="checkbox" value="${esc(o.id)}" ${view.selected.includes(o.id) ? "checked" : ""} ${answered ? "disabled" : ""}><span><b>${esc(o.id)}</b> ${esc(o.label)}</span></label>`;
  h += `<div class="row"><input type="text" id="free" placeholder="選択肢にないことは、ここに書けます" value="${esc(view.free)}" ${answered ? "disabled" : ""}>
        <button id="hintBtn">ヒント</button>${answered ? "" : `<button class="primary" id="adviseBtn">選んでアドバイスを見る</button>`}</div>
        <div id="lintbox"></div>${view.hint ? `<div class="hint">${esc(q.hint)}</div>` : ""}<div id="err" class="errline" hidden></div></div>`;
  if (view.advice) {
    const a = view.advice;
    const rec = (a.recommended || []).join("・");
    h += `<div class="advice"><span class="who">アドバイス</span>${esc(a.text)}${a.streaming ? '<span class="caret"></span>' : ""}
      ${!a.streaming && rec ? `<div class="gloss">(先輩のおすすめは ${esc(rec)} でした。正解ではなく一つの意見です)</div>` : ""}
      ${a.contradiction ? `<div class="warnline">${esc(a.contradiction)}</div>` : ""}</div>`;
    if (!a.streaming && !a.error) {
      h += `<div class="decide"><label for="dValue">決定する内容(直してから決定できます)</label><textarea id="dValue" rows="2">${esc(a.value)}</textarea>
        <label for="dReason">理由(あとで見直すときと、AI活用記録のために残します)</label><input type="text" id="dReason" style="width:100%" value="${esc(a.reason || "")}">
        <div class="row"><button class="primary" id="decideBtn">この内容で決定</button><button id="backBtn2">選び直す</button></div><div id="dlint"></div></div>`;
    }
    if (a.error) h += `<div class="errline">${esc(a.error)}</div><div class="row"><button id="backBtn2">選び直す</button></div>`;
  }
  p.innerHTML = h;
  p.querySelectorAll(".opt input").forEach(cb => cb.onchange = () => { view.selected = [...p.querySelectorAll(".opt input:checked")].map(x => x.value); $("#err").hidden = true; });
  const free = $("#free");
  free.oninput = () => { view.free = free.value; $("#err").hidden = true; };
  attachLint(free, $("#lintbox"));
  $("#hintBtn").onclick = () => { view.hint = true; drawGuided(); };
  const ab = $("#adviseBtn"); if (ab) ab.onclick = () => askAdvice(oq);
  const bb = $("#backBtn2"); if (bb) bb.onclick = () => { view.advice = null; drawGuided(); };
  const db = $("#decideBtn");
  if (db) { attachLint($("#dValue"), $("#dlint")); db.onclick = () => decide(key, oq); }
}
async function askAdvice(oq) {
  if (!view.selected.length && !view.free.trim()) { const e = $("#err"); e.textContent = "選択肢を選ぶか、自由に書いてから進んでください"; e.hidden = false; return; }
  try {
    const r = await api("POST", "/api/jobs", {project_id: S.project.id, kind: "advice", turn_id: oq.id, selected: view.selected, free_input: view.free});
    if (r.advice) { view.advice = r.advice; return drawGuided(); }
    view.advice = {text: "", streaming: true, recommended: oq.question.recommended};
    busy("組み合わせを読んでいます"); drawGuided();
    runJob(r.job_id, {
      onText: t => { view.advice.text += t; if (view.mode === "guided" && view.tab === "talk") drawGuided(); },
      onReset: () => { view.advice.text = ""; },
      onResult: res => { busy(null); view.advice = res.advice; if (view.mode === "guided") drawGuided(); },
      onError: msg => { busy(null); view.advice = {text: "", error: msg}; drawGuided(); },
    });
  } catch (e) { toast(e.message); }
}
async function decide(key, oq) {
  const value = $("#dValue").value.trim();
  if (!value) { toast("決定する内容が空です"); return; }
  try {
    const src = `ガイド付き:${oq.question.question}(選択 ${view.selected.join("・") || "自由入力"})`;
    const r = await api("POST", `/api/projects/${S.project.id}/decisions/${key}`, {value, reason: $("#dReason").value, source: src});
    const label = findItem(key).label;
    view.advice = null; view.selected = []; view.free = ""; view.hint = false; view.editing = false; view.cur = null;
    await reload();
    toast(r.needs_check.length ? `「${label}」を決定しました。前提が変わったので ${r.needs_check.map(k => findItem(k).label).join("、")} に要確認を付けました` : `「${label}」を決定しました`);
  } catch (e) { toast(e.message); }
}

// ---- 自由チャット ----
function drawFree() {
  const p = $("#pane");
  let h = `<div class="senpai"><span class="who">先輩AI</span>思いついたことを、そのまま書いてください。決められそうなことを私が抜き出します。</div>`;
  for (const m of S.chat) h += `<div class="msg ${m.role === "user" ? "me" : ""}">${esc(m.text)}</div>`;
  if (view.chatStream) h += `<div class="msg me">${esc(view.chatStream.msg)}</div><div class="msg">${esc(view.chatStream.text)}<span class="caret"></span></div>`;
  view.candidates.forEach((c, i) => {
    h += `<div class="cand"><div class="gloss">この内容で決定しますか?</div><div><b>${esc(findItem(c.item_key).label)}</b></div>
      <textarea rows="2" style="width:100%" data-ci="${i}">${esc(c.value)}</textarea>
      <div class="row"><button class="primary" data-ok="${i}">決定</button><button data-no="${i}">決定しない</button></div></div>`;
  });
  h += `<div class="row"><textarea id="chatIn" rows="2" placeholder="例:部員はみんなスマホしか見ない。練習の前の日に、寝る前にさっと入れる感じ">${esc(view.chatDraft || "")}</textarea><button class="primary" id="send" ${view.chatStream ? "disabled" : ""}>送る</button></div><div id="lintbox"></div>`;
  p.innerHTML = h;
  const ta = $("#chatIn"); attachLint(ta, $("#lintbox"));
  ta.addEventListener("input", () => { view.chatDraft = ta.value; });
  if (view.chatDraft) ta.dispatchEvent(new Event("input"));
  $("#send").onclick = () => sendChat(ta.value);
  p.querySelectorAll("[data-ok]").forEach(b => b.onclick = async () => {
    const i = +b.dataset.ok, c = view.candidates[i];
    const value = p.querySelector(`[data-ci="${i}"]`).value.trim();
    try {
      const r = await api("POST", `/api/projects/${S.project.id}/decisions/${c.item_key}`, {value, reason: c.reason, source: "自由チャット"});
      view.candidates.splice(i, 1);
      if (view.cur === c.item_key) view.cur = null;
      await reload();
      toast(`「${findItem(c.item_key).label}」を決定しました` + (r.needs_check.length ? "(依存している項目に要確認を付けました)" : ""));
    } catch (e) { toast(e.message); }
  });
  p.querySelectorAll("[data-no]").forEach(b => b.onclick = () => { view.candidates.splice(+b.dataset.no, 1); drawFree(); });
}
async function sendChat(msg) {
  if (!msg.trim()) { toast("何か書いてから送ってください"); return; }
  try {
    const r = await api("POST", "/api/jobs", {project_id: S.project.id, kind: "chat", message: msg});
    view.chatStream = {msg, text: ""}; view.chatDraft = ""; busy("考え中"); drawFree();
    runJob(r.job_id, {
      onText: t => { view.chatStream.text += t; if (view.mode === "free" && view.tab === "talk") drawFree(); },
      onReset: () => { view.chatStream.text = ""; },
      onResult: async res => { busy(null); view.chatStream = null; view.candidates.push(...res.candidates); await reload(); },
      onError: msg2 => { busy(null); view.chatStream = null; toast(msg2); render(); },
    });
  } catch (e) { toast(e.message); }
}

// ---- レビュー ----
function drawReview() {
  const p = $("#pane");
  const label = k => findItem(k)?.label || k;
  const layer = {1: "①辞書", 2: "②観点", 3: "③先輩"};
  let h = `<div class="senpai"><span class="who">先輩AI(レビュー)</span>${esc(view.reviewText)}${view.reviewing ? '<span class="caret"></span>' : ""}</div>`;
  if (!view.reviewing) {
    h += S.findings.length ? "" : `<p class="gloss">未対応の指摘はありません。</p>`;
    for (const f of S.findings) h += `<div class="rv"><span class="tag" style="color:var(--accent)">${layer[f.layer]}</span>${f.viewpoint_id ? `<span class="tag" style="color:var(--sub)">${esc(f.viewpoint_id)}</span>` : ""}<span class="m"><b>${esc(label(f.item_key))}</b>:${esc(f.message)}</span><button data-fix="${f.item_key}">直す</button><button data-res="${f.id}">対応済み</button></div>`;
    h += `<div class="row"><button id="rvClose">対話に戻る</button></div>`;
  }
  p.innerHTML = h;
  if (view.reviewing) return;
  $("#rvClose").onclick = () => { view.reviewText = null; render(); };
  p.querySelectorAll("[data-res]").forEach(b => b.onclick = async () => { await api("POST", `/api/findings/${b.dataset.res}/resolve`); await reload(); });
  p.querySelectorAll("[data-fix]").forEach(b => b.onclick = () => { view.reviewText = null; view.cur = b.dataset.fix; view.editing = true; view.mode = "guided"; render(); });
}
async function startReview() {
  try {
    const r = await api("POST", "/api/jobs", {project_id: S.project.id, kind: "review"});
    view.tab = "talk"; view.reviewText = ""; view.reviewing = true; busy("レビュー中"); render();
    runJob(r.job_id, {
      onText: t => { view.reviewText += t; if (view.reviewText !== null) drawReview(); },
      onReset: () => { view.reviewText = ""; },
      onResult: async res => { busy(null); view.reviewing = false; view.reviewText = res.summary; await reload(); },
      onError: msg => { busy(null); view.reviewing = false; view.reviewText = null; toast(msg); render(); },
    });
  } catch (e) { toast(e.message); }
}
$("#reviewBtn").onclick = startReview;

// 未レビューがあれば一度だけ聞く(工程の完了時と出力時)
function confirmUnreviewed(then) {
  if (!S.unreviewed) return then();
  const bar = $("#confirmBar");
  bar.innerHTML = `<span>まだレビューしていない決定が ${S.unreviewed} 件あります。レビューしてから進みますか?</span><button class="primary" id="cfReview">レビューする</button><button id="cfGo">このまま進む</button>`;
  bar.hidden = false;
  $("#cfReview").onclick = () => { bar.hidden = true; startReview(); };
  $("#cfGo").onclick = () => { bar.hidden = true; then(); };
}
function completePhase() {
  confirmUnreviewed(async () => {
    try {
      const r = await api("POST", `/api/projects/${S.project.id}/phases/${S.project.current_phase}/complete`);
      view.cur = null; await reload();
      toast(r.next_phase === "done" ? "すべての工程が完了しました" : `${PHASE_LABEL[r.next_phase]}に進みました`);
    } catch (e) { toast(e.message); }
  });
}

// ---- 資料・ツリー図・用語集 ----
async function drawDoc() {
  const p = $("#pane");
  const types = {requirements: "要件定義書", concept: "構想設計書", basic: "基本設計書", guide: "実装指示書"};
  const bar = `<div class="docbar">${Object.entries(types).map(([k, v]) => `<button data-doc="${k}" aria-pressed="${view.doc === k}">${v}</button>`).join("")}</div>`;
  p.innerHTML = bar + `<p class="gloss">読み込み中…</p>`;
  const d = await api("GET", `/api/projects/${S.project.id}/documents/${view.doc}`);
  p.innerHTML = bar + `<p class="gloss">次に出力すると ver.${esc(d.version)}${d.draft ? "(下書き)" : ""} になります</p><div class="md">${mdToHtml(d.markdown)}</div>`;
  p.querySelectorAll("[data-doc]").forEach(b => b.onclick = () => { view.doc = b.dataset.doc; drawDoc(); });
}
function drawTree() {
  const p = $("#pane");
  const lines = ["flowchart LR"];
  for (const ph of S.phases) {
    lines.push(`subgraph ${ph.key}[${ph.label}]`);
    for (const it of ph.items) lines.push(`  ${it.key.replace(".", "_")}["${MARK[it.status]} ${it.label}"]`);
    lines.push("end");
  }
  for (const ph of S.phases) for (const it of ph.items) for (const d of it.depends_on) lines.push(`${d.replace(".", "_")} --> ${it.key.replace(".", "_")}`);
  p.innerHTML = `<p class="gloss">矢印の先の項目は、矢印の元の項目を前提にしています。元を変えると、先に⚠(要確認)が付きます。</p><pre class="mermaid">${esc(lines.join("\n"))}</pre>`;
  const draw = () => window.mermaid.run({nodes: p.querySelectorAll(".mermaid")}).catch(() => {});
  if (window.mermaid) return draw();
  const s = document.createElement("script");
  s.src = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js";
  s.onload = () => { window.mermaid.initialize({startOnLoad: false, theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "neutral"}); draw(); };
  s.onerror = () => { p.insertAdjacentHTML("beforeend", `<p class="gloss">図を描く部品を読み込めませんでした(インターネットにつながっていないかもしれません)。</p>`); };
  document.head.appendChild(s);
}
function drawGloss() {
  const g = S.glossary || [];
  $("#pane").innerHTML = `<p class="gloss">このプロジェクトでAIが説明した用語です。2回目からはここの説明を使います。</p>` +
    (g.length ? `<table class="doc">${g.map(x => `<tr><th>${esc(x.term)}</th><td>${esc(x.explanation)}</td></tr>`).join("")}</table>` : `<p class="gloss">まだありません。</p>`);
}

// ---- 上のボタン ----
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => { view.tab = b.dataset.tab; render(); });
$("#mGuided").onclick = () => { view.mode = "guided"; view.reviewText = null; render(); };
$("#mFree").onclick = () => { view.mode = "free"; view.reviewText = null; render(); };
$("#backBtn").onclick = () => { location.hash = "#/"; };
const ob = $("#outBtn"), om = $("#outMenu");
ob.onclick = () => { om.hidden = !om.hidden; ob.setAttribute("aria-expanded", !om.hidden); };
document.addEventListener("click", e => { if (!e.target.closest(".menu")) om.hidden = true; });
om.querySelectorAll("button").forEach(b => b.onclick = () => {
  om.hidden = true;
  confirmUnreviewed(() => exportDocs(b.dataset.out));
});
async function exportDocs(kind) {
  const formats = kind === "xlsx" ? ["xlsx"] : kind === "md" ? ["md"] : ["md", "xlsx"];
  try {
    const r = await api("POST", `/api/projects/${S.project.id}/exports`, {formats, zip: kind === "zip"});
    const vers = r.results.map(x => `${x.doc_type} v${x.version}`).join(" / ");
    if (kind === "zip") { location.href = r.zip_url; toast(`zipを作りました(${vers})`); return; }
    if (kind === "folder") { await api("POST", `/api/projects/${S.project.id}/open-folder`); }
    toast(`書き出しました:${r.folder}`, kind === "folder" ? null : {label: "フォルダを開く", run: () => api("POST", `/api/projects/${S.project.id}/open-folder`)});
  } catch (e) { toast(e.message); }
}

route();
