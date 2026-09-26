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
    lastAuditPassed: false,     // 上次审计是否放行（复核的前置条件）
    lastReviewSignature: null,  // 上次成功复核时草稿的签名
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
    const sig = signature();
    if (state.lastAuditSignature !== null) {
      const stale = sig !== state.lastAuditSignature;
      $("stale-banner").classList.toggle("hidden", !stale);
      $("draft-hint").textContent = stale
        ? "草稿已修改，结论区显示的是旧结论，请重新提交审计。"
        : "";
    }
    // 草稿任意改动后，旧复核结果立即标为过期，不能作为新草稿的检修依据
    if (state.lastReviewSignature !== null) {
      const reviewStale = sig !== state.lastReviewSignature;
      $("review-stale-banner").classList.toggle("hidden", !reviewStale);
    }
  }

  function clearResult() {
    $("result-card").classList.add("hidden");
    $("reject-card").classList.add("hidden");
    $("stale-banner").classList.add("hidden");
    $("draft-hint").textContent = "";
    $("review-card").classList.add("hidden");
    $("review-stale-banner").classList.add("hidden");
    state.lastReviewSignature = null;
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
    state.lastAuditPassed = passed;
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
    state.lastAuditPassed = false;
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

  const CLASS_LABEL = {
    all: "全部最小割必经",
    some: "可替代瓶颈",
    never: "从不跨割",
  };
  const CLASS_TITLE = {
    all: "该情形下所有容量等于最大可导排量的源侧最小割都跨过这条管段，任何同容量瓶颈都绕不开它",
    some: "只出现在部分最小割中：单次最大流返回的割集可能偶然包含它，但存在不经过它的同容量瓶颈",
    never: "该情形下没有任何最小割跨过这条管段，与瓶颈无关",
  };

  function metricBox(label, value, cls) {
    const box = document.createElement("div");
    box.className = "metric";
    const l = document.createElement("span");
    l.className = "metric-label";
    l.textContent = label;
    const v = document.createElement("span");
    v.className = "metric-value" + (cls ? " " + cls : "");
    v.textContent = value;
    box.append(l, v);
    return box;
  }

  function reviewScenarioBlock(sc, idx) {
    const wrap = document.createElement("div");
    wrap.className = "review-scenario";

    const h3 = document.createElement("h3");
    if (sc.stage === "normal") {
      h3.textContent = `情形 ${idx + 1}：正常网络（无管段失效）`;
    } else {
      const r = sc.removed;
      const id = r.edge_id ? `（${r.edge_id}）` : "";
      h3.textContent = `情形 ${idx + 1}：第 ${r.position} 条管段${id} 临时失效（${r.from} → ${r.to}）`;
    }
    wrap.appendChild(h3);

    const metrics = document.createElement("div");
    metrics.className = "metric-row";
    metrics.append(
      metricBox("最大可导排量", fmt(sc.max_flow)),
      metricBox("最小割容量", fmt(sc.min_cut_capacity)),
      metricBox("事故要求", fmt(sc.required_flow)),
      metricBox("裕量（最大可导排量 − 事故要求）", fmt(sc.margin),
        sc.margin > 0 ? "meets-yes" : "meets-warn")
    );
    wrap.appendChild(metrics);

    const summary = document.createElement("p");
    summary.className = "tip";
    summary.textContent =
      `全部最小割必经 ${sc.class_counts.all} 条 · 可替代瓶颈（仅部分最小割）${sc.class_counts.some} 条 · 与瓶颈无关 ${sc.class_counts.never} 条`;
    wrap.appendChild(summary);

    // 管段分级表
    const scroll = document.createElement("div");
    scroll.className = "table-scroll";
    const table = document.createElement("table");
    table.className = "review-table";
    table.innerHTML =
      "<thead><tr><th>#</th><th>管段编号</th><th>管段</th><th>容量</th><th>薄弱分级</th></tr></thead>";
    const body = document.createElement("tbody");
    sc.edges.forEach((e) => {
      const tr = document.createElement("tr");
      if (e.class === "all") tr.className = "row-all";
      [String(e.position), e.id || "—", `${e.from} → ${e.to}`, fmt(e.capacity)]
        .forEach((c) => {
          const td = document.createElement("td");
          td.textContent = c;
          tr.appendChild(td);
        });
      const tdCls = document.createElement("td");
      const pill = document.createElement("span");
      pill.className = "pill " + e.class;
      pill.textContent = CLASS_LABEL[e.class] || e.class;
      pill.title = CLASS_TITLE[e.class] || "";
      tdCls.appendChild(pill);
      tr.appendChild(tdCls);
      body.appendChild(tr);
    });
    table.appendChild(body);
    scroll.appendChild(table);
    wrap.appendChild(scroll);

    // 可复核证据：全部最小割源侧的交集 / 并集
    const det = document.createElement("details");
    det.className = "cut-detail";
    const sum = document.createElement("summary");
    sum.textContent = "查看该情形全部最小割的源侧范围（交集 / 并集）";
    det.appendChild(sum);
    const grid = document.createElement("div");
    grid.className = "cut-grid";
    const d1 = document.createElement("div");
    const h1 = document.createElement("h3");
    h1.textContent = "源侧交集（任何最小割都含这些节点）";
    const c1 = document.createElement("div");
    c1.className = "chips";
    renderChips(c1, sc.min_source_side_nodes, "src");
    d1.append(h1, c1);
    const d2 = document.createElement("div");
    const h2 = document.createElement("h3");
    h2.textContent = "源侧并集（最小割源侧最大到此为止）";
    const c2 = document.createElement("div");
    c2.className = "chips";
    renderChips(c2, sc.max_source_side_nodes, "sink");
    d2.append(h2, c2);
    grid.append(d1, d2);
    det.appendChild(grid);
    wrap.appendChild(det);

    return wrap;
  }

  function renderReview(data) {
    const card = $("review-card");
    card.classList.remove("hidden");
    $("review-stale-banner").classList.add("hidden");

    const ok = !!data.reviewable;
    $("review-ok-panel").classList.toggle("hidden", !ok);
    $("review-fail-panel").classList.toggle("hidden", ok);

    if (!ok) {
      // 情形本已不达标：保留既有首条失败证据，不生成薄弱分级
      const f = data.failure || {};
      $("review-fail-edge").textContent = f.stage === "normal" ? "（正常网络本身）" : edgeLabel(f);
      $("review-fail-flow").textContent = fmt(f.max_flow);
      $("review-fail-required").textContent = fmt(f.required_flow);
      const cut = f.cut || { source_side_nodes: [], sink_side_nodes: [], cut_edges: [], capacity: null };
      renderChips($("review-cut-source-side"), cut.source_side_nodes, "src");
      renderChips($("review-cut-sink-side"), cut.sink_side_nodes, "sink");
      $("review-cut-edges-body").innerHTML = cutEdgeRows(cut, true);
      $("review-cut-capacity").textContent = fmt(cut.capacity);
    } else {
      const box = $("review-scenarios");
      box.innerHTML = "";
      (data.scenarios || []).forEach((sc, i) => box.appendChild(reviewScenarioBlock(sc, i)));
    }
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function submitReview() {
    // 复核必须基于“当前草稿刚通过审计”的状态；草稿改动后须先重新审计
    if (
      !state.lastAuditPassed ||
      state.lastAuditSignature === null ||
      signature() !== state.lastAuditSignature
    ) {
      $("review-hint").textContent = "草稿在审计后已被修改，请先重新提交审计并通过，再发起复核。";
      return;
    }
    const payload = currentPayload();
    $("btn-review").disabled = true;
    $("review-hint").textContent = "正在调用服务端复核 API…";
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
      $("review-hint").textContent = "审计已放行；可进一步识别在所有同容量瓶颈中均不可绕开的管段。";
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
    state.lastAuditPassed = false;
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
    state.lastAuditPassed = false;
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
  $("btn-example-pass").addEventListener("click", () => loadExample(EXAMPLE_PASS));
  $("btn-example-fail").addEventListener("click", () => loadExample(EXAMPLE_FAIL));
  $("btn-clear").addEventListener("click", clearAll);
  ["in-source", "in-sink", "in-required"].forEach((id) =>
    $(id).addEventListener("input", markDirty)
  );

  renderAll();
})();
