// The animal card: species photo, identity, story or summary, facts, sources.
import type { AnimalDetail, AnimalRow, Index, Story } from './data';
import { assetUrl } from './data';

export const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

const IUCN: Record<string, string> = {
  CR: 'Critically endangered', EN: 'Endangered', VU: 'Vulnerable', NT: 'Near threatened',
  LC: 'Least concern', DD: 'Data deficient',
};

// Facts shown in the list, in order, with labels.
const FACT_ROWS: [string, string][] = [
  ['release_site', 'Tagged at'],
  ['first_date', 'First position'],
  ['last_date', 'Last position'],
  ['days_tracked', 'Tracked for'],
  ['max_displacement_km', 'Farthest from start'],
  ['path_km', 'Distance travelled'],
  ['deepest_dive_m', 'Deepest dive'],
  ['longest_dive_min', 'Longest dive'],
  ['longest_gap_days', 'Longest silence'],
];

function cap(s: string) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

function renderStory(story: Story, facts: AnimalDetail['facts']): string {
  const sentences = story.sentences.map((s) => {
    const text = esc(s.text).replace(/\{([a-z_]+)\}/g, (_, id: string) =>
      facts[id] ? `<span class="fact" title="From the tracking record">${esc(facts[id].d)}</span>` : '');
    if (s.context?.length) {
      const src = s.context.map((c) => story.context_text[c]?.source).filter(Boolean)[0];
      return `<span class="ctx" title="${esc(src ? `General knowledge about the species. Source: ${src}` : 'General knowledge about the species')}">${text}</span>`;
    }
    return text;
  });
  // Paragraph breaks every 2–3 sentences read better on a narrow card.
  const paras: string[] = [];
  for (let i = 0; i < sentences.length; i += 3) paras.push(`<p>${sentences.slice(i, i + 3).join(' ')}</p>`);
  return `<div class="story">${paras.join('')}</div>
    <div class="story-note">Underlined values come from the tracking record; <i>italic</i> lines describe the species in general.</div>`;
}

export function renderCard(index: Index, row: AnimalRow, d: AnimalDetail): string {
  const sp = index.species[String(row.sp)];
  const photos = index.photos[String(row.sp)] ?? {};
  const ph = (row.sex && photos[row.sex]) || (row.stage === 'juvenile' && photos.juvenile) || photos.default;
  const src = index.sources[row.src];
  const who = [row.sex, row.stage && `${row.stage} when tagged`].filter(Boolean).join(', ');
  const coarse = d.track.some((p) => p[5]);
  const iucn = sp.iucn && IUCN[sp.iucn];

  const lowerName = sp.common.replace(/^\S+/, (w) => (/^(Pacific|Atlantic|African|American|Asian|European|Indian|Australian|Arctic|Antarctic|Galapagos|California|Weddell|Eurasian|Siberian|Egyptian|Cape|Canada|Swainson's|Bewick's|Blainville's)$/.test(w) ? w : w.toLowerCase()));
  const aName = `${/^[aeiou]/i.test(lowerName) ? 'an' : 'a'} ${lowerName}`;
  const photo = ph ? `
    <figure>
      <a href="${esc(ph.wiki)}" target="_blank" rel="noopener"><img src="${esc(assetUrl(ph.src))}" alt="${esc(aName)}" loading="lazy"></a>
      <figcaption>${esc(cap(aName))}, not this individual. Photo:
        <a href="${esc(ph.file)}" target="_blank" rel="noopener">${esc(ph.credit)}</a>,
        ${ph.license_url ? `<a href="${esc(ph.license_url)}" target="_blank" rel="noopener">${esc(ph.license)}</a>` : esc(ph.license)}</figcaption>
    </figure>` : '';

  const facts = FACT_ROWS.filter(([id]) => d.facts[id])
    .map(([id, label]) => `<dt>${label}</dt><dd>${esc(d.facts[id].d)}${id === 'path_km' && row.noisy ? ' ⚠︎' : ''}</dd>`).join('');
  const records = ['species_rank_days', 'species_rank_distance'].filter((id) => d.facts[id])
    .map((id) => `<div class="note">★ ${esc(cap(d.facts[id].d))}</div>`).join('');

  const unknowns = ['Birth date unknown.'];
  if (!row.sex) unknowns.push('Sex not recorded.');
  if (!row.stage) unknowns.push('Life stage not recorded.');

  return `
    <button class="icon close" data-close="card" aria-label="Close">✕</button>
    ${photo}
    <h2>${esc(sp.common)}${iucn ? `<span class="chip ${['CR', 'EN', 'VU'].includes(sp.iucn!) ? 'threat' : ''}" title="IUCN Red List status">${iucn}</span>` : ''}</h2>
    <div class="sub">${who ? esc(cap(who)) + ' · ' : ''}<i>${esc(sp.sci)}</i> · tag ${esc(row.id.split(':').slice(1).join(':'))}</div>
    ${d.story ? renderStory(d.story, d.facts) : `<p>${esc(d.summary)}</p>`}
    <dl class="facts">${facts}</dl>
    ${records}
    ${row.noisy ? '<div class="note warn">Positions from this tag are imprecise (estimated from daylight, or with large errors). The track jumps around and the distance travelled is overstated.</div>' : ''}
    ${coarse ? '<div class="note">This species is threatened: its most recent positions are delayed and rounded to protect it.</div>' : ''}
    <div class="note">${unknowns.join(' ')} One position per day is shown; dashed lines mark stretches with no positions.</div>
    <div class="actions"><button data-act="fit">Show whole track</button><button data-act="start">Replay from start</button><button data-act="share">Copy link</button></div>
    <div class="src">
      Data: <a href="${esc(d.dataset_url || src.url)}" target="_blank" rel="noopener">${esc(d.license ? (d.program || src.name) : src.name)}</a> ·
      ${(() => { const lic = d.license || src.license, url = d.license ? d.license_url : src.license_url;
        return url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(lic)}</a>` : esc(lic); })()}
      ${d.citation ? `<br>Cite: ${esc(d.citation)}` : ''}
      ${d.program ? `<br>Program: ${esc(d.program)}` : ''}
    </div>`;
}

export function renderAbout(index: Index): string {
  const sources = Object.values(index.sources)
    .map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>, ${esc(s.license)}</li>`).join('');
  return `
    <p>Every dot is a real wild animal that researchers tagged, from public tracking data:
      ${index.animals.length.toLocaleString()} animals across ${Object.keys(index.species).length} species,
      ${index.data_start.slice(0, 4)}–${index.data_end.slice(0, 4)}.</p>
    <ul>
      <li><b>Not live.</b> Public data lags weeks to years behind. Press play to replay what was recorded.</li>
      <li><b>Protecting the animals.</b> Positions from the last ${index.embargo_days.default} days are never shown, and
        ${index.embargo_days.sensitive} days for threatened species, whose more recent positions are also rounded.</li>
      <li><b>Positions are estimates.</b> Most tracks are modeled from satellite fixes; some tags estimate position
        from daylight and are much less precise. Gaps in a track are shown as gaps.</li>
      <li><b>Photos</b> show the species, not the individual, and are credited on each card.</li>
      <li><b>Nothing is invented.</b> Facts on each card come from the records. Where a card has a short story,
        numbers in it are filled from the data, and general species knowledge is marked.</li>
    </ul>
    <h3>Data sources</h3>
    <ul>${sources}</ul>
    <p class="note">Built ${esc(index.generated.slice(0, 10))}. Non-commercial. Basemap © OpenStreetMap contributors © CARTO.</p>`;
}
