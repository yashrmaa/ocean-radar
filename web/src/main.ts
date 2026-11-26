import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';

import * as maplibregl from 'maplibre-gl';
// MapLibre 6 finds its worker relative to its own module, which breaks once bundled.
// Ship the (self-contained) worker file as an asset and point MapLibre at it.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import { TripsLayer } from '@deck.gl/geo-layers';
import { PathStyleExtension } from '@deck.gl/extensions';
import type { Layer, PickingInfo } from '@deck.gl/core';

import { esc, renderAbout, renderCard } from './card';
import type { AnimalDetail, Chunk, Group } from './data';
import { loadAnimal, loadChunk, loadIndex, makeClock } from './data';

const COLORS: Record<Group, [number, number, number]> = {
  bird: [220, 160, 255], mammal: [240, 200, 90], bat: [190, 150, 120], reptile: [170, 220, 80],
  amphibian: [60, 220, 200], insect: [255, 130, 200],
  fish: [255, 150, 50], shark: [255, 86, 96], whale: [90, 205, 255], seal: [96, 140, 255],
  turtle: [120, 230, 140], other: [200, 200, 200],
};
const LABELS: Record<Group, string> = {
  bird: 'Birds', mammal: 'Land mammals', bat: 'Bats', reptile: 'Reptiles', amphibian: 'Amphibians',
  insect: 'Insects', fish: 'Fish', shark: 'Sharks', whale: 'Whales & dolphins', seal: 'Seals & sea lions',
  turtle: 'Sea turtles', other: 'Other',
};
const TRAIL_DAYS = 90;
const GAP_DAYS = 5;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

interface Head { i: number; pos: [number, number] }
interface TripData {
  length: number; startIndices: Uint32Array;
  attributes: { getPath: { value: Float32Array; size: 2 }; getTimestamps: { value: Float32Array; size: 1 };
    getColor: { value: Uint8Array; size: 4 } };
  animalOf: Int32Array; // path index -> animal index
}

const state = {
  now: 0, playing: true, speed: 45, mode: 'replay' as 'replay' | 'last',
  groups: new Set<Group>(), selected: -1, hover: -1, detail: null as AnimalDetail | null,
};

async function main() {
  const index = await loadIndex();
  const clock = makeClock(index.epoch);
  const groupOf = (i: number) => index.species[String(index.animals[i].sp)].grp;
  const tStart = clock.toDay(index.data_start);
  const tEnd = clock.toDay(index.data_end);
  // Start replay at the beginning of the year with the most animals tracked (the data reaches back to
  // the 1970s, when only a handful were).
  const yearCounts = new Map<number, number>();
  for (const a of index.animals) {
    for (let y = clock.year(a.s); y <= clock.year(a.e); y++) yearCounts.set(y, (yearCounts.get(y) ?? 0) + 1);
  }
  const busiest = [...yearCounts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  const tDefault = busiest ? Math.max(tStart, clock.toDay(`${busiest}-01-01`)) : tStart;
  state.now = tDefault;
  Object.values(index.species).forEach((s) => state.groups.add(s.grp));

  // ---------- map ----------
  maplibregl.setWorkerUrl(maplibreWorkerUrl);
  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json',
    center: [10, 25], zoom: 1.3, attributionControl: { compact: true },
  });
  const overlay = new MapboxOverlay({
    interleaved: false, layers: [],
    pickingRadius: 10, // thin tracks are hard to hit; accept clicks within 10 px
    // Fires only when no layer handled the click: empty map closes the card, like a map app.
    onClick: (info: PickingInfo) => { if (animalAt(info) < 0 && state.selected >= 0) select(-1); },
    onHover: (info: PickingInfo) => setHover(animalAt(info)),
    getTooltip: (info: PickingInfo) => {
      const i = animalAt(info);
      if (i < 0 || i === state.selected) return null;
      const a = index.animals[i], sp = index.species[String(a.sp)];
      const who = [a.stage, a.sex].filter(Boolean).join(' ');
      return {
        html: `<b>${esc(sp.common)}</b><br><span>${esc([who, `${clock.fmt(a.s).slice(0, 4)}–${clock.fmt(a.e).slice(0, 4)}`].filter(Boolean).join(' · '))}</span>`,
        style: { background: 'rgba(11,17,33,.95)', color: '#e8edfb', border: '1px solid #24304f',
          borderRadius: '8px', padding: '6px 9px', font: '13px/1.35 system-ui, sans-serif', pointerEvents: 'none' },
      };
    },
    getCursor: ({ isDragging, isHovering }: { isDragging: boolean; isHovering: boolean }) =>
      isDragging ? 'grabbing' : isHovering ? 'pointer' : 'default',
  } as any);
  map.addControl(overlay);

  // ---------- chunks ----------
  const loaded = new Map<number, Chunk>();
  const tripCache = new Map<string, TripData>();
  const groupKey = () => [...state.groups].sort().join(',');

  async function ensureChunks(day: number) {
    const y = clock.year(day);
    const want = [y - 1, y, y + 1].filter((v) => index.years.includes(v));
    await Promise.all(want.map(async (v) => { if (!loaded.has(v)) loaded.set(v, await loadChunk(v)); }));
  }

  function tripData(c: Chunk): TripData {
    const key = `${c.year}|${groupKey()}`;
    let td = tripCache.get(key);
    if (td) return td;
    const keep = c.animals.filter(([i]) => state.groups.has(groupOf(i)));
    const nVerts = keep.reduce((s, [, , len]) => s + len, 0);
    const path = new Float32Array(2 * nVerts), ts = new Float32Array(nVerts), col = new Uint8Array(4 * nVerts);
    const starts = new Uint32Array(keep.length), animalOf = new Int32Array(keep.length);
    let v = 0;
    keep.forEach(([i, start, len], k) => {
      starts[k] = v; animalOf[k] = i;
      const [r, g, b] = COLORS[groupOf(i)];
      for (let j = start; j < start + len; j++, v++) {
        path[2 * v] = c.lon[j]; path[2 * v + 1] = c.lat[j]; ts[v] = c.day[j];
        col[4 * v] = r; col[4 * v + 1] = g; col[4 * v + 2] = b; col[4 * v + 3] = 255;
      }
    });
    td = { length: keep.length, startIndices: starts, animalOf,
      attributes: { getPath: { value: path, size: 2 }, getTimestamps: { value: ts, size: 1 }, getColor: { value: col, size: 4 } } };
    tripCache.set(key, td);
    return td;
  }

  function heads(day: number): Head[] {
    const seen = new Map<number, Head>();
    for (const c of loaded.values()) {
      for (const [i, start, len] of c.animals) {
        if (seen.has(i) || !state.groups.has(groupOf(i))) continue;
        const d = c.day;
        if (day < d[start] || day > d[start + len - 1]) continue;
        let lo = start, hi = start + len - 1;
        while (hi - lo > 1) { const m = (lo + hi) >> 1; if (d[m] <= day) lo = m; else hi = m; }
        const f = d[hi] === d[lo] ? 0 : (day - d[lo]) / (d[hi] - d[lo]);
        seen.set(i, { i, pos: [c.lon[lo] + (c.lon[hi] - c.lon[lo]) * f, c.lat[lo] + (c.lat[hi] - c.lat[lo]) * f] });
      }
    }
    return [...seen.values()];
  }

  // ---------- selection ----------
  function selectedLayers(): Layer[] {
    const d = state.detail;
    if (!d || state.selected < 0) return [];
    const solid: [number, number][][] = [], gaps: [number, number][][] = [];
    let cur: [number, number][] = [];
    d.track.forEach((p, k) => {
      const prev = d.track[k - 1];
      if (prev && p[0] - prev[0] > GAP_DAYS) {
        if (cur.length > 1) solid.push(cur);
        gaps.push([[prev[1], prev[2]], [p[1], p[2]]]);
        cur = [];
      }
      cur.push([p[1], p[2]]);
    });
    if (cur.length > 1) solid.push(cur);
    const fixes = d.track.filter((p) => p[3] === 0);
    return [
      new PathLayer({ id: 'sel-solid', data: solid, getPath: (p: [number, number][]) => p,
        getColor: [255, 255, 255, 235], widthMinPixels: 2.5, jointRounded: true, capRounded: true,
        pickable: true, onClick: onPick }),
      new PathLayer({ id: 'sel-gaps', data: gaps, getPath: (p: [number, number][]) => p,
        getColor: [255, 255, 255, 170], widthMinPixels: 1.5, getDashArray: [4, 4],
        extensions: [new PathStyleExtension({ dash: true })] } as any),
      new ScatterplotLayer({ id: 'sel-fixes', data: fixes, getPosition: (p: AnimalDetail['track'][number]) => [p[1], p[2]],
        getFillColor: [255, 255, 255, 200], radiusMinPixels: 1.5, radiusMaxPixels: 2.5 }),
    ];
  }

  // ---------- picking & hover ----------
  // Which animal (index into index.animals) a picking result refers to, or -1.
  function animalAt(info: PickingInfo): number {
    const id = info.layer?.id ?? '';
    if (!info.picked || info.index < 0) return -1;
    if (id.startsWith('trips-')) return ((info.layer!.props.data as unknown) as TripData).animalOf[info.index] ?? -1;
    if (id === 'heads') return (info.object as Head).i;
    if (id === 'last') return info.object as number;
    if (id === 'hover') return state.hover;
    if (id.startsWith('sel-')) return state.selected;
    return -1;
  }

  // Returning true marks the click handled, so the overlay's "empty map" handler doesn't run.
  const onPick = (info: PickingInfo) => {
    const i = animalAt(info);
    if (i >= 0 && i !== state.selected) select(i, { fly: window.innerWidth <= 720 });
    return i >= 0;
  };

  function setHover(i: number) {
    if (i === state.hover) return;
    state.hover = i;
    // deck.gl's getCursor doesn't reach MapLibre's canvas in overlay mode, so set it here.
    map.getCanvas().style.cursor = i >= 0 ? 'pointer' : '';
    render();
  }

  // The hovered animal's whole track within the loaded years, drawn brighter on top.
  function hoverLayer(): Layer | null {
    const i = state.hover;
    if (i < 0 || i === state.selected || state.mode !== 'replay') return null;
    const path: [number, number][] = [];
    for (const c of [...loaded.values()].sort((a, b) => a.year - b.year)) {
      const seg = c.animals.find(([k]) => k === i);
      if (!seg) continue;
      for (let j = seg[1]; j < seg[1] + seg[2]; j++) path.push([c.lon[j], c.lat[j]]);
    }
    if (path.length < 2) return null;
    const [r, g, b] = COLORS[groupOf(i)];
    return new PathLayer({
      id: 'hover', data: [path], getPath: (p: [number, number][]) => p,
      getColor: [Math.min(255, r + 60), Math.min(255, g + 60), Math.min(255, b + 60), 235],
      widthMinPixels: 3, jointRounded: true, capRounded: true, pickable: true, onClick: onPick,
    });
  }

  // ---------- render ----------

  function render() {
    const layers: Layer[] = [];
    let count = 0;
    if (state.mode === 'replay') {
      const y = clock.year(state.now);
      for (const c of loaded.values()) {
        if (c.year < y - 1 || c.year > y) continue;
        layers.push(new TripsLayer({
          id: `trips-${c.year}`, data: tripData(c) as any, _pathType: 'open',
          currentTime: state.now, trailLength: TRAIL_DAYS, fadeTrail: true,
          widthMinPixels: 2, jointRounded: true, capRounded: true,
          opacity: state.selected >= 0 ? 0.5 : 0.85, pickable: true, onClick: onPick,
        } as any));
      }
      const hs = heads(state.now);
      count = hs.length;
      layers.push(new ScatterplotLayer<Head>({
        id: 'heads', data: hs, getPosition: (h) => h.pos,
        getFillColor: (h) => [...COLORS[groupOf(h.i)], 255] as [number, number, number, number],
        getLineColor: [255, 255, 255, 170], stroked: true, lineWidthMinPixels: 0.6,
        radiusMinPixels: 3.5, radiusMaxPixels: 7, pickable: true, onClick: onPick,
        updateTriggers: { getFillColor: groupKey() },
      }));
    } else {
      const rows = index.animals.map((_, i) => i).filter((i) => state.groups.has(groupOf(i)));
      count = rows.length;
      layers.push(new ScatterplotLayer<number>({
        id: 'last', data: rows, getPosition: (i) => index.animals[i].ll,
        getFillColor: (i) => {
          const a = index.animals[i], age = (tEnd - a.e) / 365;
          const alpha = i === state.hover ? 255 : Math.round(255 * Math.max(0.18, 1 - age / 10));
          return [...COLORS[groupOf(i)], alpha] as any;
        },
        getRadius: (i) => (i === state.hover || i === state.selected ? 2 : 1),
        radiusUnits: 'pixels', radiusScale: 3.2, stroked: true, lineWidthMinPixels: 0.5,
        getLineColor: (i) => (i === state.selected ? [255, 255, 255, 255] : [0, 0, 0, 0]),
        pickable: true, onClick: onPick,
        updateTriggers: { getFillColor: [groupKey(), state.hover], getRadius: [state.hover, state.selected],
          getLineColor: state.selected },
      }));
    }
    const hl = hoverLayer();
    if (hl) layers.push(hl);
    layers.push(...selectedLayers());
    overlay.setProps({ layers });
    $('date').textContent = state.mode === 'replay' ? clock.fmt(state.now) : `Data to ${index.data_end}`;
    $('count').textContent = state.mode === 'replay'
      ? `${count.toLocaleString()} animals being tracked`
      : `${count.toLocaleString()} animals, faded by how long ago they were seen`;
    ($('slider') as HTMLInputElement).value = String(Math.round(state.now));
  }

  async function setTime(day: number) {
    state.now = Math.min(Math.max(day, tStart), tEnd);
    await ensureChunks(state.now);
    render();
  }

  // ---------- card ----------
  const card = $('card');
  async function select(i: number, opts: { fly?: boolean; jump?: boolean } = {}) {
    if (i < 0) {
      state.selected = -1; state.detail = null; card.hidden = true; writeHash(); render(); return;
    }
    const row = index.animals[i];
    state.selected = i;
    state.detail = await loadAnimal(row);
    card.innerHTML = renderCard(index, row, state.detail);
    card.hidden = false;
    card.scrollTop = 0;
    if (opts.jump && (state.now < row.s || state.now > row.e)) await setTime(row.s);
    if (opts.fly) fitTrack();
    writeHash();
    render();
  }

  function fitTrack() {
    const d = state.detail;
    if (!d) return;
    const lons = d.track.map((p) => p[1]), lats = d.track.map((p) => p[2]);
    const mobile = window.innerWidth <= 720;
    map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], {
      padding: mobile ? { top: 120, bottom: window.innerHeight * 0.46 + 20, left: 30, right: 30 }
        : { top: 80, bottom: 110, left: 340, right: 380 },
      maxZoom: 6, duration: 900,
    });
  }

  card.addEventListener('click', async (e) => {
    const t = e.target as HTMLElement;
    if (t.closest('[data-close]')) return select(-1);
    const act = t.closest<HTMLElement>('[data-act]')?.dataset.act;
    if (act === 'fit') fitTrack();
    if (act === 'start' && state.selected >= 0) {
      await setTime(index.animals[state.selected].s);
      setPlaying(true);
    }
    if (act === 'share') {
      writeHash(true);
      await navigator.clipboard?.writeText(location.href).catch(() => {});
      t.textContent = 'Link copied';
    }
  });

  // ---------- URL ----------
  function writeHash(withTime = false) {
    const p = new URLSearchParams();
    if (state.selected >= 0) p.set('a', index.animals[state.selected].id);
    if (withTime || state.selected >= 0) p.set('t', clock.fmt(state.now));
    history.replaceState(null, '', p.toString() ? `#${p}` : location.pathname + location.search);
  }

  // ---------- controls ----------
  const groupsEl = $('groups');
  const counts: Partial<Record<Group, number>> = {};
  index.animals.forEach((_, i) => { const g = groupOf(i); counts[g] = (counts[g] ?? 0) + 1; });
  (Object.keys(LABELS) as Group[]).filter((g) => counts[g]).forEach((g) => {
    const l = document.createElement('label');
    l.innerHTML = `<input type="checkbox" checked><i style="background:rgb(${COLORS[g]})"></i>${LABELS[g]} <span style="opacity:.6">${counts[g]}</span>`;
    l.querySelector('input')!.addEventListener('change', (e) => {
      if ((e.target as HTMLInputElement).checked) state.groups.add(g); else state.groups.delete(g);
      render();
    });
    groupsEl.appendChild(l);
  });

  const slider = $('slider') as HTMLInputElement;
  slider.min = String(Math.floor(tStart));
  slider.max = String(Math.ceil(tEnd));
  slider.addEventListener('input', () => setTime(+slider.value));

  const playBtn = $('play');
  function setPlaying(p: boolean) {
    state.playing = p;
    playBtn.textContent = p ? '❚❚' : '▶';
    playBtn.setAttribute('aria-label', p ? 'Pause' : 'Play');
  }
  playBtn.addEventListener('click', () => setPlaying(!state.playing));
  ($('speed') as HTMLSelectElement).addEventListener('change', (e) => { state.speed = +(e.target as HTMLSelectElement).value; });

  document.querySelectorAll<HTMLInputElement>('input[name=mode]').forEach((r) => r.addEventListener('change', () => {
    state.mode = r.value as 'replay' | 'last';
    document.querySelector('.timebar')!.classList.toggle('last-mode', state.mode === 'last');
    render();
  }));

  // Search: species (common or scientific), tag id, tagging site.
  const search = $('search') as HTMLInputElement, results = $('results');
  const haystack = index.animals.map((a) => {
    const sp = index.species[String(a.sp)];
    return `${sp.common} ${sp.sci} ${a.id} ${a.site ?? ''} ${a.sex ?? ''} ${a.stage ?? ''}`.toLowerCase();
  });
  search.addEventListener('input', () => {
    const terms = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    if (!terms.length) { results.hidden = true; return; }
    const hits: number[] = [];
    for (let i = 0; i < haystack.length && hits.length < 12; i++) {
      if (terms.every((t) => haystack[i].includes(t))) hits.push(i);
    }
    results.innerHTML = hits.length ? hits.map((i) => {
      const a = index.animals[i], sp = index.species[String(a.sp)];
      const bits = [[a.stage, a.sex].filter(Boolean).join(' '), `${clock.fmt(a.s).slice(0, 4)}–${clock.fmt(a.e).slice(0, 4)}`,
        a.site, a.story ? 'story' : ''].filter(Boolean);
      return `<li role="option" data-i="${i}">${esc(sp.common)}<small>${esc(bits.join(' · '))}</small></li>`;
    }).join('') : '<li aria-disabled="true">No matches</li>';
    results.hidden = false;
  });
  results.addEventListener('click', (e) => {
    const li = (e.target as HTMLElement).closest<HTMLElement>('li[data-i]');
    if (!li) return;
    results.hidden = true;
    search.value = '';
    select(+li.dataset.i!, { fly: true, jump: true });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { results.hidden = true; if (!$('about').hidden) $('about').hidden = true; else select(-1); }
    if (e.key === ' ' && document.activeElement === document.body) { e.preventDefault(); setPlaying(!state.playing); }
  });

  const filtersBtn = $('filters-btn');
  filtersBtn.addEventListener('click', () => {
    const open = $('controls').classList.toggle('open');
    filtersBtn.setAttribute('aria-expanded', String(open));
  });

  $('about-body').innerHTML = renderAbout(index);
  $('about-btn').addEventListener('click', () => { $('about').hidden = false; });
  $('about').addEventListener('click', (e) => { if ((e.target as HTMLElement).closest('[data-close]')) $('about').hidden = true; });

  // ---------- loop ----------
  let last = performance.now();
  function tick(ts: number) {
    const dt = Math.min((ts - last) / 1000, 0.1);
    last = ts;
    if (state.playing && state.mode === 'replay') {
      let next = state.now + dt * state.speed;
      if (next > tEnd) next = tStart;
      const crossed = clock.year(next) !== clock.year(state.now);
      state.now = next;
      if (crossed) ensureChunks(state.now).then(render);
      render();
    }
    requestAnimationFrame(tick);
  }

  // ---------- start ----------
  // Dev-only handle for debugging in the console (stripped from production builds).
  if (import.meta.env.DEV) Object.assign(window, { __radar: { map, state, index, heads: () => heads(state.now) } });

  // ---------- resume where the viewer left off ----------
  // The date, map view and mode are kept in this browser (localStorage), so a reload, or a phone
  // reloading the tab in the background, picks up where you were. A link with #t= or #a= wins.
  const RESUME_KEY = 'wildliferadar:resume';
  interface Resume { day: number; lng: number; lat: number; zoom: number; mode: 'replay' | 'last'; playing?: boolean }
  function readResume(): Resume | null {
    try {
      const r = JSON.parse(localStorage.getItem(RESUME_KEY) || 'null');
      return r && Number.isFinite(r.day) && Number.isFinite(r.zoom) ? r : null;
    } catch { return null; }
  }
  function saveResume() {
    try {
      const c = map.getCenter();
      const r: Resume = { day: state.now, lng: c.lng, lat: c.lat, zoom: map.getZoom(), mode: state.mode, playing: state.playing };
      localStorage.setItem(RESUME_KEY, JSON.stringify(r));
    } catch { /* storage blocked (private mode): nothing to resume, which is fine */ }
  }
  setInterval(saveResume, 2000);
  document.addEventListener('visibilitychange', () => { if (document.hidden) saveResume(); });
  window.addEventListener('pagehide', saveResume);

  function setMode(mode: 'replay' | 'last') {
    state.mode = mode;
    document.querySelectorAll<HTMLInputElement>('input[name=mode]').forEach((r) => { r.checked = r.value === mode; });
    document.querySelector('.timebar')!.classList.toggle('last-mode', mode === 'last');
  }

  async function applyHash(initial: boolean) {
    const p = new URLSearchParams(location.hash.slice(1));
    const t = p.get('t');
    const resume = initial && !t && !p.get('a') ? readResume() : null;
    if (resume) {
      map.jumpTo({ center: [resume.lng, resume.lat], zoom: resume.zoom });
      if (resume.mode === 'last') setMode('last');
      if (resume.playing === false) setPlaying(false);
    }
    if (t || initial) await setTime(t ? clock.toDay(t) : resume ? resume.day : tDefault);
    const a = p.get('a');
    const i = a ? index.animals.findIndex((r) => r.id === a) : -1;
    if (i >= 0) { setPlaying(false); await select(i, { fly: true }); }
  }
  map.on('load', async () => {
    await applyHash(true);
    window.addEventListener('hashchange', () => applyHash(false));
    requestAnimationFrame(tick);
  });
}

main().catch((err) => {
  document.body.insertAdjacentHTML('beforeend',
    `<div class="panel" style="top:40%;left:50%;transform:translate(-50%,-50%);padding:16px">Couldn't load the map data: ${String(err)}</div>`);
});
