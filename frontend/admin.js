// frontend/admin.js — Panel Admin mirror (read-only). Isolated from app.js.
(function () {
  var current = 'supervision';

  function el(id) { return document.getElementById(id); }
  function env() { return window.currentEnv || 'qa'; }

  function adminFetch(path) {
    return fetch('/v1/' + env() + '/admin' + path).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }
  window.adminFetch = adminFetch;

  var RENDERERS = {};            // filled by Task 5+ (renderSupervision, etc.)
  window.registerAdminRenderer = function (key, fn) { RENDERERS[key] = fn; };

  function render() {
    var lbl = el('adminEnvLabel'); if (lbl) lbl.textContent = env().toUpperCase();
    var body = el('adminBody'); if (!body) return;
    var fn = RENDERERS[current];
    if (!fn) { body.innerHTML = '<div class="panel"><h3>' + current + '</h3><p>Próximamente.</p></div>'; return; }
    body.innerHTML = '<div class="panel" id="panelAdmin"><h3>Cargando…</h3></div>';
    fn(body, env());
  }

  window.showAdminSub = function (key) {
    current = key;
    document.querySelectorAll('[data-adminsub]').forEach(function (b) {
      b.classList.toggle('is-active', b.getAttribute('data-adminsub') === key);
    });
    render();
  };

  // Re-render when the env switch fires (app.js sets window.currentEnv then calls this).
  window.onEnvChange = (function (prev) {
    return function () { if (prev) prev(); render(); };
  })(window.onEnvChange);

  // Re-render when the admin tab becomes visible.
  window.onViewChange = (function (prev) {
    return function (view) { if (prev) prev(view); if (view === 'admin') render(); };
  })(window.onViewChange);

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  function renderSupervision(body, e) {
    adminFetch('/supervision/documents?page=1&page_size=50').then(function (data) {
      var items = (data && data.items) || [];
      var rows = items.map(function (d) {
        return '<tr data-docid="' + esc(d.document_id) + '">' +
          '<td>' + esc(d.document_name) + '</td>' +
          '<td>' + esc(d.area) + ' ' + esc(d.version || '') + '</td>' +
          '<td class="mono">' + esc(d.usage_count) + ' usos</td>' +
          '<td class="mono">' + esc(d.unique_users) + ' users</td>' +
          '<td class="mono"><span class="tag ok">+' + esc(d.positive_count) + '</span> ' +
          '<span class="tag crit">−' + esc(d.negative_count) + '</span></td>' +
          '</tr>';
      }).join('');
      body.innerHTML =
        '<div class="panel"><h3>Documentos <span class="badge">' + items.length + '</span></h3>' +
        '<div class="tablewrap"><table><thead><tr><th>Documento</th><th>Área</th>' +
        '<th>Usos</th><th>Users</th><th>Feedback</th></tr></thead><tbody>' +
        (rows || '<tr><td colspan="5">Sin documentos.</td></tr>') +
        '</tbody></table></div></div>' +
        '<div class="panel" id="panelAdminDetail"><h3>Chunks</h3>' +
        '<p class="mono" style="color:var(--text-dim)">Elegí un documento.</p></div>';
      body.querySelectorAll('tr[data-docid]').forEach(function (tr) {
        tr.style.cursor = 'pointer';
        tr.onclick = function () { renderChunks(tr.getAttribute('data-docid')); };
      });
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Documentos <span class="err-chip">error</span></h3></div>';
    });
  }

  function renderChunks(docId) {
    var detail = document.getElementById('panelAdminDetail'); if (!detail) return;
    detail.innerHTML = '<h3>Chunks</h3><p>Cargando…</p>';
    adminFetch('/supervision/documents/' + encodeURIComponent(docId) + '/chunks').then(function (data) {
      var chunks = (data && data.items) || (Array.isArray(data) ? data : []);
      detail.innerHTML = '<h3>Chunks <span class="badge">' + chunks.length + '</span></h3>' +
        '<div class="tablewrap"><table><tbody>' +
        (chunks.map(function (c) {
          return '<tr><td class="mono">' + esc(c.chunk_id || c.id || '') + '</td><td>' +
            esc((c.text || c.content || '').slice(0, 160)) + '…</td></tr>';
        }).join('') || '<tr><td>Sin chunks.</td></tr>') +
        '</tbody></table></div>';
    }).catch(function () { detail.innerHTML = '<h3>Chunks <span class="err-chip">error</span></h3>'; });
  }

  registerAdminRenderer('supervision', renderSupervision);

  function renderReporteria(body, e) {
    var to = new Date().toISOString().slice(0, 10);
    var from = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
    adminFetch('/reporteria?date_from=' + from + '&date_to=' + to).then(function (data) {
      body.innerHTML = '<div class="panel"><h3>Reportería ejecutiva</h3>' +
        '<pre class="mono" style="white-space:pre-wrap;overflow:auto">' +
        esc(JSON.stringify(data, null, 2)) + '</pre></div>';
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Reportería <span class="err-chip">error</span></h3></div>';
    });
  }
  registerAdminRenderer('reporteria', renderReporteria);

  function renderModelos(body, e) {
    adminFetch('/modelos').then(function (data) {
      var rows = (Array.isArray(data) ? data : (data && data.items) || []);
      body.innerHTML = '<div class="panel"><h3>Model routing <span class="badge">' + rows.length + '</span></h3>' +
        '<div class="tablewrap"><table><thead><tr><th>Nodo</th><th>Modelo</th>' +
        '<th>Temp</th><th>Max tokens</th></tr></thead><tbody>' +
        (rows.map(function (r) {
          return '<tr><td class="mono">' + esc(r.node || r.node_name) + '</td><td>' +
            esc(r.model) + '</td><td class="mono">' + esc(r.temperature) +
            '</td><td class="mono">' + esc(r.max_tokens) + '</td></tr>';
        }).join('') || '<tr><td colspan="4">Sin filas.</td></tr>') +
        '</tbody></table></div></div>';
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Modelos <span class="err-chip">error</span></h3></div>';
    });
  }
  registerAdminRenderer('modelos', renderModelos);

  function renderPrompts(body, e) {
    adminFetch('/prompts').then(function (data) {
      var rows = (Array.isArray(data) ? data : (data && data.items) || []);
      body.innerHTML = '<div class="panel"><h3>Prompts <span class="badge">' + rows.length + '</span></h3>' +
        rows.map(function (p) {
          return '<details><summary class="mono">' + esc(p.name || p.tier || p.id) + '</summary>' +
            '<pre class="mono" style="white-space:pre-wrap;overflow:auto">' +
            esc(p.content || p.template || JSON.stringify(p, null, 2)) + '</pre></details>';
        }).join('') + '</div>';
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Prompts <span class="err-chip">error</span></h3></div>';
    });
  }
  registerAdminRenderer('prompts', renderPrompts);
})();
