/**
 * obs·macro · app.js — Fase 1.B
 *
 * Wires real API data into the dashboard.
 * No external dependencies. No build step. Vanilla JS (uses Promise.allSettled, fetch, AbortController).
 *
 * HONEST DATA RULE: every displayed value comes from the API.
 * Elements with no API source show "n/d" — never a fabricated number.
 */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
var currentEnv = (document.getElementById('wrap') || {dataset: {}}).dataset.env || 'qa';

// In-flight guard — prevents overlapping refreshAll calls from piling up
var refreshing = false;

// Env-generation token — bumped on every env switch; in-flight fetches compare
// against this to detect stale results and discard them instead of rendering.
var envGen = 0;

// Batch sequencing — each refreshAll call gets a unique id; only the batch
// that was last started (activeBatch) is allowed to clear the refreshing flag.
var batchSeq = 0;
var activeBatch = 0;

// Rolling buffers for charts — capped at 30 points each
var chartBuffers = {
  p95:    [],
  p50:    [],
  tokens: [],
  cpu:    []
};

// Last-known data for banner worst-status computation
var lastHealthData    = null;   // from /health
var lastWorkloadsData = null;   // from /workloads (null = fetch not yet completed)
var CHART_CAP = 30;

// Merge state for panelFallas — replaces ad-hoc DOM expando properties
var fallasState = { workloadsHtml: undefined, eventsHtml: undefined, workloadsHasErrors: false, workloadsErr: false, eventsErr: false };

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function fmtMs(ms) {
  if (ms === null || ms === undefined) return 'n/d';
  if (ms >= 1000) return (ms / 1000).toFixed(1) + 's';
  return Math.round(ms) + 'ms';
}

function fmtBytes(b) {
  if (b === null || b === undefined) return 'n/d';
  if (b >= 1073741824) return (b / 1073741824).toFixed(1) + ' GiB';
  if (b >= 1048576)    return (b / 1048576).toFixed(1) + ' MiB';
  if (b >= 1024)       return (b / 1024).toFixed(1) + ' KiB';
  return b + ' B';
}

function fmtCores(c) {
  if (c === null || c === undefined) return 'n/d';
  return c.toFixed(2);
}

function fmtPct(p) {
  if (p === null || p === undefined) return 'n/d';
  return Math.round(p) + '%';
}

function nd(v, formatter) {
  if (v === null || v === undefined) return 'n/d';
  return formatter ? formatter(v) : String(v);
}

function severityClass(sev) {
  if (!sev) return 'info';
  var s = sev.toUpperCase();
  if (s === 'ERROR' || s === 'CRITICAL') return 'err';
  if (s === 'WARNING') return 'warn';
  return 'info';
}

function severityLabel(sev) {
  if (!sev) return 'INFO';
  var s = sev.toUpperCase();
  if (s === 'WARNING') return 'WARN';
  return s.charAt(0) + s.slice(1).toLowerCase();
}

function fmtTime(ts) {
  if (!ts) return '—';
  try {
    var d = new Date(ts);
    if (isNaN(d.getTime())) return ts.slice(0, 8) || ts;
    return [d.getHours(), d.getMinutes(), d.getSeconds()]
      .map(function(n) { return (n < 10 ? '0' : '') + n; }).join(':');
  } catch (e) { return ts; }
}

function esc(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function el(id) { return document.getElementById(id); }

function setText(id, val) {
  var e = el(id); if (e) e.textContent = val;
}

// ---------------------------------------------------------------------------
// Worst-status computation — combines /health with /workloads signals
// ---------------------------------------------------------------------------

var CRIT_PROBLEMS = [
  'CrashLoopBackOff', 'ImagePullBackOff', 'ErrImagePull',
  'CreateContainerConfigError', 'CreateContainerError', 'InvalidImageName'
];

// Returns { status: 'ok'|'warn'|'crit'|'partial', title, sub }
// healthData  — result from /health (may be null if fetch failed)
// wlData      — result from /workloads (may be null if fetch failed or pending)
// wlFailed    — true if /workloads fetch hard-failed (not stale-env)
function worstStatus(healthData, wlData, wlFailed) {
  var level = 'ok';   // ok < warn < crit
  var reasons = [];

  function bump(l) { if (l === 'crit') level = 'crit'; else if (l === 'warn' && level !== 'crit') level = 'warn'; }

  // --- /health contribution ---
  if (healthData) {
    if (healthData.status === 'crit') bump('crit');
    else if (healthData.status === 'warn') bump('warn');
    if (healthData.reasons && healthData.reasons.length) {
      reasons = reasons.concat(healthData.reasons);
    }
  }

  // --- /workloads contribution ---
  // If fetch hard-failed → partial/blind state; if wlData===null AND wlFailed===false → still loading, skip
  if (wlFailed) {
    // Can't read workloads → show as partial (amber)
    bump('warn');
    reasons.push('workloads: error de lectura');
    return { status: 'partial', level: level, reasons: reasons };
  }

  if (wlData) {
    // pod_issues
    (wlData.pod_issues || []).forEach(function(p) {
      if (CRIT_PROBLEMS.indexOf(p.problem) !== -1) {
        bump('crit');
        reasons.push(p.problem + ' en ' + p.pod);
      } else {
        bump('warn');
        reasons.push(p.problem + ' en ' + p.pod);
      }
    });

    // replica_shortfalls
    if (wlData.replica_shortfalls && wlData.replica_shortfalls.length) {
      bump('crit');
      reasons.push(wlData.replica_shortfalls.length + ' shortfall(s) de réplicas');
    }

    // pvc_issues
    (wlData.pvc_issues || []).forEach(function(pvc) {
      if (pvc.phase === 'Lost') { bump('crit'); reasons.push('PVC Lost: ' + pvc.name); }
      else if (pvc.phase === 'Pending') { bump('warn'); reasons.push('PVC Pending: ' + pvc.name); }
    });

    // partial/blind: errors array non-empty
    if (wlData.errors && wlData.errors.length) {
      bump('warn');
      return { status: 'partial', level: level, reasons: reasons, partialErrors: wlData.errors };
    }
  }

  return { status: level, level: level, reasons: reasons };
}

// Apply computed status to all banners and the topbar pill
function applyBannerStatus(computed) {
  var statusMap = { ok: 'Operativo', warn: 'Degradado', crit: 'Critico', partial: 'Estado parcial' };
  var status = computed.status;   // 'ok'|'warn'|'crit'|'partial'
  // I-2: partial keeps title "Estado parcial" but color follows the computed level
  // (crit stays red; only non-crit partial is amber)
  var displayLevel = (status === 'partial')
    ? (computed.level === 'crit' ? 'crit' : 'warn')
    : (computed.level || status);

  var title = statusMap[status] || status;
  var sub = computed.reasons && computed.reasons.length
    ? computed.reasons.slice(0, 3).join(' · ')
    : (status === 'ok' ? 'sin novedades' : '—');
  if (status === 'partial' && computed.partialErrors) {
    sub = 'no se pudo leer: ' + computed.partialErrors.join(', ');
  }

  // Topbar pill
  setText('st-title', title);
  setText('st-sub', sub);
  var dot = document.querySelector('.status-pill .dot');
  if (dot) {
    dot.style.background = displayLevel === 'ok' ? 'var(--ok)'
      : displayLevel === 'warn' ? 'var(--warn)' : 'var(--crit)';
    dot.style.boxShadow = displayLevel === 'ok' ? '0 0 0 3px var(--ok-soft)'
      : displayLevel === 'warn' ? '0 0 0 3px var(--warn-soft)' : '0 0 0 3px var(--crit-soft)';
  }

  // App + Infra view banners
  ['bannerApp', 'bannerInfra'].forEach(function(bid) {
    var banner = el(bid); if (!banner) return;
    banner.className = 'banner' + (displayLevel === 'warn' || status === 'partial' ? ' warn' : displayLevel === 'crit' ? ' crit' : '');
    var suffixes = bid === 'bannerApp' ? ['-app'] : ['-infra'];
    suffixes.forEach(function(sfx) {
      setText('st-title' + sfx, title);
      setText('st-sub' + sfx, sub);
    });
    var bDot = banner.querySelector('.d');
    if (bDot) {
      bDot.style.background = displayLevel === 'ok' ? 'var(--ok)'
        : displayLevel === 'warn' ? 'var(--warn)' : 'var(--crit)';
    }
  });
}

function pushBuffer(key, value) {
  if (value === null || value === undefined) return;
  chartBuffers[key].push(value);
  if (chartBuffers[key].length > CHART_CAP) {
    chartBuffers[key] = chartBuffers[key].slice(-CHART_CAP);
  }
}

// ---------------------------------------------------------------------------
// Fetch helper
// ---------------------------------------------------------------------------

function apiFetch(path) {
  var gen = envGen;   // capture env generation at call time
  var ctrl = new AbortController();
  var timer = setTimeout(function() { ctrl.abort(); }, 10000);
  return fetch('/v1/' + currentEnv + path, { signal: ctrl.signal })
    .then(function(r) {
      clearTimeout(timer);
      if (gen !== envGen) throw new Error('stale-env');   // env changed mid-flight → discard
      if (!r.ok) throw new Error('HTTP ' + r.status + ' on ' + path);
      return r.json();
    })
    .catch(function(e) {
      clearTimeout(timer);
      throw e;
    });
}

// ---------------------------------------------------------------------------
// Panel error chip helpers
// ---------------------------------------------------------------------------

function markPanelOk(panelId) {
  var panel = el(panelId); if (!panel) return;
  panel.classList.remove('panel-loading');
  var chip = panel.querySelector('.err-chip');
  if (chip) chip.remove();
}

function markPanelError(panelId, msg) {
  var panel = el(panelId); if (!panel) return;
  panel.classList.remove('panel-loading');
  if (!panel.querySelector('.err-chip')) {
    var h3 = panel.querySelector('h3');
    if (h3) {
      var chip = document.createElement('span');
      chip.className = 'err-chip';
      chip.textContent = msg || 'error';
      h3.appendChild(chip);
    }
  }
}

function setPanelLoading(panelId) {
  var panel = el(panelId); if (!panel) return;
  panel.classList.add('panel-loading');
  var chip = panel.querySelector('.err-chip');
  if (chip) chip.remove();
}

// ---------------------------------------------------------------------------
// Render: Health banner
// ---------------------------------------------------------------------------

function renderHealth(data) {
  // Store for combined banner computation
  lastHealthData = data;

  // Apply combined banner (workloads may not be in yet — uses last known)
  applyBannerStatus(worstStatus(lastHealthData, lastWorkloadsData, false));

  // KPIs from health
  setText('kpi-errors', nd(data.errors));
  setText('kpi-429', nd(data.gemini_429));
  setText('kpi-infra-errors', nd(data.errors));

  var errTag = el('kpi-errors-tag');
  if (errTag) {
    errTag.className = 'tag ' + (data.errors > 0 ? 'crit' : 'ok');
    errTag.textContent = '5 min';
  }
  var tag429 = el('kpi-429-tag');
  if (tag429) {
    tag429.className = 'tag ' + (data.gemini_429 > 0 ? 'warn' : 'ok');
    tag429.textContent = '5 min';
  }
}

// ---------------------------------------------------------------------------
// Render: Latency KPI + charts
// ---------------------------------------------------------------------------

function renderLatency(data) {
  var agente = null;
  if (data.stats) {
    data.stats.forEach(function(s) {
      if (s.label === 'chat_agente') agente = s;
    });
  }

  // p95 KPI
  var p95El = el('kpi-p95');
  if (p95El) {
    if (agente && agente.p95_ms !== null && agente.p95_ms !== undefined) {
      p95El.textContent = fmtMs(agente.p95_ms);
      p95El.classList.remove('nd');
    } else {
      p95El.textContent = 'n/d';
      p95El.classList.add('nd');
    }
  }

  // Push chart values
  pushBuffer('p95', agente ? agente.p95_ms : null);
  pushBuffer('p50', agente ? agente.p50_ms : null);

  // Chart 0: p95 slide big label
  var p95val = (agente && agente.p95_ms !== null && agente.p95_ms !== undefined)
    ? fmtMs(agente.p95_ms) : 'n/d';
  setText('chart-p95-big', p95val);

  // Chart 1: p50 slide big label
  var p50val = (agente && agente.p50_ms !== null && agente.p50_ms !== undefined)
    ? fmtMs(agente.p50_ms) : 'n/d';
  setText('chart-p50-big', p50val);
}

// ---------------------------------------------------------------------------
// Render: Logs table
// ---------------------------------------------------------------------------

function renderLogs(logs) {
  var tbody = el('logsTbody'); if (!tbody) return;
  if (!logs || !logs.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-faint);text-align:center;padding:20px">Sin eventos</td></tr>';
    return;
  }
  var rows = logs.slice(0, 100).map(function(ev) {
    var sc = severityClass(ev.severity);
    var sl = severityLabel(ev.severity);
    var status = (ev.status !== null && ev.status !== undefined)
      ? '<span style="color:' + (ev.status >= 500 ? 'var(--crit)' : ev.status >= 400 ? 'var(--warn)' : 'var(--ok)') + '">' + esc(String(ev.status)) + '</span>'
      : '<span style="color:var(--text-faint)">—</span>';
    var dur = (ev.duration_ms !== null && ev.duration_ms !== undefined)
      ? '<span class="mono">' + Math.round(ev.duration_ms) + '</span>'
      : '<span style="color:var(--text-faint)">—</span>';
    var msg = ev.path
      ? '<b>' + esc(ev.path) + '</b>' + (ev.message ? ' · ' + esc(ev.message) : '')
      : esc(ev.message || '—');
    return '<tr>'
      + '<td class="mono">' + esc(fmtTime(ev.ts)) + '</td>'
      + '<td><span class="sev ' + sc + '"><span class="s"></span>' + sl + '</span></td>'
      + '<td class="mono">' + esc(ev.pod || '—') + '</td>'
      + '<td class="msgcell">' + msg + '</td>'
      + '<td class="mono">' + status + '</td>'
      + '<td class="mono">' + dur + '</td>'
      + '<td></td>'
      + '</tr>';
  });
  tbody.innerHTML = rows.join('');
}

// ---------------------------------------------------------------------------
// Render: Traces panel
// ---------------------------------------------------------------------------

function renderTraces(traces) {
  var list = el('traceList');
  var hint = el('traces-hint');
  if (!list) return;

  if (!traces || !traces.length) {
    list.innerHTML = '<div class="node"><span class="nm"></span><span class="nn mono" style="color:var(--text-faint)">Sin trazas</span><span class="nbar"><i style="width:0%"></i></span><span class="nt mono">—</span></div>';
    if (hint) hint.textContent = '0 trazas';
    setText('tf-faith', 'n/d');
    setText('tf-tokens', 'n/d');
    return;
  }

  // Scale bars to the max latency in the set
  var maxLat = 1;
  traces.forEach(function(t) {
    if (t.latency_ms !== null && t.latency_ms !== undefined && t.latency_ms > maxLat) {
      maxLat = t.latency_ms;
    }
  });

  var rows = traces.slice(0, 10).map(function(t) {
    var w = (t.latency_ms !== null && t.latency_ms !== undefined)
      ? Math.round((t.latency_ms / maxLat) * 100) + '%' : '0%';
    var latLabel = (t.latency_ms !== null && t.latency_ms !== undefined)
      ? Math.round(t.latency_ms) + 'ms' : 'n/d';
    return '<div class="node">'
      + '<span class="nm"></span>'
      + '<span class="nn mono">' + esc(t.name || t.id) + '</span>'
      + '<span class="nbar"><i style="width:' + w + '"></i></span>'
      + '<span class="nt mono">' + esc(latLabel) + '</span>'
      + '</div>';
  });
  list.innerHTML = rows.join('');

  if (hint) hint.textContent = traces.length + ' trazas';

  // Avg faithfulness (null-safe)
  var faithScores = traces.filter(function(t) {
    return t.faithfulness !== null && t.faithfulness !== undefined;
  });
  var faithText = 'n/d';
  if (faithScores.length) {
    var avg = faithScores.reduce(function(s, t) { return s + t.faithfulness; }, 0) / faithScores.length;
    faithText = avg.toFixed(2);
  }
  setText('tf-faith', faithText);

  // Sum tokens (null-safe)
  var totalTokens = traces.reduce(function(s, t) {
    return s + (t.total_tokens !== null && t.total_tokens !== undefined ? t.total_tokens : 0);
  }, 0);
  setText('tf-tokens', totalTokens ? String(totalTokens) : 'n/d');

  // Push tokens into chart buffer
  pushBuffer('tokens', totalTokens || null);
  var tokBig = el('chart-tokens-big');
  if (tokBig) {
    tokBig.textContent = totalTokens ? String(totalTokens) : 'n/d';
    tokBig.classList.toggle('nd', !totalTokens);
  }
}

// ---------------------------------------------------------------------------
// Render: Infra panels
// ---------------------------------------------------------------------------

function renderInfra(data) {
  // Counts
  setText('kpi-nodes', nd(data.node_count));
  setText('kpi-pods', nd(data.pod_count));

  // Restarts sum
  var totalRestarts = (data.pods || []).reduce(function(s, p) {
    return s + (p.restarts !== null && p.restarts !== undefined ? p.restarts : 0);
  }, 0);
  setText('kpi-restarts', String(totalRestarts));
  setText('kpi-infra-restarts', String(totalRestarts));

  // Nodes table
  var nodesTbody = el('nodesTbody');
  if (nodesTbody) {
    if (!data.nodes || !data.nodes.length) {
      nodesTbody.innerHTML = '<tr><td colspan="4" style="color:var(--text-faint);text-align:center;padding:20px">Sin nodos</td></tr>';
    } else {
      nodesTbody.innerHTML = data.nodes.map(function(n) {
        var cpuPct = n.cpu_pct !== null && n.cpu_pct !== undefined ? Math.round(n.cpu_pct) : null;
        var memPct = n.mem_pct !== null && n.mem_pct !== undefined ? Math.round(n.mem_pct) : null;
        return '<tr>'
          + '<td class="mono">' + esc(n.name) + '</td>'
          + '<td class="mono">' + (cpuPct !== null ? cpuPct + '%' : '<span class="nd">n/d</span>') + '</td>'
          + '<td class="mono">' + (memPct !== null ? memPct + '%' : '<span class="nd">n/d</span>') + '</td>'
          + '<td><span class="sev info" style="color:var(--ok)"><span class="s" style="background:var(--ok)"></span>Ready</span></td>'
          + '</tr>';
      }).join('');
    }
  }

  var nodesHint = el('nodes-hint');
  if (nodesHint) nodesHint.textContent = data.node_count + ' nodos';

  // Pods table
  var podsTbody = el('podsTbody');
  if (podsTbody) {
    if (!data.pods || !data.pods.length) {
      podsTbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-faint);text-align:center;padding:20px">Sin pods</td></tr>';
    } else {
      podsTbody.innerHTML = data.pods.map(function(p) {
        var rst = p.restarts !== null && p.restarts !== undefined ? p.restarts : null;
        var rstStyle = (rst !== null && rst > 0) ? 'style="color:var(--warn)"' : '';
        return '<tr>'
          + '<td class="mono">' + esc(p.name) + '</td>'
          + '<td class="mono">' + esc(p.namespace || '—') + '</td>'
          + '<td class="mono">' + (p.cpu_cores !== null && p.cpu_cores !== undefined ? fmtCores(p.cpu_cores) : '<span class="nd">n/d</span>') + '</td>'
          + '<td class="mono">' + (p.mem_bytes !== null && p.mem_bytes !== undefined ? fmtBytes(p.mem_bytes) : '<span class="nd">n/d</span>') + '</td>'
          + '<td class="mono" ' + rstStyle + '>' + (rst !== null ? rst : '<span class="nd">n/d</span>') + '</td>'
          + '</tr>';
      }).join('');
    }
  }

  var podsHint = el('pods-hint');
  if (podsHint) podsHint.textContent = data.pod_count + ' pods';

  // Avg CPU for chart
  var cpuVals = (data.nodes || [])
    .filter(function(n) { return n.cpu_pct !== null && n.cpu_pct !== undefined; })
    .map(function(n) { return n.cpu_pct; });
  var avgCpu = cpuVals.length
    ? cpuVals.reduce(function(s, v) { return s + v; }, 0) / cpuVals.length
    : null;
  pushBuffer('cpu', avgCpu);
  var cpuBig = el('chart-cpu-big');
  if (cpuBig) {
    cpuBig.textContent = avgCpu !== null ? Math.round(avgCpu) + '%' : 'n/d';
    cpuBig.classList.toggle('nd', avgCpu === null);
  }
}

// ---------------------------------------------------------------------------
// Render: Workloads panel (Señales de falla — top section)
// ---------------------------------------------------------------------------

function renderWorkloads(data) {
  // Store for combined banner
  lastWorkloadsData = data;
  applyBannerStatus(worstStatus(lastHealthData, lastWorkloadsData, false));

  // Réplicas KPI
  var shortfalls = data.replica_shortfalls || [];
  var replicasEl = el('kpi-replicas');
  var replicasTag = el('kpi-replicas-tag');
  var replicasFoot = el('kpi-replicas-foot');
  if (replicasEl) {
    if (shortfalls.length === 0) {
      replicasEl.textContent = 'OK';
      replicasEl.className = 'k-val mono';
      replicasEl.style.color = 'var(--ok)';
      if (replicasTag) { replicasTag.className = 'tag ok'; replicasTag.textContent = 'ok'; }
      if (replicasFoot) replicasFoot.textContent = 'todos en spec';
    } else {
      replicasEl.textContent = String(shortfalls.length);
      replicasEl.className = 'k-val mono';
      replicasEl.style.color = 'var(--crit)';
      if (replicasTag) { replicasTag.className = 'tag crit'; replicasTag.textContent = 'alerta'; }
      if (replicasFoot) replicasFoot.textContent = shortfalls.length === 1 ? '1 sub-replicada' : shortfalls.length + ' sub-replicadas';
    }
  }

  // Store workloads section HTML for combined render with events
  var html = '';

  // Partial notice — MUST appear first, never hidden
  if (data.errors && data.errors.length) {
    html += '<div class="fallas-partial"><span class="fp-icon">&#x26A0;</span><span>'
      + 'Estado parcial — no se pudo leer: '
      + data.errors.map(function(e) { return esc(e); }).join(', ')
      + '</span></div>';
  }

  // Pod issues
  var podIssues = data.pod_issues || [];
  if (podIssues.length) {
    html += '<div class="fallas-section"><div class="fallas-heading">Workloads con problema</div>';
    podIssues.forEach(function(p) {
      var isCrit = CRIT_PROBLEMS.indexOf(p.problem) !== -1;
      // M-3: missing namespace/pod renders '?' instead of bare '/'
      html += '<div class="fallas-row">'
        + '<span class="frow-id">' + esc(p.namespace || '?') + '/' + esc(p.pod || '?') + '</span>'
        + '<span class="fbadge ' + (isCrit ? 'crit' : 'warn') + '">' + esc(p.problem) + '</span>'
        + (p.detail ? '<span class="frow-detail">' + esc(p.detail) + '</span>' : '')
        + '</div>';
    });
    html += '</div>';
  }

  // Replica shortfalls
  if (shortfalls.length) {
    html += '<div class="fallas-section"><div class="fallas-heading">Réplicas</div>';
    shortfalls.forEach(function(r) {
      // I-3: missing available/desired shows 'n/d' instead of 'undefined'
      html += '<div class="fallas-row">'
        + '<span class="frow-id">' + esc(r.kind) + ' ' + esc(r.name) + ' <span style="color:var(--text-faint);font-size:10.5px">(' + esc(r.namespace) + ')</span></span>'
        + '<span class="fbadge crit">' + esc(nd(r.available)) + '/' + esc(nd(r.desired)) + '</span>'
        + '</div>';
    });
    html += '</div>';
  }

  // PVC issues
  var pvcIssues = data.pvc_issues || [];
  if (pvcIssues.length) {
    html += '<div class="fallas-section"><div class="fallas-heading">PVCs</div>';
    pvcIssues.forEach(function(pvc) {
      var sev = pvc.phase === 'Lost' ? 'crit' : 'warn';
      html += '<div class="fallas-row">'
        + '<span class="frow-id">' + esc(pvc.namespace) + '/' + esc(pvc.name) + '</span>'
        + '<span class="fbadge ' + sev + '">' + esc(pvc.phase) + '</span>'
        + '</div>';
    });
    html += '</div>';
  }

  // Save partial HTML keyed under 'workloads' for merging with events (M-4: use fallasState)
  fallasState.workloadsHtml = html;
  fallasState.workloadsHasErrors = !!(data.errors && data.errors.length);
  fallasState.workloadsErr = false;  // I-1: success → no fetch error
  _renderFallasPanel();
}

// ---------------------------------------------------------------------------
// Render: Events panel (Señales de falla — bottom section)
// ---------------------------------------------------------------------------

function renderEvents(events) {
  var html = '';

  if (events && events.length) {
    html += '<div class="fallas-section"><div class="fallas-heading">Eventos Warning</div>';
    events.slice(0, 50).forEach(function(ev) {
      var msg = ev.message ? String(ev.message) : '';
      if (msg.length > 160) msg = msg.slice(0, 157) + '…';
      html += '<div class="fallas-row" style="align-items:flex-start">'
        + '<span class="frow-id mono" style="font-size:11px;color:var(--text-faint);min-width:55px">' + esc(fmtTime(ev.ts)) + '</span>'
        + '<span class="fbadge warn" style="margin-top:1px">' + esc(ev.reason || '?') + '</span>'
        + '<span style="flex:1;min-width:0;font-size:11.5px;display:flex;flex-direction:column;gap:1px;">'
        + '<span style="color:var(--text-dim)">' + esc(ev.kind || '') + '/' + esc(ev.name || '') + '</span>'
        + '<span style="color:var(--text-faint);font-size:11px">' + esc(msg) + '</span>'
        + '</span>'
        + (ev.count && ev.count > 1 ? '<span class="fbadge count">&times;' + esc(String(ev.count)) + '</span>' : '')
        + '</div>';
    });
    html += '</div>';
  }

  fallasState.eventsHtml = html;
  fallasState.eventsErr = false;  // I-1: success → no fetch error
  _renderFallasPanel();
}

// Merge workloads + events into the panel body; show empty state only when both are loaded + empty
function _renderFallasPanel() {
  var panel = el('panelFallas'); if (!panel) return;
  var body = el('fallasBody'); if (!body) return;

  var wHtml = fallasState.workloadsHtml;
  var eHtml = fallasState.eventsHtml;

  // Not both fetches have resolved yet — keep "Cargando…"
  if (wHtml === undefined || eHtml === undefined) return;

  var combined = (wHtml || '') + (eHtml || '');

  var hasErrors = fallasState.workloadsHasErrors;

  if (!combined && !hasErrors) {
    body.innerHTML = '<div class="fallas-empty">Sin señales de falla</div>';
    var hint = el('fallas-hint');
    if (hint) hint.textContent = 'sin incidencias';
  } else {
    body.innerHTML = combined || '';
    var hint2 = el('fallas-hint');
    if (hint2) {
      var count = (body.querySelectorAll('.fallas-row').length);
      hint2.textContent = count ? count + ' señal' + (count > 1 ? 'es' : '') : (hasErrors ? 'parcial' : '—');
    }
  }

  // I-1: error chip reflects either fetch having failed — order-independent
  if (fallasState.workloadsErr || fallasState.eventsErr) {
    markPanelError('panelFallas', 'error');
  } else {
    markPanelOk('panelFallas');
  }
}

// ---------------------------------------------------------------------------
// Chart redraw — called after each refreshAll
// ---------------------------------------------------------------------------

function redrawCharts() {
  var slideEls = document.querySelectorAll('#caroTrack .slide');
  slideEls.forEach(function(slide) {
    var key = slide.dataset.chart;
    if (!key || !chartBuffers[key] || !chartBuffers[key].length) return;
    var buf = chartBuffers[key];
    var svg = slide.querySelector('svg');
    if (!svg) return;

    // Seed the original max once into a read-only attribute so it is never overwritten.
    if (!svg.dataset.maxSeed) {
      svg.dataset.maxSeed = svg.dataset.max || '0';
    }
    var seedMax = Number(svg.dataset.maxSeed) || 0;

    // Compute dynMax fresh each redraw from the buffer + original seed — never persist it back.
    var bufMax = Math.max.apply(null, buf);
    var dynMax = Math.max(bufMax * 1.1, seedMax);
    if (dynMax > 0) svg.dataset.max = String(Math.round(dynMax));

    svg.dataset.vals = buf.join(',');
    drawInto(svg);
  });
}

// ---------------------------------------------------------------------------
// refreshAll — Promise.allSettled fan-out
// ---------------------------------------------------------------------------

function refreshAll() {
  if (refreshing) return;
  refreshing = true;
  var myBatch = ++batchSeq;   // tag this batch with a unique id
  activeBatch = myBatch;      // this is the batch allowed to clear refreshing

  // Mark panels loading
  ['panelCharts', 'panelTraces', 'panelLogs', 'panelNodes', 'panelPods', 'panelFallas'].forEach(setPanelLoading);

  // Reset the panel merge state so stale data from the previous cycle doesn't persist (M-4)
  fallasState = { workloadsHtml: undefined, eventsHtml: undefined, workloadsHasErrors: false, workloadsErr: false, eventsErr: false };

  var p = [
    apiFetch('/health'),
    apiFetch('/logs?limit=100'),
    apiFetch('/traces?limit=20'),
    apiFetch('/infra'),
    apiFetch('/latency'),
    apiFetch('/workloads'),
    apiFetch('/events?limit=50')
  ];

  Promise.allSettled(p).then(function(results) {
    var healthR = results[0], logsR = results[1], tracesR = results[2],
        infraR = results[3], latencyR = results[4],
        workloadsR = results[5], eventsR = results[6];

    // Health + KPIs
    if (healthR.status === 'fulfilled') {
      renderHealth(healthR.value);
    } else if (healthR.reason && healthR.reason.message === 'stale-env') {
      // stale-env: env switched mid-flight; fresh batch is already loading — skip
    } else {
      markPanelError('bannerApp', 'health err');
      console.warn('[obs] /health failed:', healthR.reason);
    }

    // Logs
    if (logsR.status === 'fulfilled') {
      renderLogs(logsR.value);
      markPanelOk('panelLogs');
    } else if (logsR.reason && logsR.reason.message === 'stale-env') {
      // stale-env: skip
    } else {
      markPanelError('panelLogs', 'error');
      console.warn('[obs] /logs failed:', logsR.reason);
    }

    // Traces
    if (tracesR.status === 'fulfilled') {
      renderTraces(tracesR.value);
      markPanelOk('panelTraces');
    } else if (tracesR.reason && tracesR.reason.message === 'stale-env') {
      // stale-env: skip
    } else {
      markPanelError('panelTraces', 'error');
      console.warn('[obs] /traces failed:', tracesR.reason);
    }

    // Infra
    if (infraR.status === 'fulfilled') {
      renderInfra(infraR.value);
      markPanelOk('panelNodes');
      markPanelOk('panelPods');
    } else if (infraR.reason && infraR.reason.message === 'stale-env') {
      // stale-env: skip
    } else {
      markPanelError('panelNodes', 'error');
      markPanelError('panelPods', 'error');
      console.warn('[obs] /infra failed:', infraR.reason);
    }

    // Latency
    if (latencyR.status === 'fulfilled') {
      renderLatency(latencyR.value);
      markPanelOk('panelCharts');
    } else if (latencyR.reason && latencyR.reason.message === 'stale-env') {
      // stale-env: skip
    } else {
      markPanelError('panelCharts', 'error');
      console.warn('[obs] /latency failed:', latencyR.reason);
    }

    // Workloads (Señales de falla — workloads section + réplicas KPI + banner escalation)
    // I-1: do NOT call markPanelOk/markPanelError here — _renderFallasPanel handles the chip
    if (workloadsR.status === 'fulfilled') {
      renderWorkloads(workloadsR.value);
      // renderWorkloads sets fallasState.workloadsErr = false and calls _renderFallasPanel
    } else if (workloadsR.reason && workloadsR.reason.message === 'stale-env') {
      // stale-env: skip
    } else {
      // Hard failure: /workloads unreadable — mark as partial/blind on banner
      lastWorkloadsData = null;
      applyBannerStatus(worstStatus(lastHealthData, null, true));
      // Set workloadsHtml to empty string (not undefined) so the panel can still render events
      fallasState.workloadsHtml = '';
      fallasState.workloadsHasErrors = false;
      fallasState.workloadsErr = true;  // I-1: record the fetch failure
      _renderFallasPanel();
      console.warn('[obs] /workloads failed:', workloadsR.reason);
    }

    // Events (Señales de falla — events section)
    // I-1: do NOT call markPanelOk/markPanelError here — _renderFallasPanel handles the chip
    if (eventsR.status === 'fulfilled') {
      renderEvents(eventsR.value);
      // renderEvents sets fallasState.eventsErr = false and calls _renderFallasPanel
    } else if (eventsR.reason && eventsR.reason.message === 'stale-env') {
      // stale-env: skip
    } else {
      // Set eventsHtml to empty string so the panel can still render workloads
      fallasState.eventsErr = true;  // I-1: record the fetch failure
      if (fallasState.eventsHtml === undefined) { fallasState.eventsHtml = ''; }
      _renderFallasPanel();
      console.warn('[obs] /events failed:', eventsR.reason);
    }

    // After all data is in, redraw charts
    redrawCharts();

    // Update freshness timestamp
    var now = new Date();
    var ts = [now.getHours(), now.getMinutes(), now.getSeconds()]
      .map(function(n) { return (n < 10 ? '0' : '') + n; }).join(':');
    setText('lastUpd', ts);
  }).finally(function() {
    // Only the latest batch may clear the flag; a superseded batch must not
    // clobber the refreshing=true that the env-switch's fresh batch set.
    if (activeBatch === myBatch) refreshing = false;
  });
}

// ---------------------------------------------------------------------------
// Env switch integration — wrap original setEnv to trigger data refresh
// ---------------------------------------------------------------------------

(function() {
  var originalSetEnv = window.setEnv;
  window.setEnv = function(env) {
    if (originalSetEnv) originalSetEnv(env);
    currentEnv = env;
    envGen++;         // invalidate any in-flight old-env fetches
    refreshing = false; // release guard so the switch's refreshAll runs as a new batch
    // Reset chart buffers and last-known data on env change
    chartBuffers = { p95: [], p50: [], tokens: [], cpu: [] };
    lastHealthData    = null;
    lastWorkloadsData = null;
    refreshAll();
  };
})();

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

// Initial load
refreshAll();

// Poll every 12 seconds
setInterval(refreshAll, 12000);
