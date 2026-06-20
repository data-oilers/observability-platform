/**
 * rag-overlay.js — live stats overlay for rag-pipeline.html
 * Vanilla JS, no external deps, CSP-safe (same-origin only).
 */

'use strict';

// ---------------------------------------------------------------------------
// XSS helpers
// ---------------------------------------------------------------------------
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ---------------------------------------------------------------------------
// Env from URL — validate; default qa
// ---------------------------------------------------------------------------
var VALID_ENVS = { qa: true, prod: true };
var rawEnv = (new URLSearchParams(window.location.search).get('env') || 'qa').toLowerCase();
var ENV = VALID_ENVS[rawEnv] ? rawEnv : 'qa';

// ---------------------------------------------------------------------------
// Inject overlay CSS
// ---------------------------------------------------------------------------
(function injectStyles() {
  var style = document.createElement('style');
  style.textContent = [
    '/* rag-overlay — injected by rag-overlay.js */',
    '.rag-ov { pointer-events:none; }',

    /* health outlines on node shape */
    '.rag-node-ok > .shape { stroke:#46C28A !important; stroke-width:2 !important;',
    '  filter:drop-shadow(0 0 5px rgba(70,194,138,.40)) !important; }',
    '.rag-node-crit > .shape { stroke:#E5534B !important; stroke-width:2.5 !important;',
    '  filter:drop-shadow(0 0 7px rgba(229,83,75,.55)) !important; }',

    /* stats chip */
    '.rag-chip-bg { rx:3; fill:#0E1420; fill-opacity:.88; stroke:#26303F; stroke-width:1; }',
    '.rag-chip-txt { font:500 9px ui-monospace,"Cascadia Mono","SF Mono",Menlo,monospace;',
    '  fill:#97A3B4; }',
    '.rag-chip-txt.rag-crit-txt { fill:#E5534B; }',

    /* status bar */
    '#rag-status-bar {',
    '  position:fixed; bottom:0; left:0; right:0; z-index:200;',
    '  background:rgba(11,14,20,.88); backdrop-filter:blur(6px);',
    '  border-top:1px solid #26303F;',
    '  padding:5px 16px;',
    '  display:flex; align-items:center; gap:14px;',
    '  font:500 11px ui-monospace,"Cascadia Mono","SF Mono",Menlo,monospace;',
    '  color:#97A3B4; letter-spacing:.02em;',
    '}',
    '#rag-status-bar .rag-sb-dot {',
    '  width:7px; height:7px; border-radius:50%;',
    '  background:#46C28A; flex:none;',
    '  animation:rag-pulse 2.4s ease-in-out infinite;',
    '}',
    '#rag-status-bar .rag-sb-dot.err { background:#E5534B; animation:none; }',
    '@keyframes rag-pulse {',
    '  0%,100%{box-shadow:0 0 0 2px rgba(70,194,138,.3);}',
    '  50%{box-shadow:0 0 0 4px transparent;}',
    '}',
    '#rag-status-bar .rag-sb-label { color:#E8EDF4; font-weight:600; }',
    '#rag-status-bar .rag-sb-env { color:#3FBFAE; }',
    '#rag-status-bar .rag-sb-err { color:#E5534B; }',
    '#rag-status-bar .rag-sb-empty { color:#E8A33D; }',
    '#minimap, #rulesPanel { bottom: 40px !important; }',
  ].join('\n');
  document.head.appendChild(style);
})();

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------
var statusBar = document.createElement('div');
statusBar.id = 'rag-status-bar';
statusBar.innerHTML =
  '<span class="rag-sb-dot" id="rag-sb-dot"></span>' +
  '<span class="rag-sb-label">overlay en vivo</span>' +
  '<span> · env=<span class="rag-sb-env">' + esc(ENV.toUpperCase()) + '</span></span>' +
  '<span id="rag-sb-msg">· iniciando…</span>';
document.body.appendChild(statusBar);

var sbMsg = document.getElementById('rag-sb-msg');
var sbDot = document.getElementById('rag-sb-dot');

function setStatus(msg, state) {
  // state: 'ok' | 'err' | 'empty' | 'init'
  if (!sbMsg) return;
  sbDot.className = 'rag-sb-dot' + (state === 'err' ? ' err' : '');
  var cls = state === 'err' ? 'rag-sb-err' :
            state === 'empty' ? 'rag-sb-empty' : '';
  sbMsg.className = cls ? 'rag-sb-' + state : '';
  sbMsg.textContent = msg;
}

// ---------------------------------------------------------------------------
// Fetch with timeout
// ---------------------------------------------------------------------------
function fetchStats() {
  var ctrl = new AbortController();
  var timer = setTimeout(function() { ctrl.abort(); }, 10000);
  return fetch('/v1/' + ENV + '/rag-nodes?since_minutes=60', { signal: ctrl.signal })
    .then(function(r) {
      clearTimeout(timer);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .catch(function(e) {
      clearTimeout(timer);
      throw e;
    });
}

// ---------------------------------------------------------------------------
// Overlay helpers
// ---------------------------------------------------------------------------
var OV_CLASS = 'rag-ov';

function clearOverlay() {
  // Remove all previously-injected overlay elements
  var els = document.querySelectorAll('.' + OV_CLASS);
  for (var i = 0; i < els.length; i++) {
    els[i].parentNode && els[i].parentNode.removeChild(els[i]);
  }
  // Remove health classes from nodes
  var nodes = document.querySelectorAll('.rag-node-ok, .rag-node-crit');
  for (var j = 0; j < nodes.length; j++) {
    nodes[j].classList.remove('rag-node-ok', 'rag-node-crit');
  }
}

function fmtMs(v) {
  if (v === null || v === undefined || isNaN(Number(v))) return 'n/d';
  return String(Math.round(Number(v)));
}

function applyOverlay(stats) {
  clearOverlay();

  var matched = 0;
  var svgCanvas = document.getElementById('canvas');
  if (!svgCanvas) return matched;

  var svgNS = 'http://www.w3.org/2000/svg';

  for (var i = 0; i < stats.length; i++) {
    var stat = stats[i];
    var nodeId = stat.node;

    // Use CSS.escape to build safe selector; esc() for displayed text
    var nodeEl;
    try {
      nodeEl = svgCanvas.querySelector('[data-id="' + CSS.escape(nodeId) + '"]');
    } catch (e) {
      continue;
    }
    if (!nodeEl) continue;

    matched++;

    // Health class
    var isCrit = stat.errors > 0;
    nodeEl.classList.add(isCrit ? 'rag-node-crit' : 'rag-node-ok');

    // Build stats chip — a <g class="rag-ov"> appended to the node <g>
    // Position: anchored at bottom-center of the node's bounding box
    var bbox;
    try { bbox = nodeEl.getBBox(); } catch(e) { continue; }

    var chipW = 96, chipH = 18;
    var chipX = bbox.x + (bbox.width - chipW) / 2;
    var chipY = bbox.y + bbox.height + 3;

    var p50  = fmtMs(stat.p50_ms);
    var p95  = fmtMs(stat.p95_ms);
    var calls = String(stat.calls || 0);
    var label = p50 + '/' + p95 + 'ms · ' + calls + ' calls';

    var chipG = document.createElementNS(svgNS, 'g');
    chipG.setAttribute('class', OV_CLASS);
    chipG.setAttribute('aria-hidden', 'true');

    var rect = document.createElementNS(svgNS, 'rect');
    rect.setAttribute('class', 'rag-chip-bg');
    rect.setAttribute('x', String(chipX));
    rect.setAttribute('y', String(chipY));
    rect.setAttribute('width', String(chipW));
    rect.setAttribute('height', String(chipH));

    var txt = document.createElementNS(svgNS, 'text');
    txt.setAttribute('class', 'rag-chip-txt' + (isCrit ? ' rag-crit-txt' : ''));
    txt.setAttribute('x', String(chipX + chipW / 2));
    txt.setAttribute('y', String(chipY + 12));
    txt.setAttribute('text-anchor', 'middle');
    txt.textContent = label;  // plain textContent — no innerHTML

    chipG.appendChild(rect);
    chipG.appendChild(txt);
    nodeEl.appendChild(chipG);
  }

  return matched;
}

// ---------------------------------------------------------------------------
// Poll loop
// ---------------------------------------------------------------------------
function poll() {
  fetchStats()
    .then(function(stats) {
      if (!Array.isArray(stats)) throw new Error('respuesta inesperada');

      var total = stats.length;
      var matched = total > 0 ? applyOverlay(stats) : 0;

      var now = new Date();
      var hms = [now.getHours(), now.getMinutes(), now.getSeconds()]
        .map(function(n) { return (n < 10 ? '0' : '') + n; }).join(':');

      if (total === 0) {
        // Honest empty state — no activity, but NOT an error
        clearOverlay();
        setStatus('· sin actividad reciente · actualizado ' + hms, 'empty');
      } else {
        setStatus(
          '· actualizado ' + hms + ' · ' + matched + '/' + total + ' nodos',
          'ok'
        );
      }
    })
    .catch(function() {
      // Do NOT clear/false-green the diagram — leave last state
      setStatus('· no se pudo leer rag-nodes — reintentando', 'err');
    });
}

// Initial call + interval
poll();
setInterval(poll, 15000);
