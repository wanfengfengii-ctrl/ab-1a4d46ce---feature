/* 事故导排网络检修审计 —— 前端逻辑
 *
 * 职责边界：前端只负责录入草稿与展示**服务端业务 API** 返回的结论，
 * 不在本地计算任何最大流 / 判定。草稿一旦在上次审计后被改动，旧结论
 * 立即标记为过期，不会被当作当前草稿的结果。
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    nodes: [],          // 汇合节点名称（不含源/汇）
    edges: [],          // {id, from, to, capacity, maintainable}
    lastAuditSignature: null,   // 上次成功提交审计时草稿的签名
    lastReviewSignature: null,  // 上次成功提交复核时草稿的签名
  };

  /* ---------------- 示例数据 ---------------- */

  // 达标示例：两条 100 容量干线在源/汇之间并联，要求 95，
  // 任一可检修管段失效后仍至少剩 100 容量。
  const EXAMPLE_PASS = {
    source: "泄压源V-101",
    sink: "焚烧炉F-1",
    required_flow: 95,
    nodes: ["汇合点A", "汇合点B"],
    edges: [
      { id: "E1", from: "泄压源V-101", to: "汇合点A", capacity: 100, maintainable: true },
      { id: "E2", from: "汇合点A", to: "焚烧炉F-1", capacity: 100, maintainable: true },
      { id: "E3", from: "泄压源V-101", to: "汇合点B", capacity: 100, maintainable: true },
      { id: "E4", from: "汇合点B", to: "焚烧炉F-1", capacity: 100, maintainable: true },
      { id: "E5", from: "汇合点A", to: "汇合点B", capacity: 40, maintainable: false },
    ],
  };

  // 失效示例：正常网络最大流 100，但首条可检修管段 E1 失效后
  // 上干线路径中断，仅剩 90，低于要求 95。
  const EXAMPLE_FAIL = {
    source: "泄压源V-101",
    sink: "焚烧炉F-1",
    required_flow: 95,
    nodes: ["汇合点A", "汇合点B"],
    edges: [
      { id: "E1", from: "泄压源V-101", to: "汇合点A", capacity: 100, maintainable: true },
      { id: "E2", from: "汇合点A", to: "焚烧炉F-1", capacity: 100, maintainable: true },
      { id: "E3", from: "泄压源V-101", to: "汇合点B", capacity: 90, maintainable: true },
      { id: "E4", from: "汇合点B", to: "焚烧炉F-1", capacity: 90, maintainable: true },
    ],
  };

  /* ---------------- 草稿渲染 ---------------- */

  function renderNodes() {
    const box = $("nodes-list");
    box.innerHTML = "";
    if (state.nodes.length === 0) {
      box.innerHTML = '<span class="tip">尚无汇合节点，点击右上方按钮添加。</span>';
      return;
    }
    state.nodes.forEach((name, i) => {
      const chip = document.createElement("span");
      chip.className = "node-chip";
      const input = document.createElement("input");
      input.type = "text";
      input.value = name;
      input.placeholder = "节点名称";
      input.addEventListener("input", () => { state.nodes[i] = input.value; markDirty(); });
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "×";
      del.title = "删除该汇合节点";
      del.addEventListener("click", () => { state.nodes.splice(i, 1); renderAll(); markDirty(); });
      chip.append(input, del);
      box.appendChild(chip);
    });
  }

  function renderEdges() {
    const body = $("edges-body");
    body.innerHTML = "";
    state.edges.forEach((edge, i) => {
      const tr = document.createElement("tr");

      const tdNo = document.createElement("td");
      tdNo.textContent = String(i + 1);

      const tdId = document.createElement("td");
      const idInput = document.createElement("input");
      idInput.type = "text";
      idInput.value = edge.id || "";
      idInput.placeholder = "选填";
      idInput.addEventListener("input", () => { edge.id = idInput.value; markDirty(); });
      tdId.appendChild(idInput);

      const tdFrom = document.createElement("td");
      const fromInput = document.createElement("input");
      fromInput.type = "text";
      fromInput.value = edge.from;
      fromInput.placeholder = "起点节点";
      fromInput.addEventListener("input", () => { edge.from = fromInput.value; markDirty(); });
      tdFrom.appendChild(fromInput);

      const tdArrow = document.createElement("td");
      tdArrow.className = "arrow";
      tdArrow.textContent = "→";

      const tdTo = document.createElement("td");
      const toInput = document.createElement("input");
      toInput.type = "text";
      toInput.value = edge.to;
      toInput.placeholder = "终点节点";
      toInput.addEventListener("input", () => { edge.to = toInput.value; markDirty(); });
      tdTo.appendChild(toInput);

      const tdCap = document.createElement("td");
      const capInput = document.createElement("input");
      capInput.type = "number";
      capInput.min = "0";
      capInput.step = "any";
      capInput.value = edge.capacity;
      capInput.addEventListener("input", () => { edge.capacity = capInput.value; markDirty(); });
      tdCap.appendChild(capInput);

      const tdMaint = document.createElement("td");
      tdMaint.className = "center";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!edge.maintainable;
      cb.title = "勾选后参与“单管段临时失效”模拟";
      cb.addEventListener("change", () => { edge.maintainable = cb.checked; markDirty(); });
      tdMaint.appendChild(cb);

      const tdDel = document.createElement("td");
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "row-del";
      delBtn.textContent = "×";
      delBtn.title = "删除该管段";
      delBtn.addEventListener("click", () => { state.edges.splice(i, 1); renderEdges(); markDirty(); });
      tdDel.appendChild(delBtn);

      tr.append(tdNo, tdId, tdFrom, tdArrow, tdTo, tdCap, tdMaint, tdDel);
      body.appendChild(tr);
    });
  }

  function renderAll() {
    renderNodes();
    renderEdges();
  }

  /* ---------------- 草稿状态 / 旧结论过期 ---------------- */

  function currentPayload() {
    const required = $("in-required").value;
    return {
      source: $("in-source").value.trim(),
      sink: $("in-sink").value.trim(),
      required_flow: required === "" ? null : Number(required),
      nodes: state.nodes.map((n) => n.trim()).filter((n) => n.length),
      edges: state.edges.map((e) => ({
        id: (e.id || "").trim() || null,
        from: (e.from || "").trim(),
        to: (e.to || "").trim(),
        capacity: e.capacity === "" || e.capacity === null ? null : Number(e.capacity),
        maintainable: !!e.maintainable,
      })),
    };
  }

  // 用稳定签名判断“草稿是否在上次审计后变化”
  function signature() {
    return JSON.stringify(currentPayload());
  }

  function markDirty() {
    if (state.lastAuditSignature !== null) {
      const stale = signature() !== state.lastAuditSignature;
      $("stale-banner").classList.toggle("hidden", !stale);
      $("draft-hint").textContent = stale
        ? "草稿已修改，结论区显示的是旧结论，请重新提交审计。"
        : "";
    }
    // 草稿任意改动后，旧复核结果立即标为过期，不能作为新草稿的检修依据
    if (state.lastReviewSignature !== null) {
      const stale = signature() !== state.lastReviewSignature;
      $("review-stale-banner").classList.toggle("hidden", !stale);
    }
  }

  function clearResult() {
    $("result-card").classList.add("hidden");
    $("reject-card").classList.add("hidden");
    $("review-card").classList.add("hidden");
    $("stale-banner").classList.add("hidden");
    $("review-stale-banner").classList.add("hidden");
    $("draft-hint").textContent = "";
  }

  /* ---------------- 结论渲染 ---------------- */

  function fmt(n) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString("zh-CN", { maximumFractionDigits: 6 });
  }

  function edgeLabel(e) {
    const id = e.edge_id ? `（${e.edge_id}）` : "";
    return `第 ${e.position} 条管段${id}：${e.from} → ${e.to}`;
  }

  function renderChips(el, names, cls) {
    el.innerHTML = "";
    names.forEach((n) => {
      const c = document.createElement("span");
      c.className = "chip " + (cls || "");
      c.textContent = n;
      el.appendChild(c);
    });
    if (names.length === 0) {
      const c = document.createElement("span");
      c.className = "chip empty";
      c.textContent = "（无）";
      el.appendChild(c);
    }
  }

  function cutEdgeRows(cut, withSides) {
    return cut.cut_edges.map((e) => {
      const cells = withSides
        ? [e.position, e.id || "—", e.from, "→", e.to, fmt(e.capacity)]
        : [e.position, e.id || "—", `${e.from} → ${e.to}`, fmt(e.capacity)];
      return "<tr>" + cells.map((c) => `<td>${c}</td>`).join("") + "</tr>";
    }).join("");
  }

  function renderResult(data) {
    const card = $("result-card");
    card.classList.remove("hidden");
    $("reject-card").classList.add("hidden");
    $("stale-banner").classList.add("hidden");
    $("draft-hint").textContent = "";

    const passed = !!data.passed;
    $("pass-panel").classList.toggle("hidden", !passed);
    $("fail-panel").classList.toggle("hidden", passed);

    if (passed) {
      $("pass-scenario-count").textContent = String(data.scenarios.length);
      $("pass-required").textContent = fmt(data.required_flow);
    } else {
      const f = data.failure;
      const isNormal = f.stage === "normal";
      $("fail-edge").textContent = isNormal
        ? "（正常网络本身）"
        : edgeLabel(f);
      $("fail-flow").textContent = fmt(f.max_flow);
      $("fail-required").textContent = fmt(f.required_flow);

      const cut = f.cut;
      renderChips($("cut-source-side"), cut.source_side_nodes, "src");
      renderChips($("cut-sink-side"), cut.sink_side_nodes, "sink");
      $("cut-edges-body").innerHTML = cutEdgeRows(cut, true);
      $("cut-capacity").textContent = fmt(cut.capacity);
    }

    // 正常网络
    const normal = data.normal;
    $("normal-flow").textContent = fmt(normal.max_flow);
    $("normal-required").textContent = fmt(data.required_flow);
    const meetsEl = $("normal-meets");
    meetsEl.textContent = normal.meets ? "达标" : "不达标";
    meetsEl.className = "metric-value " + (normal.meets ? "meets-yes" : "meets-no");
    renderChips($("normal-cut-source"), normal.cut.source_side_nodes, "src");
    renderChips($("normal-cut-sink"), normal.cut.sink_side_nodes, "sink");
    $("normal-cut-edges").innerHTML = cutEdgeRows(normal.cut, false);
    $("normal-cut-capacity").textContent = fmt(normal.cut.capacity);

    // 逐条失效
    const body = $("scenarios-body");
    body.innerHTML = "";
    if (data.scenarios.length === 0) {
      body.innerHTML = '<tr><td colspan="7" class="center tip">没有勾选“可检修”的管段，未模拟单段失效；仅审计正常网络。</td></tr>';
    } else {
      const failIdx = data.failure && !passed ? data.failure.edge_index : null;
      data.scenarios.forEach((s) => {
        const tr = document.createElement("tr");
        if (s.edge_index === failIdx) tr.className = "row-fail";
        const cells = [
          String(s.position),
          (s.edge_id ? s.edge_id + " " : "") + `${s.from} → ${s.to}`,
          "→",
          fmt(s.capacity),
          fmt(s.max_flow),
          fmt(data.required_flow),
        ];
        cells.forEach((c) => { const td = document.createElement("td"); td.textContent = c; tr.appendChild(td); });
        const tdJudge = document.createElement("td");
        const pill = document.createElement("span");
        pill.className = "pill " + (s.meets ? "yes" : "no");
        pill.textContent = s.meets ? "达标" : "不达标";
        tdJudge.appendChild(pill);
        tr.appendChild(tdJudge);
        body.appendChild(tr);
      });
    }

    const nonMaint = state.edges.filter((e) => !e.maintainable).length;
    const note = $("non-maintainable-note");
    if (nonMaint > 0) {
      note.classList.remove("hidden");
      note.textContent = `另有 ${nonMaint} 条未勾选“可检修”的管段，不参与单段临时失效模拟（视为事故期间保持投用）。`;
    } else {
      note.classList.add("hidden");
    }

    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderRejection(err) {
    $("result-card").classList.add("hidden");
    const card = $("reject-card");
    card.classList.remove("hidden");
    $("reject-msg").textContent = err || "输入无效。";
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ---------------- 提交审计（真实业务 API） ---------------- */

  async function submitAudit() {
    const payload = currentPayload();
    $("btn-audit").disabled = true;
    $("draft-hint").textContent = "正在调用服务端审计 API…";
    try {
      const resp = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        renderRejection(data.error || `审计请求失败（HTTP ${resp.status}）`);
        state.lastAuditSignature = null;
        return;
      }
      state.lastAuditSignature = signature();
      renderResult(data);
    } catch (e) {
      renderRejection("无法连接审计服务：" + e.message);
      state.lastAuditSignature = null;
    } finally {
      $("btn-audit").disabled = false;
      if ($("draft-hint").textContent.startsWith("正在")) $("draft-hint").textContent = "";
    }
  }

  /* ---------------- 薄弱管段复核（真实业务 API） ---------------- */

  function scenarioTitle(sc) {
    if (sc.stage === "normal") return "正常网络（无管段失效）";
    const r = sc.removed;
    const id = r.edge_id ? `（${r.edge_id}）` : "";
    return `第 ${r.position} 条管段${id} ${r.from} → ${r.to} 临时失效`;
  }

  function gradedEdgeRow(g, tagText, tagCls) {
    const id = g.edge_id || "—";
    const accidental = g.in_returned_cut && g.crosses !== "all"
      ? '<span class="tag accidental">单次割集偶然包含</span>'
      : "";
    const tag = tagText ? `<span class="tag ${tagCls}">${tagText}</span>` : "";
    return `<tr><td>${g.position}</td><td>${id}</td>` +
      `<td>${g.from} → ${g.to}</td><td>${fmt(g.capacity)}</td>` +
      `<td>${tag}${accidental}</td></tr>`;
  }

  function gradeTable(rows) {
    return '<div class="table-scroll"><table class="cut-table grade-table">' +
      "<thead><tr><th>#</th><th>管段编号</th><th>管段</th><th>容量</th><th>标记</th></tr></thead>" +
      `<tbody>${rows}</tbody></table></div>`;
  }

  function emptyGradeNote(text) {
    return `<p class="tip grade-empty">${text}</p>`;
  }

  function renderReviewScenario(sc, required) {
    const wrap = document.createElement("div");
    wrap.className = "review-scenario";

    const edges = sc.edges || [];
    const all = edges.filter((g) => g.crosses === "all");
    const some = edges.filter((g) => g.crosses === "some");
    const never = edges.filter((g) => g.crosses === "never");

    const allRows = all.map((g) => gradedEdgeRow(g, "所有同容量瓶颈必经", "all")).join("");
    const someRows = some.map((g) => gradedEdgeRow(g, "可替代瓶颈", "some")).join("");
    const neverRows = never.map((g) => gradedEdgeRow(g, "", "")).join("");

    wrap.innerHTML =
      `<h3>${scenarioTitle(sc)}</h3>` +
      '<div class="metric-row">' +
        `<div class="metric"><span class="metric-label">最大可导排量</span><span class="metric-value">${fmt(sc.max_flow)}</span></div>` +
        `<div class="metric"><span class="metric-label">最小割容量</span><span class="metric-value">${fmt(sc.min_cut_capacity)}</span></div>` +
        `<div class="metric"><span class="metric-label">事故要求流量</span><span class="metric-value">${fmt(required)}</span></div>` +
        `<div class="metric"><span class="metric-label">导排裕量</span><span class="metric-value meets-yes">${fmt(sc.margin)}</span></div>` +
      "</div>" +
      '<div class="grade-block grade-all">' +
        `<h4>全部最小割均跨越 —— 不可绕开的管段（${all.length} 条）</h4>` +
        (all.length ? gradeTable(allRows)
                    : emptyGradeNote("本情形没有所有同容量瓶颈都不可绕开的管段。")) +
      "</div>" +
      '<div class="grade-block grade-some">' +
        `<h4>仅部分最小割跨越 —— 可替代瓶颈（${some.length} 条）</h4>` +
        (some.length ? gradeTable(someRows)
                     : emptyGradeNote("本情形没有仅在部分最小割中出现的管段。")) +
      "</div>" +
      '<details class="grade-block grade-never">' +
        `<summary>从不跨割 —— 非瓶颈管段（${never.length} 条，点击展开）</summary>` +
        (never.length ? gradeTable(neverRows) : "") +
      "</details>";
    return wrap;
  }

  function renderReview(data) {
    const card = $("review-card");
    card.classList.remove("hidden");
    $("reject-card").classList.add("hidden");
    $("review-stale-banner").classList.add("hidden");
    $("draft-hint").textContent = "";

    const passed = !!data.passed;
    $("review-pass-panel").classList.toggle("hidden", !passed);
    $("review-fail-panel").classList.toggle("hidden", passed);

    if (passed) {
      const box = $("review-scenarios");
      box.innerHTML = "";
      data.scenarios.forEach((sc) => box.appendChild(renderReviewScenario(sc, data.required_flow)));
    } else {
      // 保留既有首条失败证据；不生成任何薄弱分级
      const f = data.failure;
      $("review-fail-edge").textContent = f.stage === "normal" ? "（正常网络本身）" : edgeLabel(f);
      $("review-fail-flow").textContent = fmt(f.max_flow);
      $("review-fail-required").textContent = fmt(f.required_flow);
      const cut = f.cut;
      renderChips($("review-cut-source-side"), cut.source_side_nodes, "src");
      renderChips($("review-cut-sink-side"), cut.sink_side_nodes, "sink");
      $("review-cut-edges-body").innerHTML = cutEdgeRows(cut, true);
      $("review-cut-capacity").textContent = fmt(cut.capacity);

      const body = $("review-fail-scenarios-body");
      body.innerHTML = "";
      data.scenarios.forEach((sc) => {
        const tr = document.createElement("tr");
        if (sc.stage !== "normal" && f.stage === "single_failure" &&
            sc.removed && sc.removed.edge_index === f.edge_index) {
          tr.className = "row-fail";
        }
        [scenarioTitle(sc), fmt(sc.max_flow), fmt(data.required_flow)].forEach((c) => {
          const td = document.createElement("td");
          td.textContent = c;
          tr.appendChild(td);
        });
        const tdJudge = document.createElement("td");
        const pill = document.createElement("span");
        pill.className = "pill " + (sc.meets ? "yes" : "no");
        pill.textContent = sc.meets ? "达标" : "不达标";
        tdJudge.appendChild(pill);
        tr.appendChild(tdJudge);
        body.appendChild(tr);
      });
    }

    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function submitReview() {
    const payload = currentPayload();
    $("btn-review").disabled = true;
    $("btn-review-rerun").disabled = true;
    $("draft-hint").textContent = "正在调用服务端复核 API…";
    try {
      const resp = await fetch("/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        renderRejection(data.error || `复核请求失败（HTTP ${resp.status}）`);
        state.lastReviewSignature = null;
        return;
      }
      state.lastReviewSignature = signature();
      renderReview(data);
    } catch (e) {
      renderRejection("无法连接复核服务：" + e.message);
      state.lastReviewSignature = null;
    } finally {
      $("btn-review").disabled = false;
      $("btn-review-rerun").disabled = false;
      if ($("draft-hint").textContent.startsWith("正在")) $("draft-hint").textContent = "";
    }
  }

  /* ---------------- 载入 / 清空 ---------------- */

  function loadExample(ex) {
    clearResult();
    $("in-source").value = ex.source;
    $("in-sink").value = ex.sink;
    $("in-required").value = String(ex.required_flow);
    state.nodes = ex.nodes.slice();
    state.edges = ex.edges.map((e) => ({ ...e }));
    state.lastAuditSignature = null;
    state.lastReviewSignature = null;
    renderAll();
  }

  function clearAll() {
    clearResult();
    $("in-source").value = "";
    $("in-sink").value = "";
    $("in-required").value = "";
    state.nodes = [];
    state.edges = [];
    state.lastAuditSignature = null;
    state.lastReviewSignature = null;
    renderAll();
  }

  /* ---------------- 事件绑定 ---------------- */

  $("btn-add-node").addEventListener("click", () => {
    state.nodes.push("");
    renderNodes();
    markDirty();
    const inputs = $("nodes-list").querySelectorAll("input");
    if (inputs.length) inputs[inputs.length - 1].focus();
  });

  $("btn-add-edge").addEventListener("click", () => {
    state.edges.push({ id: "", from: "", to: "", capacity: "", maintainable: true });
    renderEdges();
    markDirty();
  });

  $("btn-audit").addEventListener("click", submitAudit);
  $("btn-review").addEventListener("click", submitReview);
  $("btn-review-rerun").addEventListener("click", submitReview);
  $("btn-example-pass").addEventListener("click", () => loadExample(EXAMPLE_PASS));
  $("btn-example-fail").addEventListener("click", () => loadExample(EXAMPLE_FAIL));
  $("btn-clear").addEventListener("click", clearAll);
  ["in-source", "in-sink", "in-required"].forEach((id) =>
    $(id).addEventListener("input", markDirty)
  );

  renderAll();
})();
