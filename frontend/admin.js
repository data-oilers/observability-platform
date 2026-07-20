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
})();
