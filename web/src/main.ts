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
import type { AnimalDetail, AnimalRow, Chunk, Group } from './data';
import { loadAnimal, loadChunk, loadIndex, makeClock } from './data';

const COLORS: Record<Group, [number, number, number]> = {
  fish: [255, 170, 60], shark: [255, 86, 96], whale: [90, 205, 255], seal: [96, 140, 255],
  turtle: [120, 230, 140], bird: [220, 160, 255], other: [200, 200, 200],
};
const LABELS: Record<Group, string> = {
  fish: 'Fish', shark: 'Sharks', whale: 'Whales & dolphins', seal: 'Seals & sea lions',
  turtle: 'Turtles', bird: 'Seabirds', other: 'Other',
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
  groups: new Set<Group>(), selected: -1, detail: null as AnimalDetail | null,
};

async function main() {
  const index = await loadIndex();
  const clock = makeClock(index.epoch);
  const groupOf = (i: number) => index.species[String(index.animals[i].sp)].grp;
  const tStart = clock.toDay(index.data_start);
  const tEnd = clock.toDay(index.data_end);
  state.now = tStart;
  Object.values(index.species).forEach((s) => state.groups.add(s.grp));

  // ---------- map ----------
  maplibregl.setWorkerUrl(maplibreWorkerUrl);
  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json',
    center: [-150, 20], zoom: 1.4, attributionControl: { compact: true },
  });
  const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
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
        getColor: [255, 255, 255, 235], widthMinPixels: 2.5, jointRounded: true, capRounded: true }),
      new PathLayer({ id: 'sel-gaps', data: gaps, getPath: (p: [number, number][]) => p,
        getColor: [255, 255, 255, 170], widthMinPixels: 1.5, getDashArray: [4, 4],
        extensions: [new PathStyleExtension({ dash: true })] } as any),
      new ScatterplotLayer({ id: 'sel-fixes', data: fixes, getPosition: (p: AnimalDetail['track'][number]) => [p[1], p[2]],
        getFillColor: [255, 255, 255, 200], radiusMinPixels: 1.5, radiusMaxPixels: 2.5 }),
    ];
  }

  // ---------- render ----------
  const onPick = (info: PickingInfo) => {
    const obj = info.object as Head | AnimalRow | undefined;
    if (info.layer?.id.startsWith('trips-') && info.index >= 0) {
      const td = (info.layer.props.data as unknown) as TripData;
      select(td.animalOf[info.index]);
    } else if (obj && 'i' in obj) select(obj.i);
    else if (obj && 'id' in obj) select(index.animals.indexOf(obj));
  };

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
          widthMinPixels: 1.6, jointRounded: true, capRounded: true,
          opacity: state.selected >= 0 ? 0.3 : 0.85, pickable: true, onClick: onPick,
        } as any));
      }
      const hs = heads(state.now);
      count = hs.length;
      layers.push(new ScatterplotLayer<Head>({
        id: 'heads', data: hs, getPosition: (h) => h.pos,
        getFillColor: (h) => [...COLORS[groupOf(h.i)], 255] as [number, number, number, number],
        getLineColor: [255, 255, 255, 170], stroked: true, lineWidthMinPixels: 0.6,
        radiusMinPixels: 3.2, radiusMaxPixels: 7, pickable: true, onClick: onPick,
        updateTriggers: { getFillColor: groupKey() },
      }));
    } else {
      const rows = index.animals.filter((_, i) => state.groups.has(groupOf(i)));
      count = rows.length;
      layers.push(new ScatterplotLayer<AnimalRow>({
        id: 'last', data: rows, getPosition: (a) => a.ll,
        getFillColor: (a) => {
          const age = (tEnd - a.e) / 365;
          return [...COLORS[index.species[String(a.sp)].grp], Math.round(255 * Math.max(0.18, 1 - age / 10))] as any;
        },
        radiusMinPixels: 2.6, radiusMaxPixels: 6, pickable: true, onClick: onPick,
        updateTriggers: { getFillColor: groupKey() },
      }));
    }
    layers.push(...selectedLayers());
    overlay.setProps({ layers, getCursor: ({ isHovering }) => (isHovering ? 'pointer' : 'grab') });
    $('date').textContent = state.mode === 'replay' ? clock.fmt(state.now) : `Data to ${index.data_end}`;
    $('count').textContent = state.mode === 'replay'
      ? `${count.toLocaleString()} animals at sea`
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
  async function applyHash(initial: boolean) {
    const p = new URLSearchParams(location.hash.slice(1));
    const t = p.get('t');
    if (t || initial) await setTime(t ? clock.toDay(t) : tStart);
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
