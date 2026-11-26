// Loading and decoding the static assets written by pipeline/emit.py.

export type Group = 'bird' | 'mammal' | 'bat' | 'reptile' | 'amphibian' | 'insect' | 'fish' | 'shark' | 'whale' | 'seal' | 'turtle' | 'other';

export interface Species { common: string; sci: string; grp: Group; iucn: string | null; sensitive: boolean }
export interface Photo { src: string; file: string; credit: string; license: string; license_url: string | null; wiki: string }
export interface Source { name: string; url: string; license: string; license_url: string | null }

export interface AnimalRow {
  id: string; sp: number; sex: string | null; stage: string | null; src: string; site: string | null;
  s: number; e: number; shard: number; noisy: boolean; story: boolean; ll: [number, number];
}

export interface Index {
  generated: string; epoch: string; data_start: string; data_end: string;
  embargo_days: { default: number; sensitive: number };
  years: number[];
  sources: Record<string, Source>;
  species: Record<string, Species>;
  photos: Record<string, Record<string, Photo>>;
  animals: AnimalRow[];
}

export interface Chunk {
  year: number;
  lon: Float32Array; lat: Float32Array; day: Float32Array;
  animals: [number, number, number][]; // [animal index, start, length]
}

export interface Fact { v: unknown; d: string; e: Record<string, unknown> }
export interface Sentence { text: string; facts?: string[]; context?: string[]; framing?: boolean }
export interface Story {
  based_on: { last_date: string };
  sentences: Sentence[];
  context_text: Record<string, { id: string; text: string; source: string }>;
}
export interface AnimalDetail {
  facts: Record<string, Fact>;
  summary: string;
  story: Story | null;
  program: string | null; citation: string | null; dataset_url: string | null;
  license: string | null; license_url: string | null;
  track: [number, number, number, number, number | null, number][]; // day, lon, lat, kind(0 fix/1 modeled), err_km, coarsened
}

const BASE = `${import.meta.env.BASE_URL}data/`;

export async function loadIndex(): Promise<Index> {
  const r = await fetch(`${BASE}index.json`);
  if (!r.ok) throw new Error(`index.json: ${r.status}`);
  return r.json();
}

const chunkCache = new Map<number, Promise<Chunk>>();

export function loadChunk(year: number): Promise<Chunk> {
  let p = chunkCache.get(year);
  if (!p) {
    p = (async () => {
      const [hdr, buf] = await Promise.all([
        fetch(`${BASE}chunks/${year}.json`).then((r) => r.json()),
        fetch(`${BASE}chunks/${year}.bin`).then((r) => r.arrayBuffer()),
      ]);
      const n = hdr.n as number;
      return {
        year,
        lon: new Float32Array(buf, 0, n),
        lat: new Float32Array(buf, 4 * n, n),
        day: new Float32Array(buf, 8 * n, n),
        animals: hdr.animals,
      };
    })();
    chunkCache.set(year, p);
  }
  return p;
}

const shardCache = new Map<number, Promise<Record<string, AnimalDetail>>>();

export function loadAnimal(row: AnimalRow): Promise<AnimalDetail> {
  let p = shardCache.get(row.shard);
  if (!p) {
    p = fetch(`${BASE}animals/${String(row.shard).padStart(3, '0')}.json`).then((r) => r.json());
    shardCache.set(row.shard, p);
  }
  return p.then((s) => s[row.id]);
}

export function assetUrl(path: string): string {
  return `${BASE}${path}`;
}

// --- time helpers (days since the index epoch) ---
export function makeClock(epochIso: string) {
  const epoch = Date.parse(`${epochIso}T00:00:00Z`);
  return {
    toDate: (day: number) => new Date(epoch + day * 86_400_000),
    toDay: (iso: string) => (Date.parse(`${iso}T00:00:00Z`) - epoch) / 86_400_000,
    fmt: (day: number) => new Date(epoch + day * 86_400_000).toISOString().slice(0, 10),
    year: (day: number) => new Date(epoch + day * 86_400_000).getUTCFullYear(),
  };
}
