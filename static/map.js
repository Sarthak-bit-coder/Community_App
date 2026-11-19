/* Shared map overlay: category markers, last-seen circles, detail popups, layer filters, ZIP zoom, "near me". */
(function () {
  // Simple 24x24 line icons (stroke = currentColor).
  const ICONS = {
    car: 'M5 17h14M5 17v2M19 17v2M6.5 12l1.6-4.5a1.5 1.5 0 0 1 1.4-1h5a1.5 1.5 0 0 1 1.4 1L17.5 12M4 12h16v5H4zM7.5 14.5h.01M16.5 14.5h.01',
    flame: 'M12 3c1.5 3.5 6 5 6 10a6 6 0 0 1-12 0c0-2.6 1.6-4 2.8-5.6.6 1.6 1.6 2.6 2.7 2.6C11.5 7.5 11 5 12 3z',
    shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
    eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    barrier: 'M4 20h16M7 20l3-15h4l3 15M8.6 13h6.8',
    alert: 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01',
    bolt: 'M13 2 4 14h8l-1 8 9-12h-8l1-8z',
    person: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
    sound: 'M11 5 6 9H2v6h4l5 4V5zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13',
    pin: 'M12 22s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
    search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-4.3-4.3',
    box: 'M21 8 12 3 3 8v8l9 5 9-5V8zM3 8l9 5 9-5M12 13v8',
    news: 'M4 4h13v16H6a2 2 0 0 1-2-2V4zM17 8h3v10a2 2 0 0 1-2 2M8 8h5M8 12h5M8 16h3',
    chat: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
    locate: 'M12 2v3M12 19v3M2 12h3M19 12h3M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  };
  function svg(name, size = 16, width = 2) {
    return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICONS[name] || ICONS.pin}"/></svg>`;
  }

  const CATS = {
    car_crash:      { label: 'Car crash',            icon: 'car',     color: '#dc2626', group: 'incidents' },
    fire:           { label: 'Fire',                 icon: 'flame',   color: '#ea580c', group: 'incidents' },
    crime:          { label: 'Theft / break-in',     icon: 'shield',  color: '#4f46e5', group: 'incidents' },
    suspicious:     { label: 'Suspicious activity',  icon: 'eye',     color: '#7c3aed', group: 'incidents' },
    road_closure:   { label: 'Road closure',         icon: 'barrier', color: '#b45309', group: 'incidents' },
    hazard:         { label: 'Hazard',               icon: 'alert',   color: '#ca8a04', group: 'incidents' },
    outage:         { label: 'Outage / streetlight', icon: 'bolt',    color: '#0e7490', group: 'incidents' },
    missing_person: { label: 'Missing person',       icon: 'person',  color: '#be123c', group: 'incidents' },
    noise:          { label: 'Noise',                icon: 'sound',   color: '#57534e', group: 'incidents' },
    other:          { label: 'Other',                icon: 'pin',     color: '#44403c', group: 'incidents' },
    lost_item:      { label: 'Lost item',            icon: 'search',  color: '#e11d48', group: 'lost' },
    found_item:     { label: 'Found item',           icon: 'box',     color: '#2563eb', group: 'found' },
  };
  const SOURCES = { news: 'Local news', whatsapp: 'WhatsApp group', community: 'Neighbor report' };

  function cat(key) { return CATS[key] || CATS.other; }

  /** Round colored badge with a white line icon (used in lists and legends). */
  function badge(key, size = 32) {
    const c = cat(key);
    return `<span class="nw-dot" style="--c:${c.color};width:${size}px;height:${size}px">${svg(c.icon, Math.round(size * 0.5))}</span>`;
  }

  function icon(f) {
    const c = cat(f.category);
    const src = f.source === 'news' ? `<span class="nw-src">${svg('news', 10, 2.4)}</span>`
              : f.source === 'whatsapp' ? `<span class="nw-src">${svg('chat', 10, 2.4)}</span>` : '';
    return L.divIcon({
      className: 'nw-marker-wrap',
      html: `<div class="nw-marker ${f.resolved ? 'is-resolved' : ''}" style="--c:${c.color}">${svg(c.icon, 15, 2.2)}${src}</div>`,
      iconSize: [30, 30], iconAnchor: [15, 15], popupAnchor: [0, -14],
    });
  }

  function ago(iso) {
    if (!iso) return '';
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    return Math.floor(s / 86400) + ' d ago';
  }

  function popup(f) {
    const c = cat(f.category);
    const isItem = f.kind === 'lost' || f.kind === 'found';
    const status = f.resolved
      ? '<span class="badge badge-ok">Resolved</span>'
      : isItem ? `<span class="badge ${f.kind === 'lost' ? 'badge-lost' : 'badge-found'}">${f.kind === 'lost' ? 'Lost' : 'Found'}</span>`
      : '<span class="badge badge-open">Active</span>';
    return `
      <div class="nw-pop">
        ${f.image ? `<img src="${esc(f.image)}" alt="">` : ''}
        <div class="nw-pop-kicker" style="color:${c.color}">${esc(isItem ? f.itemCategory || c.label : c.label)}</div>
        <div class="nw-pop-head"><strong>${esc(f.title)}</strong>${status}</div>
        <p>${esc(f.description)}</p>
        ${isItem && f.place ? `<p class="muted small">${f.kind === 'lost' ? 'Last seen' : 'Found'} near ${esc(f.place)}. The circle shows the search area.</p>` : ''}
        ${f.resolvedNote ? `<p class="small" style="color:var(--ok)">${esc(f.resolvedNote)}</p>` : ''}
        <div class="nw-pop-foot">
          <span class="muted small">ZIP ${esc(f.zip)} · ${ago(f.created)}${f.source && f.source !== 'community' ? ' · ' + SOURCES[f.source] : ''}${f.sourceName ? ' (' + esc(f.sourceName) + ')' : ''}</span>
          ${f.sourceUrl ? `<a href="${esc(f.sourceUrl)}" target="_blank" rel="noopener">Read the article</a>` : ''}
          ${f.url ? `<a href="${esc(f.url)}">${f.kind === 'lost' ? 'I found this' : 'Is this yours?'}</a>` : ''}
        </div>
      </div>`;
  }

  /** Render features on a map. Returns {byId, layers, fit()} */
  function render(map, features, opts = {}) {
    const layers = { incidents: L.layerGroup(), lost: L.layerGroup(), found: L.layerGroup(), news: L.layerGroup() };
    Object.values(layers).forEach(l => l.addTo(map));
    const byId = {};
    features.forEach(f => {
      if (f.lat == null || f.lng == null) return;
      const c = cat(f.category);
      const group = f.source === 'news' ? layers.news : layers[c.group];
      if (f.kind === 'lost' || f.kind === 'found') {
        // Circle around the last-seen / found spot, sized by how sure the poster was.
        L.circle([f.lat, f.lng], {
          radius: f.radius || 300, color: c.color, weight: 1.5, dashArray: f.kind === 'lost' ? '5 6' : null,
          fillColor: c.color, fillOpacity: 0.07, interactive: false,
        }).addTo(group);
      }
      const m = L.marker([f.lat, f.lng], { icon: icon(f), riseOnHover: true })
        .bindPopup(popup(f), { maxWidth: 300, minWidth: 240 });
      m.addTo(group);
      byId[f.id] = m;
    });
    function fit() {
      const pts = features.filter(f => f.lat != null).map(f => [f.lat, f.lng]);
      if (pts.length) map.fitBounds(pts, { padding: [50, 50], maxZoom: opts.maxZoom || 14 });
    }
    return { byId, layers, fit };
  }

  /** Floating panel with a toggle for each layer and a "near me" button. */
  function addOverlay(map, layers, counts) {
    const Overlay = L.Control.extend({
      options: { position: 'topright' },
      onAdd() {
        const div = L.DomUtil.create('div', 'nw-overlay');
        const row = (key, label, color) =>
          `<label class="nw-toggle"><input type="checkbox" data-layer="${key}" checked>
             <span class="nw-sw" style="background:${color}"></span><span class="nw-tl">${label}</span>
             <span class="nw-count">${counts[key] || 0}</span></label>`;
        div.innerHTML = `
          <div class="nw-overlay-title">Show</div>
          ${row('incidents', 'Incidents', '#dc2626')}
          ${row('news', 'Local news', '#44403c')}
          ${row('lost', 'Lost items', '#e11d48')}
          ${row('found', 'Found items', '#2563eb')}
          <button type="button" class="btn btn-ghost btn-sm nw-locate">${svg('locate', 14)} Near me</button>
          <details class="nw-legend"><summary>Legend</summary>
            ${Object.keys(CATS).map(k => `<div>${badge(k, 18)} ${CATS[k].label}</div>`).join('')}
          </details>`;
        L.DomEvent.disableClickPropagation(div);
        L.DomEvent.disableScrollPropagation(div);
        div.querySelectorAll('input[data-layer]').forEach(cb => cb.addEventListener('change', () => {
          const layer = layers[cb.dataset.layer];
          cb.checked ? layer.addTo(map) : map.removeLayer(layer);
        }));
        div.querySelector('.nw-locate').addEventListener('click', () => locateMe(map));
        return div;
      },
    });
    const ctl = new Overlay();
    map.addControl(ctl);
    return ctl;
  }

  let meMarker = null;
  function locateMe(map, onFound) {
    if (!navigator.geolocation) { toast('Location is not available in this browser.'); return; }
    toast('Finding your location…');
    navigator.geolocation.getCurrentPosition(pos => {
      const ll = [pos.coords.latitude, pos.coords.longitude];
      if (meMarker) map.removeLayer(meMarker);
      meMarker = L.circleMarker(ll, { radius: 8, color: '#fff', weight: 3, fillColor: '#111', fillOpacity: 1 })
        .addTo(map).bindTooltip('You are here');
      map.setView(ll, 15);
      if (onFound) onFound(pos.coords.latitude, pos.coords.longitude);
    }, err => {
      toast(err.code === 1 ? 'Location permission was denied. You can allow it in your browser settings.' : 'Could not get your location.');
    }, { enableHighAccuracy: true, timeout: 10000 });
  }

  /** Zoom a map to a 5-digit ZIP. Resolves to the ZIP info or null. */
  const zipCache = {};
  async function zoomToZip(map, zip) {
    if (!/^\d{5}$/.test(zip)) return null;
    try {
      const info = zipCache[zip] || (zipCache[zip] = await fetch('/api/zip/' + zip).then(r => r.ok ? r.json() : null));
      if (!info) { toast('We couldn’t find that ZIP code.'); delete zipCache[zip]; return null; }
      // The geocoder's ZIP boxes are very loose (city-sized), so zoom to neighborhood level around the center.
      map.flyTo([info.lat, info.lng], 14, { duration: 0.8 });
      return info;
    } catch (e) { return null; }
  }

  /** Wire an input so typing a full ZIP zooms the map (and calls onZip). Returns a trigger function. */
  function bindZipInput(input, map, onZip) {
    let last = '';
    const go = () => {
      const zip = input.value.trim();
      if (zip.length === 5 && zip !== last) {
        last = zip;
        zoomToZip(map, zip).then(info => info && onZip && onZip(zip, info));
      }
      if (zip.length < 5) last = '';
    };
    input.addEventListener('input', go);
    input.addEventListener('change', go);
    return go;
  }

  function counts(features) {
    const c = { incidents: 0, news: 0, lost: 0, found: 0 };
    features.forEach(f => { c[f.source === 'news' ? 'news' : cat(f.category).group]++; });
    return c;
  }

  window.NWMap = { CATS, ICONS, svg, badge, cat, icon, popup, render, addOverlay, locateMe, counts, ago,
                   zoomToZip, bindZipInput };
})();
