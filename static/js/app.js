/* =====================================================================
   PSU PathFinder – browser code (vanilla JavaScript, no libraries)

   This file holds NO building names, IDs, coordinates or distances.
   Everything comes from the Flask API, which reads SQLite:
     /api/locations  /api/entrances  /api/walkways  /api/paths
     /api/footprints /api/route      /api/history   /api/data-report
   The browser only DRAWS what the backend returns; it never calculates
   a distance or chooses a route by itself.
   ===================================================================== */
(() => {
  'use strict';

  // ------------------------------------------------------------------ state
  const S = {
    map: null,                 // {width_px, height_px, image, annotated_image}
    locations: [], byId: {},
    entrances: [], walkNodes: [], walkSegments: [], segById: {},
    altPaths: [], footprints: [],
    route: null,               // last /api/route response
    selected: null,            // building id shown in the info card
    zoom: 1, fitWidth: 0,
    layers: { buildings: true, gate: true, entrances: true, walkways: true, alternatives: true, footprints: false, labels: false },
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n) => Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
  const SVGNS = 'http://www.w3.org/2000/svg';

  const el = {
    viewport: $('#mapViewport'), inner: $('#mapInner'), svg: $('#mapSvg'), markers: $('#markerLayer'),
    lFoot: $('#layerFootprints'), lWalk: $('#layerWalkways'), lAlt: $('#layerAlternatives'), lRoute: $('#layerRoute'),
    start: $('#startSelect'), dest: $('#destSelect'), search: $('#searchInput'), searchList: $('#searchList'),
    find: $('#findBtn'), reset: $('#resetBtn'), swap: $('#swapBtn'), flagged: $('#excludeFlagged'),
    result: $('#resultCard'), info: $('#infoCard'), infoBody: $('#infoBody'),
    history: $('#historyList'), toast: $('#toast'),
    navigate: $('#navigate'), panel: $('#panel'), panelScroll: $('#panel .panel-scroll'),
    sheetHandle: $('#sheetHandle'), sheetDrag: $('#resultHead'),
    searchBox: $('#searchBox'), planner: $('.planner'), routeFields: $('.planner .route-fields'),
    mTopbar: $('#mTopbar'), mSearchSlot: $('#mSearchSlot'), mBack: $('#mBack'), mMenuBtn: $('#mMenuBtn'), mMenu: $('#mMenu'),
  };

  // ------------------------------------------------------------------ API
  async function api(path, options = {}) {
    let res;
    try {
      res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
    } catch (err) {
      throw new Error('Cannot reach the PSU PathFinder server. Is "python app.py" still running?');
    }
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok || !data || data.success === false) {
      const err = new Error((data && data.message) || `Request failed (${res.status})`);
      err.status = res.status;
      err.code = data && data.error;
      throw err;
    }
    return data;
  }

  // ADMIN actions (clear history, re-import data): the server checks the X-Admin-Token header.
  // The token is kept only in this page's memory, never saved.
  let adminToken = '';
  async function adminApi(path, options = {}) {
    const send = () => api(path, { ...options, headers: { 'Content-Type': 'application/json', ...(adminToken ? { 'X-Admin-Token': adminToken } : {}) } });
    try {
      return await send();
    } catch (err) {
      if (err.code !== 'ADMIN_ONLY') throw err;
      const t = window.prompt('This action is for the site administrator. Enter the admin token:');
      if (!t) throw new Error('Cancelled: admin token required.');
      adminToken = t.trim();
      return send();
    }
  }

  let toastTimer = null;
  function toast(msg, isError = false) {
    el.toast.textContent = msg;
    el.toast.classList.toggle('error', isError);
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.toast.hidden = true; }, isError ? 6000 : 3000);
  }

  // percent -> map pixel (SVG viewBox units)
  const px = (p) => [p[0] / 100 * S.map.width_px, p[1] / 100 * S.map.height_px];
  const pathD = (pts) => pts.map((p, i) => { const [x, y] = px(p); return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`; }).join(' ');
  function svg(tag, attrs = {}, parent = null) {
    const n = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (parent) parent.appendChild(n);
    return n;
  }
  const placeAt = (node, x, y) => { node.style.left = `${x}%`; node.style.top = `${y}%`; };

  // ------------------------------------------------------------------ boot
  async function init() {
    try {
      const [loc, ent, walk, paths, fps] = await Promise.all([
        api('/api/locations'), api('/api/entrances'), api('/api/walkways'), api('/api/paths'), api('/api/footprints'),
      ]);
      S.map = loc.map;
      S.locations = loc.locations;
      S.locations.forEach((l) => { S.byId[l.building_id] = l; });
      S.entrances = ent.entrances;
      S.walkNodes = walk.nodes;
      S.walkSegments = walk.segments;
      S.walkSegments.forEach((s) => { S.segById[s.segment_id] = s; });
      S.altPaths = paths.annotated_paths;
      S.footprints = fps.footprints;
    } catch (err) {
      toast(err.message, true);
      el.history.innerHTML = `<p class="empty-state">${esc(err.message)}</p>`;
      return;
    }
    el.svg.setAttribute('viewBox', `0 0 ${S.map.width_px} ${S.map.height_px}`);
    el.inner.style.aspectRatio = `${S.map.width_px} / ${S.map.height_px}`;
    $$('.preview-frame').forEach((f) => { f.style.aspectRatio = `${S.map.width_px} / ${S.map.height_px}`; });

    fillSelects();
    drawFootprints();
    drawWalkways();
    drawAlternativePaths();
    drawMarkers();
    buildLegendKey();
    heroPreview();
    $('[data-stat="segments"]').textContent = S.walkSegments.length;
    $('[data-stat="entrances"]').textContent = S.entrances.length;
    $('[data-stat="buildings"]').textContent = S.locations.length;
    applyLayers();
    placeSearch();
    fitMap();
    bindEvents();
    if (location.hash === '#navigate' && isMobile()) {   // opened directly on the map from a phone
      history.replaceState(null, '', location.pathname);
      enterMapMode(true);
    }
    loadHistory();
    loadSiteStats();
  }

  function sortedLocations() {
    // Main Gate first (usual starting point), then legend order
    return [...S.locations].sort((a, b) => (a.location_type === 'gate' ? -1 : b.location_type === 'gate' ? 1 : a.legend_number - b.legend_number));
  }

  function fillSelects() {
    const opts = (placeholder) => `<option value="">${placeholder}</option>` +
      sortedLocations().map((l) => `<option value="${esc(l.building_id)}">${l.location_type === 'gate' ? '' : l.legend_number + '. '}${esc(l.building_name)}</option>`).join('');
    el.start.innerHTML = opts('Choose a starting point');
    el.dest.innerHTML = opts('Choose a destination');
    const gate = S.locations.find((l) => l.location_type === 'gate');
    if (gate) el.start.value = gate.building_id;      // default start: Main Gate
  }

  function heroPreview() {
    $$('.preview-pin').forEach((p) => {
      const b = S.byId[p.dataset.building];
      if (!b) return;
      placeAt(p, b.x_percent, b.y_percent);
      p.hidden = false;
    });
  }

  // ------------------------------------------------------------------ map layers
  function drawFootprints() {
    el.lFoot.innerHTML = '';
    S.footprints.forEach((f) => {
      const pts = f.footprint_coordinates.map((p) => px(p).map((v) => v.toFixed(1)).join(',')).join(' ');
      const poly = svg('polygon', { points: pts, 'data-building': f.building_id || '', class: f.building_id ? '' : 'unnamed' }, el.lFoot);
      const t = svg('title', {}, poly);
      t.textContent = f.structure_name || (S.byId[f.building_id]?.building_name ?? '') + ' (restricted area)';
    });
  }

  function drawWalkways() {
    el.lWalk.innerHTML = '';
    S.walkSegments
      .filter((s) => ['original_walkway', 'main_gate_access', 'entrance_access'].includes(s.segment_type))
      .forEach((s) => {
        const cls = [s.verification_status === 'traced_inferred' ? 'inferred' : '',
                     s.segment_type === 'entrance_access' ? 'access' : '',
                     s.annotation_colour === 'purple' ? 'annotated' : ''].join(' ').trim();
        const p = svg('path', { d: pathD(s.geometry_coordinates), class: cls, 'data-segment': s.segment_id }, el.lWalk);
        svg('title', {}, p).textContent = s.annotation_colour === 'purple'
          ? `${s.segment_name} (original walkway, purple on the latest map, not measured)`
          : `${s.segment_name} (traced, not measured)`;
      });
    // purple strokes are clickable for details
    S.walkSegments.filter((s) => s.annotation_colour === 'purple').forEach((s) => {
      const hit = svg('path', { d: pathD(s.geometry_coordinates), class: 'hit' }, el.lWalk);
      hit.addEventListener('click', (e) => { e.stopPropagation(); showSegmentInfo(s.segment_id); });
    });
  }

  function showSegmentInfo(segmentId) {
    const s = S.segById[segmentId];
    if (!s) return;
    highlightBuilding(null);
    $$('#layerWalkways path:not(.hit)').forEach((n) => n.classList.toggle('focus', n.dataset.segment === segmentId));
    el.infoBody.innerHTML = `
      <div class="info-title"><span class="sw" style="background:var(--walkway)"></span><h3>${esc(s.segment_name)}</h3></div>
      <p class="kv"><b>Type:</b> original campus walkway (drawn <b>purple</b> on the latest annotated map)</p>
      <p class="kv"><b>Distance:</b> not measured (stored as NULL) · <b>Status:</b> ${esc(s.verification_status.replace(/_/g, ' '))}</p>
      ${s.note ? `<p class="kv">${esc(s.note)}</p>` : ''}
      <p class="kv">Main-route legs may draw their line along it. Its length is never shown as metres.</p>`;
    el.info.hidden = false;
    revealCard(el.info);
  }

  function drawAlternativePaths() {
    el.lAlt.innerHTML = '';
    const segs = S.walkSegments.filter((s) => ['alternative_walkway', 'junction_link'].includes(s.segment_type));
    segs.forEach((s) => {
      const cls = [s.segment_type === 'junction_link' ? 'link' : '', s.is_walkable ? '' : 'unverified'].join(' ').trim();
      const p = svg('path', { d: pathD(s.geometry_coordinates), class: cls, 'data-path': s.path_id || '' }, el.lAlt);
      if (!s.is_walkable) svg('title', {}, p).textContent = `${s.segment_name} – unverified, not used for routing`;
    });
    // wide invisible strokes on top make the thin paths easy to click
    segs.forEach((s) => {
      const hit = svg('path', { d: pathD(s.geometry_coordinates), class: 'hit', 'data-path': s.path_id || '' }, el.lAlt);
      hit.addEventListener('click', (e) => { e.stopPropagation(); showPathInfo(s.path_id); });
    });
  }

  function drawMarkers() {
    el.markers.innerHTML = '';
    // entrances (blue dots)
    S.entrances.forEach((e) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'emark layer-entrances' + (e.verification_status === 'unverified' ? ' unverified' : '') + (e.node_id ? '' : ' display-only');
      b.dataset.entrance = e.entrance_id;
      b.dataset.building = e.building_id;
      b.title = `${e.entrance_name} – ${e.building_name} (${e.verification_status.replace(/_/g, ' ')})`;
      b.setAttribute('aria-label', b.title);
      placeAt(b, e.x_percent, e.y_percent);
      b.addEventListener('click', (ev) => { ev.stopPropagation(); showBuilding(e.building_id, e.entrance_id); });
      el.markers.appendChild(b);
    });
    // buildings + gate
    S.locations.forEach((l) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.building = l.building_id;
      b.setAttribute('aria-label', l.building_name);
      if (l.location_type === 'gate') {
        b.className = 'gmark layer-gate';
        b.innerHTML = `<span class="dot"></span><span class="label">${esc(l.building_name)}</span>`;
      } else {
        b.className = 'bmark layer-buildings';
        b.innerHTML = `<span class="dot" style="background:${esc(l.building_color)}">${l.legend_number}</span><span class="label">${esc(l.short_name || l.building_name)}</span>`;
      }
      placeAt(b, l.x_percent, l.y_percent);
      b.addEventListener('click', (ev) => { ev.stopPropagation(); showBuilding(l.building_id); });
      el.markers.appendChild(b);
    });
  }

  function buildLegendKey() {
    $('#buildingKey').innerHTML = sortedLocations().map((l) => `
      <li><button type="button" data-building="${esc(l.building_id)}" title="${esc(l.building_name)}">
        <span class="num" style="background:${l.location_type === 'gate' ? 'var(--gate)' : esc(l.building_color)}">${l.location_type === 'gate' ? '' : l.legend_number}</span>
        ${esc(l.short_name || l.building_name)}</button></li>`).join('');
    $$('#buildingKey button').forEach((b) => b.addEventListener('click', () => { showBuilding(b.dataset.building); panTo(S.byId[b.dataset.building]); }));
  }

  function applyLayers() {
    $$('.legend-list input[data-layer]').forEach((cb) => { S.layers[cb.dataset.layer] = cb.checked; });
    el.lFoot.style.display = S.layers.footprints ? '' : 'none';
    el.lWalk.style.display = S.layers.walkways ? '' : 'none';
    el.lAlt.style.display = S.layers.alternatives ? '' : 'none';
    $$('.layer-entrances').forEach((n) => { n.style.display = S.layers.entrances ? '' : 'none'; });
    $$('.layer-buildings').forEach((n) => { n.style.display = S.layers.buildings ? '' : 'none'; });
    $$('.layer-gate').forEach((n) => { n.style.display = S.layers.gate ? '' : 'none'; });
    el.inner.classList.toggle('show-labels', S.layers.labels);
  }

  // ------------------------------------------------------------------ zoom & pan
  const ZOOM_MIN = 1, ZOOM_MAX = 6;
  const ratio = () => S.map.width_px / S.map.height_px;
  const mobileQuery = window.matchMedia('(max-width: 820px), (max-height: 520px) and (pointer: coarse)');
  const isMobile = () => mobileQuery.matches;
  const inMapMode = () => document.body.classList.contains('map-mode');

  // Part of the viewport that is NOT covered by the floating search bar or the bottom sheet
  function visibleRect() {
    const vp = el.viewport;
    if (!inMapMode()) return { left: 0, top: 0, width: vp.clientWidth, height: vp.clientHeight };
    const top = el.mTopbar.offsetHeight;
    const m = sheetMetrics();
    const sheetTop = el.navigate.clientHeight - (el.panel.offsetHeight - m[S.sheet || 'half']);
    return { left: 0, top, width: vp.clientWidth, height: Math.max(120, sheetTop - top) };
  }
  // padding inside the scroll box (map mode adds room so any point can be centred above the sheet)
  const padLeft = () => parseFloat(getComputedStyle(el.viewport).paddingLeft) || 0;

  function fitMap() {
    const vp = el.viewport;
    const usableW = vp.clientWidth - 2 * padLeft();
    // zoom 1 = whole campus visible (on phones: in the part not covered by the search bar / sheet)
    const usableH = inMapMode() ? vp.clientHeight - el.mTopbar.offsetHeight : vp.clientHeight;
    S.fitWidth = Math.min(usableW, usableH * ratio());
    setZoom(1);
    if (inMapMode()) {                                                // phones: frame all 14 locations, like opening Google Maps
      fitBounds(S.locations.map((l) => [l.x_percent, l.y_percent]), false);
    }
  }
  function setZoom(z, focus = null) {
    const vp = el.viewport;
    const oldW = el.inner.offsetWidth || S.fitWidth;
    const vis = visibleRect();
    // the map point under the focus (default: centre of the visible area) stays put
    const fx = focus ? focus.x : vis.left + vis.width / 2, fy = focus ? focus.y : vis.top + vis.height / 2;
    const mx = (vp.scrollLeft + fx - el.inner.offsetLeft) / oldW;
    const my = (vp.scrollTop + fy - el.inner.offsetTop) / (oldW / ratio());
    S.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z));
    const w = S.fitWidth * S.zoom, h = w / ratio();
    el.inner.style.width = `${w}px`;
    // centre the map vertically on desktop when it is smaller than the viewport
    el.inner.style.marginTop = !inMapMode() && h < vp.clientHeight ? `${(vp.clientHeight - h) / 2}px` : '0';
    vp.scrollTo({ left: mx * w + el.inner.offsetLeft - fx, top: my * h + el.inner.offsetTop - fy, behavior: 'auto' });
  }
  function centerOn(xPct, yPct, smooth = true) {
    const vp = el.viewport, w = el.inner.offsetWidth, h = el.inner.offsetHeight, vis = visibleRect();
    const shift = inMapMode() ? 22 : 0;            // keep clear of the map buttons on the right
    vp.scrollTo({ left: xPct / 100 * w + el.inner.offsetLeft - (vis.left + vis.width / 2 - shift),
                  top: yPct / 100 * h + el.inner.offsetTop - (vis.top + vis.height / 2),
                  behavior: smooth ? 'smooth' : 'auto' });
  }
  function panTo(loc, zoom = 2) {
    if (!loc) return;
    if (S.zoom < zoom) setZoom(zoom);
    centerOn(loc.x_percent, loc.y_percent);
  }
  function fitBounds(points, smooth = true) {
    if (!points.length) return;
    const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const vis = visibleRect();
    const spanW = Math.max(6, maxX - minX) / 100, spanH = Math.max(6, maxY - minY) / 100;
    // room for pins, labels and (on phones) the floating map buttons on the right
    const padX = Math.min(vis.width < 600 ? 130 : 140, vis.width * 0.3);
    const padY = Math.min(vis.width < 600 ? 90 : 140, vis.height * 0.25);
    const needW = Math.min((vis.width - padX) / spanW, (vis.height - padY) * ratio() / spanH);
    setZoom(Math.max(1, Math.min(4, needW / S.fitWidth)));
    centerOn((minX + maxX) / 2, (minY + maxY) / 2, smooth);
  }

  function bindMapInteraction() {
    const vp = el.viewport;
    const pointers = new Map();        // active touches / mouse, for drag + pinch
    let drag = null, pinch = null, lastTap = null;
    const local = (e) => { const r = vp.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };

    vp.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || e.target.closest('button')) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 2) {                                     // second finger: start pinch-zoom
        const [a, b] = [...pointers.values()];
        pinch = { d0: Math.hypot(a.x - b.x, a.y - b.y), z0: S.zoom };
        drag = null;
        vp.classList.add('dragging');
        try { vp.setPointerCapture(e.pointerId); } catch { /* ignore */ }
      } else if (pointers.size === 1) {
        drag = { x: e.clientX, y: e.clientY, sl: vp.scrollLeft, st: vp.scrollTop, moved: false, id: e.pointerId };
      }
    });
    vp.addEventListener('pointermove', (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch && pointers.size >= 2) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const r = vp.getBoundingClientRect();
        setZoom(pinch.z0 * d / pinch.d0, { x: (a.x + b.x) / 2 - r.left, y: (a.y + b.y) / 2 - r.top });
        S.justDragged = Date.now();
        return;
      }
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) > 5) {
        drag.moved = true;
        vp.classList.add('dragging');
        try { vp.setPointerCapture(drag.id); } catch { /* ignore */ }
      }
      if (drag.moved) { vp.scrollLeft = drag.sl - dx; vp.scrollTop = drag.st - dy; }
    });
    const end = (e) => {
      pointers.delete(e.pointerId);
      if (pinch && pointers.size < 2) {
        pinch = null;
        // continue panning with the remaining finger
        const rest = [...pointers.entries()][0];
        drag = rest ? { x: rest[1].x, y: rest[1].y, sl: vp.scrollLeft, st: vp.scrollTop, moved: true, id: rest[0] } : null;
        if (!rest) vp.classList.remove('dragging');
        return;
      }
      if (drag && drag.id === e.pointerId) {
        if (drag.moved) S.justDragged = Date.now();
        else if (e.type === 'pointerup' && e.pointerType === 'touch') {
          // double-tap to zoom in, like Google Maps
          const now = Date.now(), p = local(e);
          if (lastTap && now - lastTap.t < 320 && Math.hypot(p.x - lastTap.x, p.y - lastTap.y) < 30) {
            setZoom(S.zoom * 1.7, p);
            S.justDragged = now;
            lastTap = null;
          } else lastTap = { t: now, ...p };
        }
        vp.classList.remove('dragging');
        drag = null;
      }
    };
    vp.addEventListener('pointerup', end);
    vp.addEventListener('pointercancel', end);
    vp.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && !e.metaKey) return;          // plain wheel scrolls/pans (trackpad pinch sends ctrlKey)
      e.preventDefault();
      setZoom(S.zoom * (e.deltaY < 0 ? 1.2 : 1 / 1.2), local(e));
    }, { passive: false });
    vp.addEventListener('keydown', (e) => {
      if (e.key === '+' || e.key === '=') setZoom(S.zoom * 1.25);
      else if (e.key === '-') setZoom(S.zoom / 1.25);
      else if (e.key === '0') fitMap();
    });
    $('#zoomIn').addEventListener('click', () => setZoom(S.zoom * 1.35));
    $('#zoomOut').addEventListener('click', () => setZoom(S.zoom / 1.35));
    $('#zoomFit').addEventListener('click', fitMap);
    // cycles: original map -> latest annotated map -> earlier annotated map (all aligned to one frame)
    const views = [
      { img: '#mapImage', label: 'Plan', msg: 'Showing the original campus map.' },
      { img: '#latestImage', label: 'Latest', msg: 'Showing the LATEST annotated map (purple = original walkways, orange = alternatives).' },
      { img: '#planImage', label: 'Earlier', msg: 'Showing the earlier annotated map.' },
    ];
    let view = 0;
    $('#planToggle').addEventListener('click', (e) => {
      view = (view + 1) % views.length;
      views.forEach((v, i) => { $(v.img).hidden = i !== view; });
      e.currentTarget.setAttribute('aria-pressed', String(view !== 0));
      e.currentTarget.textContent = views[(view + 1) % views.length].label === 'Plan' ? 'Map' : views[(view + 1) % views.length].label;
      toast(views[view].msg);
    });
    let resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        placeSearch();
        if (!isMobile() && inMapMode()) exitMapMode();
        if (inMapMode()) setSheet(S.sheet, false);
        const z = S.zoom; fitMap(); if (z > 1 && !inMapMode()) setZoom(z);
      }, 120);
    });
  }

  // ------------------------------------------------------------------ phone "map mode" + bottom sheet
  // On phones the map opens full-screen (like Google Maps): floating search bar on top,
  // draggable bottom sheet with three heights: peek / half / full.
  function placeSearch() {
    if (isMobile()) { if (el.searchBox.parentElement !== el.mSearchSlot) el.mSearchSlot.appendChild(el.searchBox); }
    else if (el.searchBox.parentElement !== el.planner) el.planner.insertBefore(el.searchBox, el.routeFields);
  }
  function enterMapMode(push = true) {
    if (!isMobile()) { document.getElementById('navigate').scrollIntoView({ behavior: 'smooth' }); return; }
    if (inMapMode()) return;
    document.body.classList.add('map-mode');
    if (push) history.pushState({ map: true }, '', '#navigate');     // phone Back button closes the map
    S.sheet = S.route ? 'peek' : 'half';
    requestAnimationFrame(() => {
      setSheet(S.sheet, false);
      fitMap();
      if (S.route) fitRoute(S.route, false);
    });
  }
  function exitMapMode(targetId = null) {
    if (!inMapMode()) return;
    document.body.classList.remove('map-mode');
    el.panel.style.transform = '';
    el.mMenu.hidden = true;
    el.mMenuBtn.setAttribute('aria-expanded', 'false');
    fitMap();
    const target = document.getElementById(targetId || 'navigate');
    if (target) requestAnimationFrame(() => target.scrollIntoView({ behavior: 'auto' }));
  }
  function sheetMetrics() {
    const H = el.panel.offsetHeight;                 // sheet height when fully open
    const navH = el.navigate.clientHeight;
    let peek = 150;                                  // handle + title + first field
    if (S.route && !el.result.hidden) {              // handle + distance + stops
      const top = el.panel.getBoundingClientRect().top;
      peek = $('#resultStops').getBoundingClientRect().bottom - top + 14;
    } else if (!el.info.hidden) {
      const top = el.panel.getBoundingClientRect().top;
      peek = el.info.querySelector('.info-title').getBoundingClientRect().bottom - top + 40;
    }
    peek = Math.max(96, Math.min(peek, H * 0.6, navH * 0.42));   // landscape phones: keep most of the map visible
    return { full: 0, half: Math.max(0, H - Math.round(navH * 0.5)), peek: Math.max(0, H - peek) };
  }
  function setSheet(state, animate = true) {
    if (!inMapMode()) return;
    S.sheet = state;
    el.panel.classList.toggle('dragging', !animate);
    el.panel.style.transform = `translateY(${sheetMetrics()[state]}px)`;
    el.panel.dataset.state = state;
    el.sheetHandle.setAttribute('aria-expanded', String(state !== 'peek'));
    if (state !== 'full') el.panelScroll.scrollTop = 0;
    if (!animate) requestAnimationFrame(() => el.panel.classList.remove('dragging'));
  }
  function bindSheet() {
    const order = ['peek', 'half', 'full'];
    let d = null;
    const start = (e) => {
      if (!inMapMode() || e.button !== 0 || e.target.closest('button:not(.sheet-handle), select, input, a')) return;
      const m = sheetMetrics();
      d = { y0: e.clientY, base: m[S.sheet], m, last: e.clientY, t: performance.now(), v: 0, moved: false, id: e.pointerId, el: e.currentTarget };
    };
    const move = (e) => {
      if (!d || e.pointerId !== d.id) return;
      const dy = e.clientY - d.y0;
      if (!d.moved && Math.abs(dy) > 6) {
        d.moved = true;
        el.panel.classList.add('dragging');
        try { d.el.setPointerCapture(d.id); } catch { /* ignore */ }
      }
      if (!d.moved) return;
      const now = performance.now();
      d.v = (e.clientY - d.last) / Math.max(1, now - d.t);     // px per ms, + = downwards
      d.last = e.clientY; d.t = now;
      const y = Math.max(0, Math.min(d.m.peek + 40, d.base + dy));
      el.panel.style.transform = `translateY(${y}px)`;
    };
    const end = (e) => {
      if (!d || e.pointerId !== d.id) return;
      el.panel.classList.remove('dragging');
      const i = order.indexOf(S.sheet);
      let next;
      if (!d.moved) {
        if (e.currentTarget !== el.sheetHandle) { d = null; return; }    // a plain tap on content
        next = S.sheet === 'peek' ? 'half' : S.sheet === 'half' ? 'full' : 'half';
      } else {
        // where the sheet would coast to (position + flick), then snap to the nearest height
        const y = d.base + (e.clientY - d.y0) + d.v * 220;
        next = order.reduce((best, s) => (Math.abs(d.m[s] - y) < Math.abs(d.m[best] - y) ? s : best), 'half');
        if (next === S.sheet && Math.abs(d.v) > 0.5) next = order[Math.max(0, Math.min(2, i + (d.v < 0 ? 1 : -1)))];
      }
      d = null;
      setSheet(next);
    };
    [el.sheetHandle, el.sheetDrag].forEach((n) => {
      n.addEventListener('pointerdown', start);
      n.addEventListener('pointermove', move);
      n.addEventListener('pointerup', end);
      n.addEventListener('pointercancel', end);
    });
    el.sheetHandle.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSheet(S.sheet === 'full' ? 'peek' : S.sheet === 'peek' ? 'half' : 'full'); }
    });
    // phone Back button / browser back closes the full-screen map
    window.addEventListener('popstate', () => { if (inMapMode() && !(history.state && history.state.map)) exitMapMode('home'); });
    // every "Campus Map" / "Start Navigation" link opens map mode on phones
    document.addEventListener('click', (e) => {
      const a = e.target.closest('a[href="#navigate"], [data-open-map]');
      if (a && isMobile()) { e.preventDefault(); enterMapMode(); }
    });
    el.mBack.addEventListener('click', () => { if (history.state && history.state.map) history.back(); else exitMapMode('home'); });
    el.mMenuBtn.addEventListener('click', () => {
      const open = el.mMenu.hidden;
      el.mMenu.hidden = !open;
      el.mMenuBtn.setAttribute('aria-expanded', String(open));
    });
    $$('#mMenu a').forEach((a) => a.addEventListener('click', (e) => {
      e.preventDefault();
      const id = a.getAttribute('href').slice(1);
      if (history.state && history.state.map) history.replaceState(null, '', `#${id}`);
      exitMapMode(id);
    }));
  }

  // ------------------------------------------------------------------ search
  const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  function searchLocations(q) {
    const n = norm(q);
    if (!n) return [];
    const words = n.split(' ');
    return sortedLocations()
      .map((l) => {
        const hay = [l.building_name, l.short_name, ...(l.aliases || []), l.plan_label].map(norm);
        const all = hay.join(' | ');
        if (!words.every((w) => all.includes(w))) return null;
        const score = hay.some((h) => h.startsWith(n)) ? 0 : 1;
        const via = norm(l.building_name).includes(n) ? '' : (l.aliases || []).find((a) => norm(a).includes(words[0])) || l.plan_label || '';
        return { l, score, via };
      })
      .filter(Boolean)
      .sort((a, b) => a.score - b.score || a.l.legend_number - b.l.legend_number);
  }
  let searchIndex = -1;
  function renderSearch() {
    const results = searchLocations(el.search.value);
    searchIndex = results.length ? 0 : -1;
    if (!el.search.value.trim()) { closeSearch(); return; }
    el.searchList.innerHTML = results.length
      ? results.map((r, i) => `<li role="option" id="sr-${i}" data-building="${esc(r.l.building_id)}" aria-selected="${i === 0}">
          <span class="sw" style="background:${r.l.location_type === 'gate' ? 'var(--gate)' : esc(r.l.building_color)}"></span>${esc(r.l.building_name)}
          ${r.via ? `<small>${esc(r.via)}</small>` : ''}</li>`).join('')
      : '<li class="empty">No matching building</li>';
    el.searchList.hidden = false;
    el.search.setAttribute('aria-expanded', 'true');
    $$('#searchList li[data-building]').forEach((li) => li.addEventListener('mousedown', (e) => { e.preventDefault(); pickSearch(li.dataset.building); }));
  }
  function closeSearch() { el.searchList.hidden = true; el.search.setAttribute('aria-expanded', 'false'); }
  function pickSearch(id) {
    closeSearch();
    el.search.value = S.byId[id].building_name;
    if (inMapMode()) setSheet('half');
    showBuilding(id);
    panTo(S.byId[id], 2.2);
  }
  function moveSearch(delta) {
    const items = $$('#searchList li[data-building]');
    if (!items.length) return;
    searchIndex = (searchIndex + delta + items.length) % items.length;
    items.forEach((li, i) => li.setAttribute('aria-selected', String(i === searchIndex)));
    el.search.setAttribute('aria-activedescendant', items[searchIndex].id);
    items[searchIndex].scrollIntoView({ block: 'nearest' });
  }

  // ------------------------------------------------------------------ building / entrance / path info
  function highlightBuilding(id, entranceId = null) {
    $$('.bmark, .gmark').forEach((m) => m.classList.toggle('selected', m.dataset.building === id));
    $$('.emark').forEach((m) => m.classList.toggle('focus', !!id && (entranceId ? m.dataset.entrance === entranceId : m.dataset.building === id)));
    $$('#layerFootprints polygon').forEach((p) => p.classList.toggle('focus', !!id && p.dataset.building === id));
    if (id && !S.layers.footprints) el.lFoot.style.display = '';     // show the selected footprint
    if (!id) el.lFoot.style.display = S.layers.footprints ? '' : 'none';
    $$('#layerFootprints polygon').forEach((p) => { p.style.display = (S.layers.footprints || p.classList.contains('focus')) ? '' : 'none'; });
  }

  async function showBuilding(id, entranceId = null) {
    const b = S.byId[id];
    if (!b) return;
    S.selected = id;
    highlightBuilding(id, entranceId);
    let ents = S.entrances.filter((e) => e.building_id === id);
    try { ents = (await api(`/api/entrances?building=${encodeURIComponent(id)}`)).entrances; } catch { /* keep cached */ }
    const frontage = S.walkNodes.filter((n) => n.building_id === id && n.node_type === 'building_frontage');
    const isGate = b.location_type === 'gate';
    el.infoBody.innerHTML = `
      <div class="info-title"><span class="sw" style="background:${isGate ? 'var(--gate)' : esc(b.building_color)}"></span>
        <h3>${isGate ? '' : b.legend_number + '. '}${esc(b.building_name)}</h3></div>
      ${b.aliases?.length ? `<p class="kv">Also searched as: ${b.aliases.map(esc).join(', ')}</p>` : ''}
      ${b.plan_label && b.plan_label !== b.building_name ? `<p class="kv">Label on the architectural plan: <b>${esc(b.plan_label)}</b></p>` : ''}
      ${isGate ? '<p class="kv">Position taken from the red dot on the annotated map. Default starting point.</p>' : ''}
      <div class="info-actions">
        <button class="btn btn-outline" type="button" data-set="start">Set as start</button>
        <button class="btn btn-primary" type="button" data-set="dest">Set as destination</button>
      </div>
      <h4>Possible entrances (${ents.length})</h4>
      ${ents.length ? `<ul class="ent-list">${ents.map((e) => `
          <li><span class="dot ${e.verification_status === 'unverified' ? 'unverified' : ''}"></span>
          <span><button type="button" data-entrance="${esc(e.entrance_id)}">${esc(e.entrance_name)}</button><br>
          <small class="muted">${esc(e.verification_status.replace(/_/g, ' '))}${e.node_id ? ' · connected to walkways' : ' · display only'}${e.note ? ' — ' + esc(e.note) : ''}</small></span></li>`).join('')}</ul>`
        : `<p class="kv">${isGate ? 'The gate itself connects to the walkway network.' : 'No blue entrance dot is drawn for this building on the annotated map.'}</p>`}
      ${frontage.length ? `<p class="kv">Routes reach it at: ${frontage.map((f) => esc(f.node_name)).join('; ')} (walkway in front of the building).</p>` : ''}`;
    el.info.hidden = false;
    revealCard(el.info);
    $$('#infoBody [data-entrance]').forEach((btn) => btn.addEventListener('click', () => {
      const e = ents.find((x) => x.entrance_id === btn.dataset.entrance);
      highlightBuilding(id, e.entrance_id);
      if (S.zoom < 2.5) setZoom(2.5);
      centerOn(e.x_percent, e.y_percent);
    }));
    $('#infoBody [data-set="start"]').addEventListener('click', () => { el.start.value = id; onSelectChange(); toast(`Start: ${b.building_name}`); });
    $('#infoBody [data-set="dest"]').addEventListener('click', () => { el.dest.value = id; onSelectChange(); toast(`Destination: ${b.building_name}`); });
  }

  function showPathInfo(pathId) {
    const p = S.altPaths.find((x) => x.path_id === pathId);
    if (!p) return;
    highlightBuilding(null);
    $$('#layerAlternatives path:not(.hit)').forEach((n) => n.classList.toggle('focus', n.dataset.path === pathId));
    const segs = S.walkSegments.filter((s) => s.path_id === pathId);
    el.infoBody.innerHTML = `
      <div class="info-title"><span class="sw" style="background:var(--alt-path)"></span><h3>${esc(p.path_name)}</h3></div>
      <p class="kv">${esc(p.description || '')}</p>
      <p class="kv"><b>Distance:</b> not measured (stored as NULL) · <b>Status:</b> ${esc(p.verification_status)}</p>
      ${p.possible_excel_match ? `<p class="kv"><b>Possible Excel match:</b> ${esc(p.possible_excel_match)}</p>` : ''}
      ${segs.some((s) => !s.is_walkable)
        ? `<p class="kv warn">Drawn over a building footprint, so it is <b>not used for routing</b> until the path is confirmed walkable and measured.</p>`
        : `<p class="kv">Modelled as ${segs.length} walkway segments (${segs.map((s) => esc(s.segment_id)).join(', ')}). Route LINES may follow it on
        alternative legs, but its length is never used as a Dijkstra distance.</p>`}`;
    el.info.hidden = false;
    revealCard(el.info);
  }

  function revealCard(card) {
    if (inMapMode()) { el.panelScroll.scrollTop = 0; setSheet('half'); }
    else card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function closeInfo() {
    el.info.hidden = true;
    S.selected = null;
    highlightBuilding(null);
    $$('#layerAlternatives path, #layerWalkways path').forEach((n) => n.classList.remove('focus'));
  }

  // ------------------------------------------------------------------ selection pins
  function onSelectChange() {
    $$('.bmark, .gmark').forEach((m) => {
      m.classList.toggle('is-start', m.dataset.building === el.start.value);
      m.classList.toggle('is-dest', m.dataset.building === el.dest.value);
    });
    drawPins();
    if (S.route && (S.route.start !== el.start.value || S.route.destination !== el.dest.value)) clearRoute(false);
  }
  function drawPins() {
    $$('.pin', el.markers).forEach((p) => p.remove());
    [['a', el.start.value, 'A'], ['b', el.dest.value, 'B']].forEach(([cls, id, letter]) => {
      const b = S.byId[id];
      if (!b) return;
      const pin = document.createElement('div');
      pin.className = `pin ${cls} drop`;
      pin.innerHTML = `<span><b>${letter}</b></span>`;
      placeAt(pin, b.x_percent, b.y_percent);
      el.markers.appendChild(pin);
    });
  }

  // ------------------------------------------------------------------ routing
  async function findRoute({ save = true } = {}) {
    const start = el.start.value, destination = el.dest.value;
    if (!start) { toast('Please choose a starting location.', true); el.start.focus(); return; }
    if (!destination) { toast('Please choose a destination.', true); el.dest.focus(); return; }
    if (start === destination) { toast('The starting location and the destination are the same.', true); return; }
    el.find.disabled = true;
    el.find.textContent = 'Calculating…';
    try {
      const r = await api('/api/route', {
        method: 'POST',
        body: JSON.stringify({ start, destination, exclude_flagged: el.flagged.checked, save_history: save }),
      });
      S.route = r;
      renderRoute(r);
      if (save) loadHistory();
    } catch (err) {
      toast(err.message, true);
    } finally {
      el.find.disabled = false;
      el.find.textContent = 'Find Shortest Route';
    }
  }

  function renderRoute(r) {
    // ---- map: draw ONLY the geometry returned by the backend
    el.lRoute.innerHTML = '';
    const lines = r.leg_geometries.filter((g) => g.length > 1);
    lines.forEach((g) => {
      const d = pathD(g);
      svg('path', { d, class: 'outline' }, el.lRoute);
      svg('path', { d, class: 'casing' }, el.lRoute);
    });
    lines.forEach((g) => svg('path', { d: pathD(g), class: 'line' }, el.lRoute));
    // lawn links (traced_inferred) are drawn dashed on top
    r.route_segments.filter((s) => s.verification_status === 'traced_inferred').forEach((s) => {
      const seg = S.segById[s.segment_id];
      if (seg) svg('path', { d: pathD(seg.geometry_coordinates), class: 'inferred' }, el.lRoute);
    });
    lines.forEach((g) => svg('path', { d: pathD(g), class: 'flow' }, el.lRoute));
    $$('.bmark, .gmark').forEach((m) => m.classList.toggle('dim', !r.route.some((s) => s.building_id === m.dataset.building)));

    // ---- panel
    $('#resultDistance').textContent = fmt(r.distance);
    $('#resultTime').textContent = `≈ ${r.walking_time_min} min walk`;
    $('#resultTime').title = `Estimated at ${r.walking_speed_m_per_min} m per minute`;
    const badge = $('#geoBadge');
    badge.className = `geo-badge ${r.geometry_status}`;
    badge.textContent = { traced: 'Traced walkway line', partial: 'Line partly drawn', none: 'Distance only' }[r.geometry_status];
    $('#resultStops').innerHTML = r.route.map((s, i) => `<li><span class="chip ${i === 0 ? 'a' : i === r.route.length - 1 ? 'b' : ''}">${esc(s.short_name || s.building_name)}</span></li>`).join('');
    $('#resultDirect').innerHTML = r.hops > 1
      ? `Dijkstra found a ${r.hops}-leg route.${r.direct_pair ? ` The direct measurement is ${fmt(r.direct_pair.distance)} m (${esc(r.direct_pair.option_label.toLowerCase())}), so going via ${esc(r.route.slice(1, -1).map((s) => s.short_name).join(', '))} saves ${fmt(r.direct_pair.saving)} m.` : ''}`
      : `Direct measured walk between the two locations.`;
    $('#resultWarnings').innerHTML = r.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join('');
    $('#resultLegs').innerHTML = r.legs.map((l) => `
      <div class="leg">
        <div class="leg-head"><span>${r.legs.length > 1 ? `Leg ${l.index}: ` : ''}${esc(S.byId[l.from]?.short_name || l.from_name)} → ${esc(S.byId[l.to]?.short_name || l.to_name)}</span><span>${fmt(l.distance)} m</span></div>
        <div class="leg-meta"><span class="tag ${l.option_type === 'main' ? '' : 'alt'}">${esc(l.option_label)}</span>${l.flagged ? '<span class="tag flag">flagged</span>' : ''}
          ${esc(l.description)} · Excel row ${l.excel_row}${l.walked_in_recorded_direction ? '' : ' · measured in the opposite direction'}</div>
        ${l.options.length > 1 ? `<table class="options"><thead><tr><th>Measured option</th><th>Distance</th><th>Description</th></tr></thead><tbody>
          ${l.options.map((o) => `<tr class="${o.selected ? 'sel' : ''}"><td>${esc(o.label)}</td><td>${o.distance == null ? '–' : fmt(o.distance) + ' m'}</td><td>${esc(o.description)}${o.status === 'flagged' ? ' ⚠' : ''}</td></tr>`).join('')}
          </tbody></table>` : ''}
        ${l.directions.length ? `<ol class="directions">${l.directions.map((d) => `<li class="${d.segment_type === 'arrival' ? 'arrive' : ''}">${esc(d.text)}</li>`).join('')}</ol>` : ''}
        ${l.geometry_note ? `<p class="geo-note">${esc(l.geometry_note)}</p>` : ''}
      </div>`).join('');
    const a = r.algorithm;
    $('#algoStats').innerHTML = `
      <div class="stat-grid">
        <div><b>${a.graph_vertices}</b>vertices in the building graph</div>
        <div><b>${a.graph_edges}</b>measured edges (undirected)</div>
        <div><b>${a.vertices_finalised}</b>vertices finalised</div>
        <div><b>${a.edges_examined}</b>edges examined</div>
        <div><b>${a.relaxations}</b>successful relaxations</div>
        <div><b>${a.heap_pushes} / ${a.heap_pops}</b>heap pushes / pops</div>
      </div>
      <p class="visit-order"><b>Order popped from the min-heap:</b> ${a.visit_order.map((v) => esc(S.byId[v]?.short_name || v)).join(' → ')}</p>`;
    $('#geoExplain').textContent = r.geometry_explanation;
    el.result.hidden = false;
    el.info.hidden = true;
    S.selected = null;
    highlightBuilding(null);

    if (inMapMode()) setSheet('peek');        // phones: map first, details one swipe away
    fitRoute(r);
    if (!inMapMode() && window.matchMedia('(max-width: 960px)').matches) el.viewport.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function fitRoute(r, smooth = true) {
    const pts = r.leg_geometries.filter((g) => g.length > 1).flat().concat(r.route.map((s) => [s.x_percent, s.y_percent]));
    fitBounds(pts, smooth);
  }

  function clearRoute(resetSelects = true) {
    S.route = null;
    el.lRoute.innerHTML = '';
    el.result.hidden = true;
    $$('.bmark, .gmark').forEach((m) => m.classList.remove('dim'));
    if (resetSelects) {
      fillSelects();
      el.dest.value = '';
      el.search.value = '';
      el.flagged.checked = false;
      closeInfo();
      onSelectChange();
      fitMap();
      setSheet('half');
    }
  }

  // ------------------------------------------------------------------ history
  async function loadHistory() {
    try {
      const h = await api('/api/history?limit=24');
      if (!h.history.length) {
        el.history.innerHTML = '<div class="empty-state">No searches yet. Find a route on the campus map and it will be saved here.</div>';
        return;
      }
      el.history.innerHTML = h.history.map((x) => {
        const when = new Date(x.search_timestamp);
        const stops = (x.route_result.route || []).map((id) => S.byId[id]?.short_name || id);
        return `<article class="hist">
          <div class="hist-top"><div class="hist-route">${esc(S.byId[x.start_location_id]?.short_name || x.start_name)}<span class="arrow">→</span>${esc(S.byId[x.destination_location_id]?.short_name || x.destination_name)}</div>
          <div class="hist-dist">${fmt(x.total_distance_m)} m</div></div>
          <div class="hist-meta">${stops.length > 2 ? `Via ${esc(stops.slice(1, -1).join(', '))} · ` : ''}${x.route_result.exclude_flagged ? 'flagged values ignored · ' : ''}${isNaN(when) ? esc(x.search_timestamp) : when.toLocaleString()}</div>
          <button class="btn btn-outline" type="button" data-history="${x.history_id}">View on map</button>
        </article>`;
      }).join('');
      $$('[data-history]').forEach((btn) => btn.addEventListener('click', () => {
        const x = h.history.find((y) => String(y.history_id) === btn.dataset.history);
        el.start.value = x.start_location_id;
        el.dest.value = x.destination_location_id;
        el.flagged.checked = !!x.route_result.exclude_flagged;
        onSelectChange();
        if (isMobile()) enterMapMode(); else document.getElementById('navigate').scrollIntoView({ behavior: 'smooth' });
        findRoute({ save: false }).then(() => {
          if (S.route && Math.abs(S.route.distance - x.total_distance_m) > 0.005) {
            toast(`Note: with today's data this route is ${fmt(S.route.distance)} m (saved result: ${fmt(x.total_distance_m)} m).`);
          }
        });
      }));
    } catch (err) {
      el.history.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
    }
  }

  async function clearHistory() {
    if (!window.confirm('Delete all saved route searches from the database?')) return;
    try {
      const r = await adminApi('/api/history', { method: 'DELETE' });
      toast(`Deleted ${r.deleted} saved search${r.deleted === 1 ? '' : 'es'}.`);
      loadHistory();
    } catch (err) { toast(err.message, true); }
  }

  // ------------------------------------------------------------------ home page figures
  // (internal workbook notes stay in the backend: GET /api/data-report)
  async function loadSiteStats() {
    try {
      const r = await api('/api/data-report');
      const w = r.workbook;
      $('[data-stat="pairs"]').textContent = w.counts ? w.counts.unique_pairs : '–';
    } catch (err) {
      $('[data-stat="pairs"]').textContent = '–';
    }
  }

  // ------------------------------------------------------------------ events
  function bindEvents() {
    bindMapInteraction();
    bindSheet();
    el.find.addEventListener('click', () => findRoute());
    el.reset.addEventListener('click', () => { clearRoute(true); toast('Route cleared.'); });
    el.swap.addEventListener('click', () => {
      const hadRoute = !!S.route;
      const a = el.start.value;
      el.start.value = el.dest.value;
      el.dest.value = a;
      onSelectChange();
      if (hadRoute && el.start.value && el.dest.value) findRoute();   // recalculate the reversed route
    });
    el.start.addEventListener('change', onSelectChange);
    el.dest.addEventListener('change', onSelectChange);
    el.flagged.addEventListener('change', () => { if (S.route) findRoute(); });
    $('#infoClose').addEventListener('click', closeInfo);
    $$('.legend-list input[data-layer]').forEach((cb) => cb.addEventListener('change', () => { applyLayers(); highlightBuilding(S.selected); }));

    el.search.addEventListener('input', renderSearch);
    el.search.addEventListener('focus', () => { if (el.search.value) renderSearch(); });
    el.search.addEventListener('blur', () => setTimeout(closeSearch, 120));
    el.search.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); moveSearch(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); moveSearch(-1); }
      else if (e.key === 'Enter') {
        const items = $$('#searchList li[data-building]');
        if (items[searchIndex]) { e.preventDefault(); pickSearch(items[searchIndex].dataset.building); }
      } else if (e.key === 'Escape') closeSearch();
    });

    $('#refreshHistory').addEventListener('click', loadHistory);
    $('#clearHistory').addEventListener('click', clearHistory);
    el.viewport.addEventListener('click', (e) => {
      const dragged = S.justDragged && Date.now() - S.justDragged < 250;
      if (!e.target.closest('button') && !dragged && S.selected) closeInfo();
    });

    // header highlight for the visible section
    const links = $$('.site-nav a');
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((en) => { if (en.isIntersecting) links.forEach((a) => a.classList.toggle('active', a.dataset.nav === en.target.id)); });
    }, { rootMargin: '-45% 0px -50% 0px' });
    ['home', 'navigate', 'history', 'about', 'team'].forEach((id) => obs.observe(document.getElementById(id)));
    onSelectChange();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
