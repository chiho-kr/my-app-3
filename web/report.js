// 보고서 페이지: report.json(요약) + dong.geojson(행정동 지표) + gu.geojson(자치구 경계)
(async function () {
  const [R, dongGeo, guGeo] = await Promise.all(
    ["data/report.json", "data/dong.geojson", "data/gu.geojson"].map((u) => fetch(u).then((r) => r.json())));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const fmt = (d) => d3.format(",")(d);
  const tip = d3.select("#tip");
  const showTip = (ev, html) => {
    tip.html(html).style("display", "block");
    const w = tip.node().offsetWidth, h = tip.node().offsetHeight;
    let x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
    if (y + h > innerHeight - 8) y = ev.clientY - h - 14;
    tip.style("left", x + "px").style("top", y + "px");
  };
  const hideTip = () => tip.style("display", "none");
  const hhmm = (m) => { const t = (18 * 60 + m) % 1440; return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };
  const S = R.summary;

  // ---- KPI
  const pct = d3.format(".0%");
  d3.select("#kpis").html(`
    <div class="kpi" style="grid-column: span 2"><div class="v flow">${fmt(S.n_ph)}<span class="arrow">→</span>${fmt(S.open["2200"])}<span class="arrow">→</span>${S.open["0000"]}<span class="arrow">→</span>${S.open["0200"]}</div>
      <div class="l">서울 약국 전체 → 22시 → 0시 → 2시에 문 연 약국 수</div></div>
    <div class="kpi"><div class="v">${S.gap_popw.toFixed(1)}시간</div><div class="l">심야 공백 시간 (인구 가중 평균)<br>30분 안에 문 연 약국에 닿지 못하는 시간</div></div>
    <div class="kpi"><div class="v">${(S.pop_gap8 / 1e4).toFixed(1)}만 명</div><div class="l">공백 8시간 이상인 ${S.dong_gap8}개 동의 주민</div></div>`);

  // 차트 폭 = 컨테이너 폭 (그리기 전에 비운다)
  const widthOf = (el) => { el.selectAll("*").remove(); return el.node().clientWidth || 480; };

  // ---- 공용: 시간 축 선 그래프
  function lineChart(sel, series, { yMax, yFmt, yTicks }) {
    const el = d3.select(sel), W = widthOf(el), H = 220, m = { t: 8, r: 12, b: 26, l: 44 };
    const x = d3.scaleLinear([0, 720], [m.l, W - m.r]);
    const y = d3.scaleLinear([0, yMax], [H - m.b, m.t]).nice();
    const svg = el.append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("width", "100%");
    svg.append("g").attr("class", "gridline").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(yTicks || 4).tickSize(-(W - m.l - m.r)).tickFormat(""));
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`)
      .call(d3.axisBottom(x).tickValues(d3.range(0, 721, 120)).tickFormat(hhmm).tickSizeOuter(0));
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(yTicks || 4).tickFormat(yFmt).tickSize(0).tickPadding(6)).select(".domain").remove();
    for (const s of series)
      svg.append("path").datum(R.timeline).attr("fill", "none").attr("stroke", css(s.color)).attr("stroke-width", 2)
        .attr("stroke-linejoin", "round").attr("d", d3.line((d) => x(d.m), (d) => y(s.v(d))));
    const cross = svg.append("line").attr("stroke", css("--ink3")).attr("y1", m.t).attr("y2", H - m.b).style("display", "none");
    const dots = series.map((s) => svg.append("circle").attr("r", 4).attr("fill", css(s.color)).attr("stroke", css("--surface")).attr("stroke-width", 2).style("display", "none"));
    svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", W - m.l - m.r).attr("height", H - m.t - m.b).attr("fill", "transparent")
      .on("pointermove", (ev) => {
        const mx = x.invert(d3.pointer(ev)[0]), d = R.timeline[Math.max(0, Math.min(72, Math.round(mx / 10)))];
        cross.attr("x1", x(d.m)).attr("x2", x(d.m)).style("display", null);
        series.forEach((s, i) => dots[i].attr("cx", x(d.m)).attr("cy", y(s.v(d))).style("display", null));
        showTip(ev, `<b>${hhmm(d.m)}</b><br>` + series.map((s) => `${s.name} ${yFmt(s.v(d))}`).join("<br>"));
      })
      .on("pointerleave", () => { cross.style("display", "none"); dots.forEach((c) => c.style("display", "none")); hideTip(); });
  }
  const drawLines = () => {
    lineChart("#c-open", [{ name: "문 연 약국", v: (d) => d.open, color: "--accent" }], { yMax: 5000, yFmt: d3.format(",") });
    lineChart("#c-area", [{ name: "30분 안", v: (d) => d.p30, color: "--accent" }, { name: "15분 안", v: (d) => d.p15, color: "--warm" }],
      { yMax: 1, yFmt: d3.format(".0%") });
  };

  // ---- 행정동 지도 (d3-geo, 배경지도 없음)
  // d3는 바깥 고리가 시계 방향이어야 한다. 방향이 반대인 폴리곤은 지구 전체를 덮는 면적이 나오므로 뒤집는다
  const rewind = (f) => {
    const g = f.geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
    for (const p of polys)
      if (d3.geoArea({ type: "Polygon", coordinates: p }) > 2 * Math.PI) p.forEach((ring) => ring.reverse());
    return f;
  };
  dongGeo.features.forEach(rewind); guGeo.features.forEach(rewind);
  const dongs = dongGeo.features;
  const byCd = new Map(dongs.map((f) => [f.properties.adm_cd, f.properties]));

  const METRICS = {
    D_2200: { label: "밀도 22시 (인구 1만 명당 영업 약국)", f: d3.format(".2f"), ramp: "b", zero: true },
    D_0000: { label: "밀도 0시 (인구 1만 명당 영업 약국)", f: d3.format(".2f"), ramp: "b", zero: true },
    LQ_2200: { label: "특화도 LQ 22시", f: d3.format(".2f"), ramp: "b", zero: true },
    G_2200: { label: "중력 접근성 22시", f: d3.format(".2f"), ramp: "b" },
    G_0000: { label: "중력 접근성 0시", f: d3.format(".3f"), ramp: "b" },
    R_2200: { label: "유지율 22시 (G / G⁰)", f: d3.format(".1%"), ramp: "b" },
    gap_h: { label: "심야 공백 시간 (시간)", f: d3.format(".1f"), ramp: "o" },
    T_2200: { label: "22시 이동시간 중앙값 (분)", f: (v) => (v > 60 ? "60+" : d3.format(".0f")(v)), ramp: "o" },
    T_0000: { label: "0시 이동시간 중앙값 (분)", f: (v) => (v > 60 ? "60+" : d3.format(".0f")(v)), ramp: "o" },
    old_share: { label: "65세 이상 인구 비율", f: d3.format(".1%"), ramp: "o" },
  };
  const LEFT = ["D_2200", "LQ_2200", "G_2200", "G_0000", "R_2200", "D_0000"];
  const RIGHT = ["gap_h", "T_2200", "T_0000", "old_share"];
  let selected = null;
  const maps = [];

  function classes(key) {
    const meta = METRICS[key];
    const vals = dongs.map((f) => f.properties[key]).filter((v) => v != null && !(meta.zero && v === 0)).sort(d3.ascending);
    const br = [...new Set([0.2, 0.4, 0.6, 0.8].map((q) => d3.quantileSorted(vals, q)))];
    const cols = [1, 2, 3, 4, 5].map((i) => css(`--${meta.ramp}${i}`));
    const pal = br.length === 4 ? cols : cols.slice(5 - br.length - 1);
    const sc = d3.scaleThreshold(br, pal);
    return { sc, br, pal, min: vals[0], max: vals[vals.length - 1],
             color: (v) => (v == null ? css("--zero") : meta.zero && v === 0 ? css("--zero") : sc(v)) };
  }

  function makeMap(host, selId, rampId, opts, init) {
    const el = d3.select(host), W = 520, H = 430;
    const proj = d3.geoMercator().fitExtent([[6, 6], [W - 6, H - 6]], dongGeo);
    const path = d3.geoPath(proj);
    const svg = el.append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img");
    const g = svg.append("g");
    const ps = g.selectAll("path.dong").data(dongs).join("path").attr("class", "dong").attr("d", path)
      .on("pointermove", (ev, f) => {
        const p = f.properties, k = state.key;
        showTip(ev, `<b>${p.gu} ${p.dong}</b><br>${METRICS[k].label}<br><b>${p[k] == null ? "정의 안 됨" : METRICS[k].f(p[k])}</b>`);
      })
      .on("pointerleave", hideTip)
      .on("click", (ev, f) => select(f.properties.adm_cd));
    g.append("g").selectAll("path").data(guGeo.features).join("path").attr("class", "gu").attr("d", path);
    const selPath = g.append("path").attr("class", "sel");
    const state = { key: init };
    const sel = d3.select(selId);
    sel.selectAll("option").data(opts).join("option").attr("value", (k) => k).text((k) => METRICS[k].label);
    sel.property("value", init).on("change", (ev) => { state.key = ev.target.value; paint(); });
    function paint() {
      const meta = METRICS[state.key], c = classes(state.key);
      ps.attr("fill", (f) => c.color(f.properties[state.key]));
      const items = [];
      if (meta.zero) items.push({ col: css("--zero"), t: "0" });
      const edges = [c.min, ...c.br, c.max];
      c.pal.forEach((col, i) => items.push({ col, t: `${meta.f(edges[i])}–${meta.f(edges[i + 1])}` }));
      if (state.key.startsWith("LQ")) items.push({ col: css("--zero"), t: "" });
      d3.select(rampId).html(items.filter((d) => d.t).map((d) => `<div><span style="background:${d.col}"></span>${d.t}</div>`).join(""));
      if (selected) updateDetail();
    }
    paint();
    const m = { setSel: (f) => selPath.attr("d", f ? path(f) : null), state };
    maps.push(m);
    return m;
  }
  makeMap("#map1", "#m1", "#r1", LEFT, "D_2200");
  makeMap("#map2", "#m2", "#r2", RIGHT, "gap_h");

  const rankOf = (key, cd, asc) => {
    const vals = dongs.map((f) => f.properties[key]).filter((v) => v != null);
    const v = byCd.get(cd)[key];
    return v == null ? null : 1 + vals.filter((x) => (asc ? x < v : x > v)).length;
  };
  function updateDetail() {
    const p = byCd.get(selected);
    if (!p) return;
    const item = (lab, v) => `<span>${lab} <b>${v}</b></span>`;
    d3.select("#detail").html(`<b>${p.gu} ${p.dong}</b> <span class="small">인구 ${fmt(p.pop)}명 · 65세 이상 ${d3.format(".1%")(p.old_share)}</span>
      <div class="row">
        ${item("약국", p.N_all + "곳")}${item("22시 영업", p.N_2200 + "곳")}${item("0시 영업", p.N_0000 + "곳")}
        ${item("밀도 22시", METRICS.D_2200.f(p.D_2200))}${item("LQ 22시", p.LQ_2200 == null ? "—" : METRICS.LQ_2200.f(p.LQ_2200))}
        ${item("중력 0시", METRICS.G_0000.f(p.G_0000))}${item("유지율 22시", METRICS.R_2200.f(p.R_2200))}
        ${item("공백", METRICS.gap_h.f(p.gap_h) + "시간 (" + rankOf("gap_h", selected, true) + "/427, 짧은 순)")}
        ${item("0시 중앙값", METRICS.T_0000.f(p.T_0000) + "분")}
      </div>`);
  }
  function select(cd) {
    selected = selected === cd ? null : cd;
    const f = dongs.find((d) => d.properties.adm_cd === selected);
    maps.forEach((m) => m.setSel(f));
    markPts();
    if (selected) updateDetail();
    else d3.select("#detail").html('<span class="muted">지도에서 동을 누르면 지표 값이 여기에 나온다.</span>');
  }

  function markPts() {
    d3.selectAll(".pt").attr("stroke", css("--surface")).attr("stroke-width", 1).attr("r", 3.5);
    if (selected) d3.selectAll(".pt").filter((d) => d.adm_cd === selected).raise().attr("stroke", css("--hl")).attr("stroke-width", 2.5).attr("r", 6);
  }

  // ---- 자치구별 상자그림 (가로)
  function drawBox() {
    const props = dongs.map((f) => f.properties);
    const groups = d3.groups(props, (p) => p.gu).map(([gu, xs]) => {
      const v = xs.map((p) => p.gap_h).sort(d3.ascending);
      const q1 = d3.quantileSorted(v, 0.25), q2 = d3.quantileSorted(v, 0.5), q3 = d3.quantileSorted(v, 0.75), iqr = q3 - q1;
      return { gu, xs, q1, q2, q3, lo: d3.min(v.filter((x) => x >= q1 - 1.5 * iqr)), hi: d3.max(v.filter((x) => x <= q3 + 1.5 * iqr)) };
    }).sort((a, b) => a.q2 - b.q2);
    const el = d3.select("#c-box"), W = widthOf(el), rowH = 22, m = { t: 6, r: 14, b: 28, l: 64 };
    const H = m.t + m.b + rowH * groups.length;
    const x = d3.scaleLinear([0, 10], [m.l, W - m.r]);
    const y = d3.scaleBand(groups.map((g) => g.gu), [m.t, H - m.b]).padding(0.3);
    const svg = el.append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("width", "100%");
    svg.append("g").attr("class", "gridline").attr("transform", `translate(0,${H - m.b})`)
      .call(d3.axisBottom(x).ticks(5).tickSize(-(H - m.t - m.b)).tickFormat(""));
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`)
      .call(d3.axisBottom(x).ticks(5).tickFormat((v) => v + "시간").tickSizeOuter(0));
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).tickSize(0).tickPadding(8)).select(".domain").remove();
    const row = svg.selectAll("g.row").data(groups).join("g").attr("transform", (g) => `translate(0,${y(g.gu)})`);
    const bh = y.bandwidth(), ink2 = css("--ink2");
    row.append("line").attr("x1", (g) => x(g.lo)).attr("x2", (g) => x(g.hi)).attr("y1", bh / 2).attr("y2", bh / 2).attr("stroke", ink2);
    row.append("rect").attr("x", (g) => x(g.q1)).attr("width", (g) => Math.max(1, x(g.q3) - x(g.q1))).attr("height", bh).attr("rx", 3)
      .attr("fill", css("--o1")).attr("stroke", css("--o3"));
    row.append("line").attr("x1", (g) => x(g.q2)).attr("x2", (g) => x(g.q2)).attr("y2", bh).attr("stroke", css("--ink")).attr("stroke-width", 2);
    const rnd = d3.randomLcg(7);
    row.selectAll("circle").data((g) => g.xs.map((p) => ({ ...p, jy: (rnd() - 0.5) * bh * 0.8 })))
      .join("circle").attr("class", "pt").attr("cx", (p) => x(p.gap_h)).attr("cy", (p) => bh / 2 + p.jy).attr("r", 3.5)
      .attr("fill", css("--o4")).attr("fill-opacity", 0.75).attr("stroke", css("--surface")).attr("stroke-width", 1)
      .on("pointermove", (ev, p) => showTip(ev, `<b>${p.gu} ${p.dong}</b><br>공백 ${p.gap_h.toFixed(1)}시간`))
      .on("pointerleave", hideTip).on("click", (ev, p) => select(p.adm_cd)).style("cursor", "pointer");
  }

  // ---- 산점도
  function scatter(sel, xKey, xLabel, xScale, xFmt) {
    const props = dongs.map((f) => f.properties).filter((p) => p[xKey] > 0);
    const el = d3.select(sel), W = widthOf(el), H = 300, m = { t: 8, r: 12, b: 40, l: 44 };
    const x = xScale.domain(d3.extent(props, (p) => p[xKey])).range([m.l, W - m.r]).nice();
    const y = d3.scaleLinear([0, 10], [H - m.b, m.t]);
    const svg = el.append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("width", "100%");
    svg.append("g").attr("class", "gridline").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(5).tickSize(-(W - m.l - m.r)).tickFormat(""));
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(5, xFmt).tickSizeOuter(0));
    svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat((v) => v + "h").tickSize(0).tickPadding(6)).select(".domain").remove();
    svg.append("text").attr("x", W - m.r).attr("y", H - 6).attr("text-anchor", "end").attr("fill", css("--ink3")).attr("font-size", 11).text(xLabel);
    svg.append("text").attr("x", m.l).attr("y", m.t + 10).attr("dx", 6).attr("fill", css("--ink3")).attr("font-size", 11).text("심야 공백 시간");
    svg.append("g").selectAll("circle").data(props).join("circle").attr("class", "pt")
      .attr("cx", (p) => x(p[xKey])).attr("cy", (p) => y(p.gap_h)).attr("r", 3.5)
      .attr("fill", css("--accent")).attr("fill-opacity", 0.55).attr("stroke", css("--surface")).attr("stroke-width", 1)
      .style("cursor", "pointer")
      .on("pointermove", (ev, p) => showTip(ev, `<b>${p.gu} ${p.dong}</b><br>${xLabel} ${METRICS[xKey].f(p[xKey])}<br>공백 ${p.gap_h.toFixed(1)}시간`))
      .on("pointerleave", hideTip).on("click", (ev, p) => select(p.adm_cd));
  }
  const ci = (k) => R.corr.cols.indexOf(k);
  d3.select("#rho-old").text(R.corr.m[ci("old_share")][ci("gap_h")].toFixed(2));
  d3.select("#rho-g").text(R.corr.m[ci("G_0000")][ci("gap_h")].toFixed(2));
  const drawAll = () => {
    drawLines();
    drawBox();
    scatter("#c-old", "old_share", "65세 이상 비율", d3.scaleLinear(), ".0%");
    scatter("#c-g", "G_0000", "자정 중력 접근성", d3.scaleLog(), ".2");
    markPts();
  };
  drawAll();
  let lastW = innerWidth, timer = null;
  addEventListener("resize", () => {
    if (innerWidth === lastW) return;
    lastW = innerWidth; clearTimeout(timer); timer = setTimeout(drawAll, 150);
  });

  // ---- 상관 행렬
  (function () {
    const names = { D_2200: "밀도 22시", LQ_2200: "LQ 22시", G_0000: "중력 0시", R_2200: "유지율 22시", gap_h: "공백 시간", old_share: "65세+ 비율" };
    const col = d3.scaleLinear([-1, 0, 1], [css("--div-pos"), css("--div-mid"), css("--div-neg")]);
    const cols = R.corr.cols;
    let h = "<table><thead><tr><th></th>" + cols.map((c) => `<th>${names[c]}</th>`).join("") + "</tr></thead><tbody>";
    cols.forEach((r, i) => {
      h += `<tr><td>${names[r]}</td>` + cols.map((c, j) => {
        const v = R.corr.m[i][j];
        const bg = i === j ? "transparent" : col(v);
        const strong = Math.abs(v) > 0.55 && i !== j;
        return `<td style="background:${bg};text-align:center;${strong ? "color:#fff;font-weight:700" : ""}">${i === j ? "—" : v.toFixed(2)}</td>`;
      }).join("") + "</tr>";
    });
    d3.select("#c-corr").html(h + "</tbody></table>");
  })();

  // ---- 자치구 표 (정렬)
  (function () {
    const C = [
      { k: "gu", t: "자치구", f: (v) => v, l: true },
      { k: "pop", t: "인구", f: fmt },
      { k: "old_share", t: "65세+", f: d3.format(".1%") },
      { k: "N_all", t: "약국", f: fmt },
      { k: "N_2200", t: "22시 영업", f: fmt },
      { k: "N_0000", t: "0시 영업", f: fmt },
      { k: "D_2200", t: "밀도 22시", f: d3.format(".2f") },
      { k: "LQ_2200", t: "LQ 22시", f: d3.format(".2f") },
      { k: "G_0000", t: "중력 0시", f: d3.format(".2f") },
      { k: "R_2200", t: "유지율 22시", f: d3.format(".1%") },
      { k: "gap_h", t: "공백(시간)", f: d3.format(".1f") },
    ];
    let key = "gap_h", asc = true;
    const host = d3.select("#t-gu");
    function draw() {
      const rows = [...R.gu].sort((a, b) => (asc ? d3.ascending : d3.descending)(a[key], b[key]));
      host.html("<table><thead><tr>" + C.map((c) => `<th data-k="${c.k}" style="cursor:pointer${c.l ? ";text-align:left" : ""}">${c.t}${c.k === key ? (asc ? " ▲" : " ▼") : ""}</th>`).join("") +
        "</tr></thead><tbody>" + rows.map((r) => "<tr>" + C.map((c) => `<td${c.l ? ' class="tl"' : ""}>${c.f(r[c.k])}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
      host.selectAll("th").on("click", (ev) => { const k = ev.currentTarget.dataset.k; asc = k === key ? !asc : k === "gu"; key = k; draw(); });
    }
    draw();
  })();

  // ---- 민감도 표
  (function () {
    const s = R.sens;
    d3.select("#t-sens").html(`<table><thead><tr><th>지표</th><th class="tl">바꾼 설정 (기본값)</th><th>기본값과 순위 상관 ρ</th><th>공백 상위 10% 유지</th></tr></thead><tbody>
      ${Object.entries(s.G_0000).map(([k, v]) => `<tr><td>중력 접근성 0시</td><td class="tl">${k} (800m)</td><td>${v.toFixed(2)}</td><td>—</td></tr>`).join("")}
      ${Object.entries(s.gap_h).map(([k, v]) => `<tr><td>심야 공백 시간</td><td class="tl">${k} (30분)</td><td>${v.toFixed(2)}</td><td>${pct(s.gap_top10_keep[k])}</td></tr>`).join("")}
    </tbody></table>`);
  })();

  // ---- 사례 동
  const CASE_TEXT = {
    "명동": "22시에 서울 영업 약국의 15%(31곳)가 인구 2,465명인 이 동에 있다. 관광 상권의 밤 수요를 따라 생긴 공급이라 밀도·특화도 모두 1위다. 공백도 2.6시간으로 짧다(427개 동 중 25위).",
    "반포1동": "동 안에 밤 약국이 하나도 없는데(22시 0곳) 공백은 0시간이다. 맞붙은 논현1동의 24시간 약국까지 밤새 30분 안에 닿는다. 동 안 공급 지표로는 보이지 않는 접근성이다.",
    "목4동": "역시 밤 약국이 없는데 공백 0시간이다. 강서구 화곡2동의 24시간 약국까지 밤새 30분 안에 닿는다. 소수의 24시간 약국이 어디 있느냐가 새벽 접근성을 정한다.",
    "남현동": "22시에 동 안 약국 1곳이 열어 밀도 순위는 51위로 높은 편이다. 그러나 관악산 자락이라 자정에는 60분이 넘고, 공백은 8.5시간이다. 밀도와 공백이 엇갈리는 대표 사례다.",
    "도봉1동": "도봉산 입구 주거지. 약국이 닫기 전인 22시에도 중앙값 51분이 걸리고, 공백이 10.0시간으로 서울에서 가장 길다. 65세 이상 비율도 35.9%로 높다.",
    "방화3동": "김포공항과 개화산 사이. 65세 이상이 30.5%인데 자정 중앙값 41분, 공백 9.0시간이다. 같은 강서구에 24시간 약국(화곡2동)이 있어도 구 안 격차가 크다.",
  };
  d3.select("#cases").html(R.cases.map((c) => `<div class="card" data-cd="${c.adm_cd}" style="cursor:pointer">
      <div class="why">${c.why}</div><h4>${c.gu} ${c.dong}</h4>
      <div class="stats"><span>22시 영업 <b>${c.N_2200}곳</b></span><span>공백 <b>${c.gap_h.toFixed(1)}시간</b></span>
        <span>밀도 순위 <b>${c.rank_D}위</b></span><span>공백 순위 <b>${c.rank_gap}위</b></span></div>
      <p>${CASE_TEXT[c.dong] || ""}</p></div>`).join(""));
  d3.selectAll("#cases .card").on("click", (ev) => {
    select(ev.currentTarget.dataset.cd);
    document.getElementById("map1").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  d3.select("#cases").append("p").attr("class", "small").style("grid-column", "1/-1")
    .text("순위는 427개 동 기준. 밀도 순위는 높은 순(0인 동은 공동 순위), 공백 순위는 짧은 순. 카드를 누르면 지도에서 표시된다.");
})();
