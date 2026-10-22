// 서울 심야 약국 지도 — 격자별 '가장 가까운 영업 약국까지 분'을 시간·공간 보간해 등시간 띠로 그린다.
(async function () {
  const $ = (id) => document.getElementById(id);
  const BAND_COLORS = ["--b1", "--b2", "--b3", "--b4", "--b5"].map((v) =>
    getComputedStyle(document.documentElement).getPropertyValue(v).trim());
  const rgb = BAND_COLORS.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)));
  const FAR = 72;          // 60분 초과(255)를 보간할 때 쓰는 값

  const status = (s) => { $("status").textContent = s; };
  let meta, frames, nearest, mask, ph;
  try {
    const get = (p) => fetch("data/" + p).then((r) => { if (!r.ok) throw new Error(p + " " + r.status); return r; });
    meta = await (await get("meta.json")).json();
    frames = new Uint8Array(await (await get("frames.bin")).arrayBuffer());
    nearest = new Int16Array(await (await get("nearest.bin")).arrayBuffer());
    mask = new Uint8Array(await (await get("mask.bin")).arrayBuffer());
    ph = await (await get("pharmacies.json")).json();
  } catch (e) {
    status("데이터를 불러오지 못했습니다 (" + e.message + "). web/data 폴더가 같이 배포됐는지 확인하세요.");
    return;
  }
  const { rows, cols, times, bands } = meta;
  const N = rows * cols;
  const maxMin = 720;   // 18:00~06:00. 아직 계산 안 된 시점은 마지막 계산 시점 값으로 보인다
  $("slider").max = maxMin;
  if (times.length < meta.n_total_steps) status(`계산된 시점 ${times.length}/${meta.n_total_steps}개 (계산 중인 데이터)`);
  else status("");

  // ---- 범례
  const labels = meta.bands.map((b) => `${b}분 안`);
  $("legend").innerHTML = labels.map((l, i) =>
    `<div class="band"><span class="sw" style="background:${BAND_COLORS[i]}"></span><span class="k">${l}</span><span class="v" id="bv${i}">–</span></div>`).join("");

  // ---- 시간 보간: 분 m에서 격자값
  const cur = new Float32Array(N);
  function bracket(m) {
    let k = 0;
    while (k < times.length - 1 && times[k + 1] <= m) k++;
    const k2 = Math.min(k + 1, times.length - 1);
    const a = k2 === k ? 0 : (m - times[k]) / (times[k2] - times[k]);
    return [k, k2, Math.max(0, Math.min(1, a))];
  }
  function fillCur(m) {
    const [k, k2, a] = bracket(m);
    const A = k * N, B = k2 * N;
    for (let i = 0; i < N; i++) {
      let x = frames[A + i], y = frames[B + i];
      x = x === 255 ? FAR : x; y = y === 255 ? FAR : y;
      cur[i] = x + (y - x) * a;
    }
    return a < 0.5 ? k : k2;
  }

  // ---- 캔버스: 등시간 경계를 벡터 다각형(marching squares, d3-contour)으로 구해 고해상도로 그린다.
  // 격자값을 픽셀로 늘려 칠하면 확대 시 계단이 보이므로, 칸 사이를 보간한 경계선을 브라우저 안티앨리어싱으로 그린다.
  const UPC = 12;                                   // 격자 1칸 → 캔버스 12px
  const W = cols * UPC, H = rows * UPC;
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  // d3-contour 입력: 위(북)에서 아래로 행 순서, "이하" 영역을 구하려고 부호를 뒤집는다. 서울 밖 칸은 매우 큰 값.
  const OUTSIDE = 9999;
  const grid = new Float64Array(N);
  const contours = d3.contours().size([cols, rows]).smooth(true);
  const FILL = [...BAND_COLORS, "rgba(90,98,122,0.37)"];   // 띠 색 5개 + 30분 초과(서울 안 회색)
  const BAND_ALPHA = 0.75;
  function tracePath(geom) {
    ctx.beginPath();
    for (const poly of geom.coordinates) for (const ring of poly) {
      ring.forEach(([x, y], k) => (k ? ctx.lineTo(x * UPC, y * UPC) : ctx.moveTo(x * UPC, y * UPC)));
      ctx.closePath();
    }
  }
  function draw() {
    for (let rowTop = 0; rowTop < rows; rowTop++) {
      const src = (rows - 1 - rowTop) * cols, dst = rowTop * cols;
      for (let c = 0; c < cols; c++) grid[dst + c] = mask[src + c] === 2 ? -cur[src + c] : -OUTSIDE;
    }
    ctx.clearRect(0, 0, W, H);
    // 서울 영역 전체(= 30분 초과 회색 바탕), 그 위에 30·15·10·5·3분 이하 영역을 바깥부터 덮는다
    const levels = [-OUTSIDE + 1, ...[...bands].reverse().map((b) => -b)];
    const geoms = contours.thresholds(levels)(grid);
    geoms.forEach((g, k) => {
      if (!g.coordinates.length) return;
      tracePath(g);
      ctx.globalCompositeOperation = "destination-out";   // 아래 띠를 지우고 (반투명 색이 겹쳐 진해지지 않게)
      ctx.globalAlpha = 1; ctx.fillStyle = "#000"; ctx.fill("evenodd");
      ctx.globalCompositeOperation = "source-over";
      const b = k === 0 ? 5 : bands.length - k;            // k=0 회색, 1→30분(띠 4), …, 5→3분(띠 0)
      ctx.globalAlpha = b === 5 ? 1 : BAND_ALPHA;
      ctx.fillStyle = FILL[b]; ctx.fill("evenodd");
      if (k > 0) {                                         // 띠 경계선
        ctx.globalAlpha = 0.55; ctx.strokeStyle = "#0b1020"; ctx.lineWidth = 1.6; ctx.lineJoin = "round"; ctx.stroke();
      }
    });
    ctx.globalAlpha = 1;
    pushCanvas();
  }
  // 캔버스는 바뀔 때만 지도 텍스처로 올린다 (animate: true는 매 프레임 올려 무겁다)
  let pauseTimer = null;
  function pushCanvas() {
    const s = map.getSource("access");   // draw()는 지도 생성 뒤에만 불린다
    if (!s) return;
    s.play(); map.triggerRepaint();
    clearTimeout(pauseTimer); pauseTimer = setTimeout(() => s.pause(), 80);
  }

  // ---- 지도
  const map = new maplibregl.Map({
    container: "map",
    style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    center: [126.985, 37.56], zoom: 10.4, minZoom: 9, maxZoom: 15,
    attributionControl: { compact: true, customAttribution: "약국: E-Gen · 대중교통: KTDB GTFS · 경로: MOTIS" },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  // 데스크톱에선 왼쪽 패널 폭만큼 지도 중심을 오른쪽으로
  const SEOUL_BOUNDS = [[126.764, 37.413], [127.184, 37.715]];
  const fitSeoul = () => map.fitBounds(SEOUL_BOUNDS, { animate: false,
    padding: window.innerWidth > 560 ? { left: 390, top: 24, right: 24, bottom: 24 } : { left: 12, top: 12, right: 12, bottom: 280 } });
  fitSeoul();

  const phFeatures = ph.map((p, i) => ({ type: "Feature", id: i,
    geometry: { type: "Point", coordinates: [p[0], p[1]] },
    properties: { o: p[2] ?? 99999, c: p[3] ?? -99999, o2: p[6] ?? 99999, c2: p[7] ?? -99999, name: p[5] || "" } }));

  // 공식 노선색 → 채도 55%로 낮추고 밝기를 0.50~0.68에 맞춘 색 (등시간 띠와 경쟁하지 않게)
  function soften(hex) {
    const r = parseInt(hex.slice(1, 3), 16) / 255, g = parseInt(hex.slice(3, 5), 16) / 255, b = parseInt(hex.slice(5, 7), 16) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn;
    let h = 0, s = 0;
    if (d) {
      s = d / (1 - Math.abs(2 * l - 1));
      h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
      h *= 60; if (h < 0) h += 360;
    }
    const S = s * 0.55, L = Math.min(0.68, Math.max(0.5, l));
    return `hsl(${Math.round(h)}, ${Math.round(S * 100)}%, ${Math.round(L * 100)}%)`;
  }
  const EXPRESS_RED = soften("#E60012");   // 광역버스 공식 빨강
  let routeLayers = null, routeFeatures = null;   // 노선 레이어 정의·피처 (map load 후 채움)

  // 노선 paint: 운행 대수 n(feature-state)이 0이면 투명, w가 있으면 n에 따라 굵기
  const nState = ["coalesce", ["feature-state", "n"], 0];
  function routePaint(L, hi) {
    const p = { ...(hi ? L.hi : L.base) };
    p["line-opacity"] = ["case", [">", nState, 0], p["line-opacity"], 0];
    const w = hi ? L.wHi : L.w;
    if (w) p["line-width"] = ["interpolate", ["linear"], nState, 0, 0, w[0], w[1], w[2], w[3]];
    else if (p["line-width"] === undefined) p["line-width"] = 1;
    return p;
  }

  map.on("load", async () => {
    const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
    // 배경지도의 도로·철도선을 어둡게 낮춰 버스·전철 노선과 섞이지 않게
    for (const l of map.getStyle().layers) {
      if (l.type === "line" && /^(road|tunnel|bridge|rail)/.test(l.id)) map.setPaintProperty(l.id, "line-opacity", 0.35);
    }
    map.addSource("access", { type: "canvas", canvas, coordinates: meta.corners, animate: false });
    map.addLayer({ id: "access", type: "raster", source: "access", paint: { "raster-opacity": 1, "raster-resampling": "linear", "raster-fade-duration": 0 } }, firstSymbol);
    const seoul = await (await fetch("data/seoul.geojson")).json();
    map.addSource("seoul", { type: "geojson", data: seoul });
    map.addLayer({ id: "seoul-line", type: "line", source: "seoul", paint: { "line-color": "#e8ecf7", "line-opacity": 0.55, "line-width": 1.2 } }, firstSymbol);
    // 노선도 (KTDB GTFS, 궤적은 MOTIS가 OSM에 맞춘 것). 지금 시각에 운행 중인 노선만, 운행 대수만큼 굵게
    try {
      const routes = await (await fetch("data/routes.geojson")).json();
      for (const f of routes.features) {
        f.properties.n.split(",").forEach((v, k) => { f.properties["c" + k] = +v; });
        delete f.properties.n;
        f.properties.hc = f.properties.color ? soften(f.properties.color) : f.properties.kind === "express" ? EXPRESS_RED : null;
      }
      // 운행 여부·대수는 feature-state("n")로 바꾼다 — setFilter는 레이어를 다시 타일링해 슬라이더보다 늦게 따라온다
      map.addSource("routes", { type: "geojson", data: routes, generateId: true });
      // 위계: 주인공 = 등시간 띠(색), 근거 = 영업 약국(노란 불빛), 배경 = 노선(무채색, 모양으로만 구분)
      // "교통망 강조"를 켜면 위계가 뒤집혀 노선이 색을 입고(hi) 띠는 흐려진다.
      // 그리는 순서 = 아래→위. w/wHi: 운행 대수에 따른 굵기 [대수1, 굵기1, 대수2, 굵기2]
      const zw = (a, b) => ["interpolate", ["linear"], ["zoom"], 10, a, 14, b];
      const ROUTE_LAYERS = [
        // 광역·공항버스: 기본은 무채색(아주 옅게), 강조 시 공식 빨강을 채도 낮춰 가늘게
        { kind: "express",
          base: { "line-color": "#8d97b5", "line-opacity": 0.12, "line-width": 0.5 },
          hi:   { "line-color": EXPRESS_RED, "line-opacity": 0.45, "line-width": 0.8 } },
        { kind: "city", w: [1, 0.25, 12, 0.7], wHi: [1, 0.4, 12, 1.1],
          base: { "line-color": "#8d97b5", "line-opacity": 0.4 },
          hi:   { "line-color": ["coalesce", ["get", "hc"], "#bcd0ff"],
                  "line-opacity": ["case", ["boolean", ["get", "seoul"], true], 0.8, 0.4] } },
        { kind: "night", w: [1, 0.6, 8, 1.1], wHi: [1, 1.0, 8, 1.8],
          base: { "line-color": "#e8ecf7", "line-opacity": 0.75 },
          hi:   { "line-color": "#ffffff", "line-opacity": 1 } },
        // 전철: 궤도교통 — 테두리 + 점선 철도 기호 (기본은 연회색, 강조 시 붉게)
        { kind: "rail", id: "route-rail-case",
          base: { "line-color": "#c9cfdf", "line-opacity": 0.55, "line-width": zw(2.2, 4.2) },
          hi:   { "line-color": ["coalesce", ["get", "hc"], "#ff4d4d"], "line-opacity": 1, "line-width": zw(3.4, 6.5) } },
        { kind: "rail", id: "route-rail-tie", layout: { "line-cap": "butt" },
          base: { "line-color": "#0b1020", "line-opacity": 0.9, "line-width": zw(0.9, 1.8), "line-dasharray": [2, 2] },
          hi:   { "line-color": "#0b1020", "line-opacity": 0.95, "line-width": zw(1.4, 2.6), "line-dasharray": [2, 2] } },
      ];
      for (const L of ROUTE_LAYERS) {
        L.id = L.id || "route-" + L.kind;
        map.addLayer({ id: L.id, type: "line", source: "routes", filter: ["==", ["get", "kind"], L.kind],
          layout: { "line-join": "round", "line-cap": "round", ...(L.layout || {}),
                    visibility: $("rt-" + L.kind).checked ? "visible" : "none" },
          paint: routePaint(L, false) }, "seoul-line");
      }
      routeLayers = ROUTE_LAYERS;
      routeFeatures = routes.features;
    } catch (e) { status("노선도를 불러오지 못했습니다 (" + e.message + "). 약국·띠 지도는 그대로 볼 수 있습니다."); }
    // 선택 표시: 클릭한 노선(노란 강조), 클릭한 칸(노란 테두리)과 그 칸의 가장 가까운 영업 약국(노란 고리·점선)
    const EMPTY = { type: "FeatureCollection", features: [] };
    if (map.getSource("routes")) {
      map.addLayer({ id: "route-hl", type: "line", source: "routes", filter: ["==", ["id"], -1],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#ffc35a", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 3, 14, 6], "line-opacity": 0.95 } }, "seoul-line");
    }
    map.addSource("sel", { type: "geojson", data: EMPTY });
    map.addLayer({ id: "sel-fill", type: "fill", source: "sel", filter: ["==", ["get", "k"], "cell"],
      paint: { "fill-color": "#ffc35a", "fill-opacity": 0.15 } });
    map.addLayer({ id: "sel-link", type: "line", source: "sel", filter: ["==", ["get", "k"], "link"],
      paint: { "line-color": "#ffc35a", "line-width": 1.5, "line-dasharray": [2, 2] } });
    map.addLayer({ id: "sel-cell", type: "line", source: "sel", filter: ["==", ["get", "k"], "cell"],
      paint: { "line-color": "#ffc35a", "line-width": 2.5 } });
    map.addSource("ph", { type: "geojson", data: { type: "FeatureCollection", features: phFeatures } });
    map.addLayer({ id: "ph-glow", type: "circle", source: "ph", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 6, 12, 9, 14, 12], "circle-color": "#ffc35a", "circle-blur": 1,
      "circle-opacity": ["case", ["boolean", ["feature-state", "open"], false], 0.35, 0] } });
    map.addLayer({ id: "ph", type: "circle", source: "ph", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.2, 12, 3, 14, 4.2], "circle-color": "#ffc35a",
      "circle-stroke-color": "#0b1020", "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 0.6, 14, 1.2],
      "circle-opacity": ["case", ["boolean", ["feature-state", "open"], false], 1, 0],
      "circle-stroke-opacity": ["case", ["boolean", ["feature-state", "open"], false], 1, 0] } });
    map.addLayer({ id: "sel-ph", type: "circle", source: "sel", filter: ["==", ["get", "k"], "ph"],
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 7, 14, 11], "circle-color": "rgba(0,0,0,0)",
               "circle-stroke-color": "#ffc35a", "circle-stroke-width": 2.5 } });
    update(+$("slider").value);
  });

  // ---- 그래프: 서울 면적 중 각 띠 안의 비율 (계산된 시점)
  const chart = $("chart"), cx = chart.getContext("2d");
  function drawChart(m) {
    const dpr = window.devicePixelRatio || 1, w = chart.clientWidth, h = chart.clientHeight;
    if (!w) return;
    chart.width = w * dpr; chart.height = h * dpr; cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx.clearRect(0, 0, w, h);
    const X = (t) => THUMB_R + (t / 720) * (w - 2 * THUMB_R), Y = (v) => h - 12 - v * (h - 16);
    for (let b = bands.length - 1; b >= 0; b--) {
      cx.beginPath(); cx.moveTo(X(times[0]), Y(0));
      times.forEach((t, k) => cx.lineTo(X(t), Y(meta.stats[k][b])));
      cx.lineTo(X(times[times.length - 1]), Y(0)); cx.closePath();
      cx.fillStyle = BAND_COLORS[b]; cx.globalAlpha = 0.85; cx.fill();
    }
    cx.globalAlpha = 1;
    // 현재 시각 막대 (드래그 가능): 굵은 노란 선 + 위쪽 손잡이
    cx.strokeStyle = "#ffc35a"; cx.lineWidth = 2;
    cx.beginPath(); cx.moveTo(X(m), 4); cx.lineTo(X(m), h - 12); cx.stroke();
    cx.fillStyle = "#ffc35a"; cx.beginPath(); cx.arc(X(m), 5, 4.5, 0, Math.PI * 2); cx.fill();
    cx.fillStyle = "#8d97b5"; cx.font = "500 10px JetBrains Mono, monospace";
    [[0, "18"], [180, "21"], [360, "00"], [540, "03"], [720, "06"]].forEach(([t, s]) => {
      cx.textAlign = t === 0 ? "left" : t === 720 ? "right" : "center"; cx.fillText(s, X(t), h - 1); });
  }

  // ---- 그래프 드래그로 시각 바꾸기 (슬라이더와 같은 눈금)
  const THUMB_R = 7;
  function chartToMinute(ev) {
    const r = chart.getBoundingClientRect();
    const t = (ev.clientX - r.left - THUMB_R) / (r.width - 2 * THUMB_R);
    return Math.round(Math.max(0, Math.min(1, t)) * maxMin);
  }
  let dragging = false;
  chart.addEventListener("pointerdown", (e) => {
    dragging = true; chart.setPointerCapture(e.pointerId);
    const m = chartToMinute(e); $("slider").value = m; update(m);
  });
  chart.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const m = chartToMinute(e);
    if (m !== +$("slider").value) { $("slider").value = m; update(m); }
  });
  const endDrag = () => { dragging = false; };
  chart.addEventListener("pointerup", endDrag);
  chart.addEventListener("pointercancel", endDrag);

  // ---- 갱신
  let nearestFrame = 0, curM = 0;
  const hhmm = (m) => { const t = (18 * 60 + m) % 1440; return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };
  const emphasis = () => $("rt-emph").checked;
  let routeK = -1, routeN = null;
  function updateRoutes(m) {
    if (!routeLayers || !map.getSource("routes")) return;
    const k = Math.max(0, Math.min(72, Math.round(m / 10)));
    const feats = routeFeatures;
    if (k !== routeK) {
      if (!routeN) routeN = new Int16Array(feats.length).fill(-1);
      for (let i = 0; i < feats.length; i++) {
        const n = feats[i].properties["c" + k];
        if (n !== routeN[i]) { map.setFeatureState({ source: "routes", id: i }, { n }); routeN[i] = n; }
      }
      routeK = k;
    }
    const on = new Set(["rail", "city", "night", "express"].filter((kd) => $("rt-" + kd).checked));
    let n = 0;
    for (const f of feats) if (f.properties["c" + k] > 0 && on.has(f.properties.kind)) n++;
    $("nroutes").textContent = n.toLocaleString("ko-KR");
  }
  function applyEmphasis() {
    const hi = emphasis();
    for (const L of routeLayers || []) {
      for (const [key, v] of Object.entries(routePaint(L, hi))) map.setPaintProperty(L.id, key, v);
    }
    if (map.getLayer("access")) map.setPaintProperty("access", "raster-opacity", hi ? 0.3 : 1);
    document.body.classList.toggle("emph", hi);
    updateRoutes(curM);
  }
  for (const kd of ["rail", "city", "night", "express"]) {
    $("rt-" + kd).addEventListener("change", (e) => {
      for (const L of routeLayers || []) if (L.kind === kd) map.setLayoutProperty(L.id, "visibility", e.target.checked ? "visible" : "none");
      updateRoutes(curM);
    });
  }
  $("rt-emph").addEventListener("change", applyEmphasis);

  function update(m) {
    updateRoutes(m);
    curM = m;
    nearestFrame = fillCur(m);
    draw();
    $("clock").textContent = hhmm(m);
    $("daylabel").textContent = m >= 360 ? "목요일 새벽" : "수요일";
    // 띠별 서울 면적 비율 (현재 보간값 기준)
    let n = 0; const cnt = [0, 0, 0, 0, 0];
    for (let i = 0; i < N; i++) if (mask[i] === 2) { n++; const v = cur[i]; for (let b = 0; b < 5; b++) if (v <= bands[b]) cnt[b]++; }
    cnt.forEach((c, b) => { $("bv" + b).textContent = Math.round((100 * c) / n) + "%"; });
    // 영업 중 약국 (서울 bbox 안 대략치 대신 서울 경계 판정은 생략, 전체 중 열려 있는 수)
    if (map.getSource("ph")) {
      if (!phOpen) phOpen = new Uint8Array(ph.length).fill(2);
      for (let i = 0; i < ph.length; i++) {
        const o = isOpen(ph[i], m) ? 1 : 0;
        if (o !== phOpen[i]) { map.setFeatureState({ source: "ph", id: i }, { open: !!o }); phOpen[i] = o; }
      }
    }
    $("nopen").textContent = openCount(m).toLocaleString("ko-KR");
    drawChart(m);
    refreshSelection();
  }
  let phOpen = null;
  const isOpen = (p, m) => (p[2] != null && p[2] <= m && p[3] > m) || (p[6] != null && p[6] <= m && p[7] > m);
  const SEOUL_BOX = [126.76, 37.41, 127.19, 37.72];   // 영업 약국 수 집계 범위 (서울 경계 근사)
  function openCount(m) {
    let c = 0;
    for (const p of ph) if (isOpen(p, m) && p[0] >= SEOUL_BOX[0] && p[0] <= SEOUL_BOX[2] && p[1] >= SEOUL_BOX[1] && p[1] <= SEOUL_BOX[3]) c++;
    return c;
  }

  // ---- 슬라이더·재생
  let raf = null, last = 0;
  $("slider").addEventListener("input", (e) => update(+e.target.value));
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function step(ts) {
    if (ts - last > (reduce ? 400 : 60)) {
      last = ts;
      let m = +$("slider").value + (reduce ? 5 : 1);
      if (m > maxMin) m = 0;
      $("slider").value = m; update(m);
    }
    raf = requestAnimationFrame(step);
  }
  $("play").addEventListener("click", () => {
    const on = raf === null;
    $("play").textContent = on ? "멈춤" : "재생";
    $("play").setAttribute("aria-pressed", String(on));
    if (on) raf = requestAnimationFrame(step); else { cancelAnimationFrame(raf); raf = null; }
  });
  window.addEventListener("resize", () => drawChart(curM));

  // ---- 격자 칸 찾기 (모서리 평행사변형 근사): 위치 → 칸 index (계산된 서울 칸만, 아니면 -1)
  const [TL, TR, , BL] = meta.corners;
  const ux = [TR[0] - TL[0], TR[1] - TL[1]], vy = [BL[0] - TL[0], BL[1] - TL[1]];
  const det = ux[0] * vy[1] - ux[1] * vy[0];
  const at = (u, v) => [TL[0] + u * ux[0] + v * vy[0], TL[1] + u * ux[1] + v * vy[1]];
  function cellAt(lngLat) {
    const dx = lngLat.lng - TL[0], dy = lngLat.lat - TL[1];
    const u = (dx * vy[1] - dy * vy[0]) / det, v = (ux[0] * dy - ux[1] * dx) / det;
    const col = Math.floor(u * cols), rowTop = Math.floor(v * rows);
    if (col < 0 || col >= cols || rowTop < 0 || rowTop >= rows) return -1;
    const i = (rows - 1 - rowTop) * cols + col;
    return mask[i] === 2 ? i : -1;
  }
  function cellRing(i) {
    const col = i % cols, rowTop = rows - 1 - Math.floor(i / cols);
    const u0 = col / cols, u1 = (col + 1) / cols, v0 = rowTop / rows, v1 = (rowTop + 1) / rows;
    return [at(u0, v0), at(u1, v0), at(u1, v1), at(u0, v1), at(u0, v0)];
  }
  const cellCenter = (i) => at(((i % cols) + 0.5) / cols, (rows - 1 - Math.floor(i / cols) + 0.5) / rows);
  const cellText = (i) => {
    const val = cur[i], j = nearest[nearestFrame * N + i];
    return val > 60 ? "60분 안에 문 연 약국 없음" : `<b>${Math.round(val)}분</b> · ${j >= 0 && ph[j] ? ph[j][5] : ""}`;
  };

  // ---- 마우스 올리기: 칸의 최소 시간과 약국 이름 (노선 위에서는 손가락 커서)
  const ROUTE_HIT = ["route-rail-case", "route-night", "route-city", "route-express"];
  // 노선은 "교통망 강조" 모드에서만 고른다 (기본 화면에선 노선이 촘촘해 칸을 누르기 어렵다)
  const KIND_ORDER = { rail: 0, night: 1, city: 2, express: 3 };
  function routesAt(point) {
    if (!routeLayers || !emphasis()) return [];
    const layers = ROUTE_HIT.filter((id) => map.getLayer(id) && map.getLayoutProperty(id, "visibility") !== "none");
    const R = 7;   // 판정 반경(px)
    const ids = new Set();
    for (const f of map.queryRenderedFeatures([[point.x - R, point.y - R], [point.x + R, point.y + R]], { layers })) {
      if ((f.state.n || 0) > 0) ids.add(f.id);
    }
    // KTDB는 같은 노선을 방향·급행별 id로 나눠 둔다 → 이름·종류가 같으면 한 묶음
    const groups = new Map();
    for (const id of ids) {
      const p = routeFeatures[id].properties, key = p.kind + "|" + p.name;
      if (!groups.has(key)) groups.set(key, { name: p.name, kind: p.kind, hc: p.hc, ids: [] });
      groups.get(key).ids.push(id);
    }
    return [...groups.values()].sort((x, y) =>
      (KIND_ORDER[x.kind] - KIND_ORDER[y.kind]) || x.name.localeCompare(y.name, "ko", { numeric: true }));
  }
  const hlFilter = (ids) => ["in", ["id"], ["literal", ids]];
  const swatchStyle = (g) => !g.hc ? "" : g.kind === "rail"
    ? ` style="background:repeating-linear-gradient(90deg,var(--night) 0 3px,${g.hc} 3px 6px);box-shadow:0 0 0 1.5px ${g.hc} inset"`
    : ` style="background:${g.hc}"`;
  const nowCount = (ids) => {
    const k = Math.max(0, Math.min(72, Math.round(curM / 10)));
    return ids.reduce((s, id) => s + routeFeatures[id].properties["c" + k], 0);
  };
  map.on("mousemove", (e) => {
    map.getCanvas().style.cursor = routesAt(e.point).length ? "pointer" : "";
    const i = cellAt(e.lngLat), tip = $("tip");
    if (i < 0) { tip.hidden = true; return; }
    tip.innerHTML = cellText(i);
    tip.style.left = e.originalEvent.clientX + 14 + "px"; tip.style.top = e.originalEvent.clientY + 14 + "px";
    tip.hidden = false;
  });
  map.on("mouseout", () => { $("tip").hidden = true; });

  // ---- 클릭: 노선 위면 노선 정보, 아니면 칸 선택 (노란 테두리 + 가장 가까운 영업 약국 강조)
  const KIND = { rail: "전철", city: "시내버스", night: "심야버스(N)", express: "광역·공항버스" };
  const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: false, maxWidth: "260px", className: "sel-pop", offset: 22 });
  let sel = null;            // { type: "cell", i } | { type: "route", group, lngLat } | { type: "pick", groups, lngLat }
  let quietClose = false;    // 코드로 팝업을 옮길 때는 선택을 지우지 않는다
  popup.on("close", () => { if (!quietClose) clearSelection(); });
  function showPopup(lngLat, html) {
    popup.setLngLat(lngLat).setHTML(html);
    if (!popup.isOpen()) popup.addTo(map);
  }
  function clearSelection() {
    sel = null;
    if (map.getSource("sel")) map.getSource("sel").setData({ type: "FeatureCollection", features: [] });
    if (map.getLayer("route-hl")) map.setFilter("route-hl", ["==", ["id"], -1]);
  }
  // 칸 가까이(직선 800m) 지금 영업 중인 약국이 있는데 결과가 그 약국이 아니면, 대개 곧 닫아서 도착 전에 마감되는 경우다.
  // 지도에선 '지금 영업 중'이라 켜져 있어 헷갈리므로 마감 시각을 알려 준다.
  const closeAt = (p, m) => (p[2] != null && p[2] <= m && p[3] > m) ? p[3] : (p[6] != null && p[6] <= m && p[7] > m) ? p[7] : null;
  function closingNote(i, j) {
    const [cx, cy] = cellCenter(i), kx = 88.2, ky = 111.0;   // km/도 (서울 위도)
    let best = -1, bd = 0.8;
    for (let q = 0; q < ph.length; q++) {
      if (q === j || !isOpen(ph[q], curM)) continue;
      const d = Math.hypot((ph[q][0] - cx) * kx, (ph[q][1] - cy) * ky);
      if (d < bd) { bd = d; best = q; }
    }
    if (best < 0) return "";
    const c = closeAt(ph[best], curM);
    if (c == null || c - curM > 30) return "";
    return `<div class="pop-x">더 가까운 <b>${ph[best][5]}</b>(${Math.round(bd * 1000)}m)은 ${hhmm(c)}에 닫아 도착 전에 마감</div>`;
  }

  function refreshSelection() {
    if (!sel || !map.getSource("sel")) return;
    if (sel.type === "cell") {
      const i = sel.i, j = nearest[nearestFrame * N + i], center = cellCenter(i);
      const feats = [{ type: "Feature", properties: { k: "cell" }, geometry: { type: "Polygon", coordinates: [cellRing(i)] } }];
      let html = `<div class="pop-t">${hhmm(curM)} 출발</div>`;
      if (cur[i] <= 60 && j >= 0 && ph[j]) {
        const pj = [ph[j][0], ph[j][1]];
        feats.push({ type: "Feature", properties: { k: "ph" }, geometry: { type: "Point", coordinates: pj } });
        feats.push({ type: "Feature", properties: { k: "link" }, geometry: { type: "LineString", coordinates: [center, pj] } });
        html += `<div class="pop-v"><b>${Math.round(cur[i])}분</b> 대중교통+도보</div>`
          + `<div class="pop-n">가장 빨리 닿는 영업 약국<br><b>${ph[j][5]}</b></div>`;
        html += closingNote(i, j);
      } else {
        html += `<div class="pop-v">60분 안에 문 연 약국 없음</div>` + closingNote(i, -1);
      }
      map.getSource("sel").setData({ type: "FeatureCollection", features: feats });
      showPopup(center, html);
    } else if (sel.type === "pick") {
      showPicker();
    } else {
      const g = sel.group, n = nowCount(g.ids);
      showPopup(sel.lngLat, `<div class="pop-t"><i class="rsw ${g.kind}"${swatchStyle(g)}></i> ${KIND[g.kind] || "노선"}</div>`
        + `<div class="pop-v"><b>${g.name}</b></div>`
        + `<div class="pop-n">${hhmm(curM)} ${n > 0 ? `운행 중 ${n}대` : "운행 안 함"}</div>`);
    }
  }
  function selectRoute(group, lngLat) {
    sel = { type: "route", group, lngLat };
    map.setFilter("route-hl", hlFilter(group.ids));
    refreshSelection();
  }
  // 겹친 노선 목록: 마우스를 올리면 미리 강조, 누르면 그 노선을 선택
  function showPicker() {
    const items = sel.groups.map((g, gi) => {
      const n = nowCount(g.ids);
      return `<li><button type="button" class="pick" data-g="${gi}"><i class="rsw ${g.kind}"${swatchStyle(g)}></i>`
        + `<span class="pn">${g.name}</span><span class="pk">${KIND[g.kind]}${n > 0 ? ` · ${n}대` : ""}</span></button></li>`;
    }).join("");
    showPopup(sel.lngLat, `<div class="pop-t">노선 ${sel.groups.length}개 · 고르세요</div><ul class="picks">${items}</ul>`);
    const el = popup.getElement();
    el.querySelectorAll("button.pick").forEach((btn) => {
      const g = sel.groups[+btn.dataset.g];
      btn.addEventListener("mouseenter", () => map.setFilter("route-hl", hlFilter(g.ids)));
      btn.addEventListener("focus", () => map.setFilter("route-hl", hlFilter(g.ids)));
      btn.addEventListener("click", () => selectRoute(g, sel.lngLat));
    });
    el.querySelector("ul.picks").addEventListener("mouseleave", () => {
      if (sel && sel.type === "pick") map.setFilter("route-hl", ["==", ["id"], -1]);
    });
  }
  map.on("click", (e) => {
    const rs = routesAt(e.point), i = rs.length ? -1 : cellAt(e.lngLat);
    quietClose = true;
    if (!rs.length && i < 0) popup.remove();
    quietClose = false;
    clearSelection();
    if (rs.length === 1) {
      selectRoute(rs[0], e.lngLat);
      return;
    } else if (rs.length > 1) {
      sel = { type: "pick", groups: rs, lngLat: e.lngLat };
    } else if (i >= 0) {
      sel = { type: "cell", i };
    }
    refreshSelection();
    if (sel && sel.type === "cell") revealPharmacy(sel.i);
  });
  // 칸을 클릭했을 때 가장 가까운 약국이 화면 밖이면, 칸과 약국이 함께 보이게 지도를 옮긴다
  function revealPharmacy(i) {
    const j = nearest[nearestFrame * N + i];
    if (cur[i] > 60 || j < 0 || !ph[j]) return;
    const c = cellCenter(i), p = [ph[j][0], ph[j][1]];
    const b = map.getBounds();
    if (b.contains(p) && b.contains(c)) return;
    const pad = window.innerWidth > 560 ? { left: 420, top: 90, right: 60, bottom: 60 } : { left: 30, top: 90, right: 30, bottom: 300 };
    map.fitBounds([[Math.min(c[0], p[0]), Math.min(c[1], p[1])], [Math.max(c[0], p[0]), Math.max(c[1], p[1])]],
      { padding: pad, maxZoom: map.getZoom(), duration: 600 });
  }
  window.addEventListener("keydown", (e) => { if (e.key === "Escape") popup.remove(); });

  update(0);
})();
