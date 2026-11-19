/* Shared map overlay: category markers, last-seen circles, detail popups, layer filters, "near me". */
(function () {
  const CATS = {
    car_crash:      { label: 'Car crash',          icon: '🚗', color: '#dc2626', group: 'incidents' },
    fire:           { label: 'Fire',               icon: '🔥', color: '#ea580c', group: 'incidents' },
    crime:          { label: 'Theft / break-in',   icon: '🚨', color: '#7c3aed', group: 'incidents' },
    suspicious:     { label: 'Suspicious activity',icon: '👀', color: '#9333ea', group: 'incidents' },
    road_closure:   { label: 'Road closure',       icon: '🚧', color: '#d97706', group: 'incidents' },
    hazard:         { label: 'Hazard',             icon: '⚠️', color: '#ca8a04', group: 'incidents' },
    outage:         { label: 'Outage / streetlight',icon: '💡', color: '#0891b2', group: 'incidents' },
    missing_person: { label: 'Missing person',     icon: '🧍', color: '#be123c', group: 'incidents' },
    noise:          { label: 'Noise',              icon: '🔊', color: '#64748b', group: 'incidents' },
    other:          { label: 'Other',              icon: '📍', color: '#475569', group: 'incidents' },
    lost_item:      { label: 'Lost item',          icon: '🔍', color: '#e11d48', group: 'lost' },
    found_item:     { label: 'Found item',         icon: '📦', color: '#2563eb', group: 'found' },
  };
  const SOURCES = { news: '📰 Local news', whatsapp: '💬 WhatsApp group', community: '👥 Neighbor report' };

  function cat(key) { return CATS[key] || CATS.other; }

  function icon(f) {
    const c = cat(f.category);
    const badge = f.source === 'news' ? '<span class="nw-badge">📰</span>'
                : f.source === 'whatsapp' ? '<span class="nw-badge">💬</span>' : '';
    return L.divIcon({
      className: 'nw-marker-wrap',
      html: `<div class="nw-marker ${f.resolved ? 'is-resolved' : ''}" style="--c:${c.color}"><span>${c.icon}</span>${badge}</div>`,
      iconSize: [34, 34], iconAnchor: [17, 34], popupAnchor: [0, -30],
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
    const facts = [
      `<span>${c.icon} ${esc(isItem ? f.itemCategory : c.label)}</span>`,
      `<span>ZIP ${esc(f.zip)}</span>`,
      f.created ? `<span>${ago(f.created)}</span>` : '',
    ].join('');
    return `
      <div class="nw-pop">
        ${f.image ? `<img src="${esc(f.image)}" alt="">` : ''}
        <div class="nw-pop-head"><strong>${esc(f.title)}</strong>${status}</div>
        <div class="nw-pop-facts">${facts}</div>
        <p>${esc(f.description)}</p>
        ${isItem && f.place ? `<p class="muted small">${f.kind === 'lost' ? 'Last seen' : 'Found'} near ${esc(f.place)} · circle shows the search area</p>` : ''}
        ${f.resolvedNote ? `<p class="small" style="color:var(--ok)">✓ ${esc(f.resolvedNote)}</p>` : ''}
        <div class="nw-pop-foot">
          <span class="muted small">${SOURCES[f.source] || ''}${f.sourceName ? ' · ' + esc(f.sourceName) : ''}</span>
          ${f.sourceUrl ? `<a href="${esc(f.sourceUrl)}" target="_blank" rel="noopener">Read article ↗</a>` : ''}
          ${f.url ? `<a href="${esc(f.url)}">${f.kind === 'lost' ? 'I found this →' : 'Is this yours? →'}</a>` : ''}
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
      const m = L.marker([f.lat, f.lng], { icon: icon(f), riseOnHover: true })
        .bindPopup(popup(f), { maxWidth: 300, minWidth: 240 });
      if (f.kind === 'lost' || f.kind === 'found') {
        // Circle around the last-seen / found spot, sized by how sure the poster was.
        L.circle([f.lat, f.lng], {
          radius: f.radius || 300, color: c.color, weight: 1.5, dashArray: f.kind === 'lost' ? '6 6' : null,
          fillColor: c.color, fillOpacity: 0.08, interactive: false,
        }).addTo(group);
      }
      m.addTo(group);
      byId[f.id] = m;
    });
    function fit() {
      const pts = features.filter(f => f.lat != null).map(f => [f.lat, f.lng]);
      if (pts.length) map.fitBounds(pts, { padding: [50, 50], maxZoom: opts.maxZoom || 14 });
    }
    return { byId, layers, fit };
  }

  /** Floating legend with toggles for each layer, plus a "near me" button. */
  function addOverlay(map, layers, counts) {
    const Overlay = L.Control.extend({
      options: { position: 'topright' },
      onAdd() {
        const div = L.DomUtil.create('div', 'nw-overlay');
        const row = (key, label, sw) =>
          `<label class="nw-toggle"><input type="checkbox" data-layer="${key}" checked>
             <span class="nw-sw">${sw}</span><span class="nw-tl">${label}</span>
             <span class="nw-count">${counts[key] || 0}</span></label>`;
        div.innerHTML = `
          <div class="nw-overlay-title">On the map</div>
          ${row('incidents', 'Incidents', '🚨')}
          ${row('news', 'Local news', '📰')}
          ${row('lost', 'Lost items', '🔍')}
          ${row('found', 'Found items', '📦')}
          <button type="button" class="btn btn-ghost btn-sm nw-locate">📍 Near me</button>
          <details class="nw-legend"><summary>Legend</summary>
            ${Object.values(CATS).map(c => `<div><span>${c.icon}</span> ${c.label}</div>`).join('')}
            <div><span>📰</span> From local news · <span>💬</span> From WhatsApp</div>
            <div><span style="display:inline-block;width:14px;height:14px;border:1.5px dashed #e11d48;border-radius:50%;vertical-align:middle"></span> Last-seen area</div>
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
      meMarker = L.circleMarker(ll, { radius: 8, color: '#fff', weight: 3, fillColor: '#2f5bea', fillOpacity: 1 })
        .addTo(map).bindTooltip('You are here');
      map.setView(ll, 15);
      if (onFound) onFound(pos.coords.latitude, pos.coords.longitude);
    }, err => {
      toast(err.code === 1 ? 'Location permission was denied. You can allow it in your browser settings.' : 'Could not get your location.');
    }, { enableHighAccuracy: true, timeout: 10000 });
  }

  function counts(features) {
    const c = { incidents: 0, news: 0, lost: 0, found: 0 };
    features.forEach(f => { c[f.source === 'news' ? 'news' : cat(f.category).group]++; });
    return c;
  }

  window.NWMap = { CATS, cat, icon, popup, render, addOverlay, locateMe, counts, ago };
})();
