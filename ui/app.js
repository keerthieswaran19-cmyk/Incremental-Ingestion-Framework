// IngestOps Enterprise Operations Cockpit Frontend Controller

let currentView = 'overview';
let cachedRuns = [];
let cachedDLQ = [];
let cachedTargets = [];
let cachedHistory = [];
let currentEventTab = 'target';
let throughputChartInstance = null;
let splitChartInstance = null;

// Generic Fetch Wrapper
async function apiGet(endpoint) {
  try {
    const res = await fetch(endpoint);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.error(`API GET error on ${endpoint}:`, err);
    return null;
  }
}

async function apiPost(endpoint, body) {
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.error(`API POST error on ${endpoint}:`, err);
    return null;
  }
}

// Navigation View Router
function navigate(viewName) {
  currentView = viewName;

  // Update sidebar active link
  document.querySelectorAll('.nav-item').forEach(item => {
    const text = item.querySelector('span')?.innerText.toLowerCase() || '';
    const match = 
      (viewName === 'overview' && text.includes('overview')) ||
      (viewName === 'executions' && text.includes('execution')) ||
      (viewName === 'events' && text.includes('event')) ||
      (viewName === 'quarantine' && text.includes('dead-letter')) ||
      (viewName === 'reconciliation' && text.includes('conservation')) ||
      (viewName === 'watermarks' && text.includes('watermark')) ||
      (viewName === 'health' && text.includes('system'));
    item.classList.toggle('active', match);
  });

  // Update panels
  document.querySelectorAll('.view-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `view-${viewName}`);
  });

  // Update breadcrumb
  const titles = {
    overview: 'Operations Overview',
    executions: 'Pipeline Executions',
    events: 'Event Explorer',
    quarantine: 'Dead-Letter Queue (DLQ)',
    reconciliation: 'Conservation Reconciliation Proof',
    watermarks: 'Watermark Registry',
    health: 'System Health & Metrics'
  };
  document.getElementById('header-breadcrumb').innerText = titles[viewName] || 'Overview';

  // Render view-specific data
  if (viewName === 'overview') renderOverview();
  else if (viewName === 'executions') renderExecutions();
  else if (viewName === 'events') renderEvents();
  else if (viewName === 'quarantine') renderQuarantine();
  else if (viewName === 'reconciliation') renderReconciliation();
  else if (viewName === 'watermarks') renderWatermarks();
  else if (viewName === 'health') renderHealth();

  if (window.lucide) lucide.createIcons();
}

function refreshCurrentView() {
  loadAllData().then(() => {
    navigate(currentView);
  });
}

// Master Data Loader
async function loadAllData() {
  const [overview, runs, dlq, watermarks] = await Promise.all([
    apiGet('/api/overview'),
    apiGet('/api/runs?limit=100'),
    apiGet('/api/quarantine?limit=100'),
    apiGet('/api/watermarks'),
  ]);

  cachedRuns = runs || [];
  cachedDLQ = dlq || [];

  if (overview) {
    document.getElementById('kpi-active-targets').innerText = (overview.target_snapshot.active_records || 0).toLocaleString();
    document.getElementById('kpi-tombstones').innerText = `Tombstones: ${(overview.target_snapshot.tombstones || 0).toLocaleString()}`;
    document.getElementById('kpi-source-events').innerText = (overview.table_counts.source_events || 0).toLocaleString();
    document.getElementById('kpi-quarantine').innerText = (overview.table_counts.quarantine || 0).toLocaleString();
    document.getElementById('kpi-peak-speed').innerText = `${(overview.performance.max_records_per_sec || 0).toLocaleString()} r/s`;
    document.getElementById('kpi-avg-speed').innerText = `Avg: ${(overview.performance.avg_records_per_sec || 0).toLocaleString()} r/s`;

    if (watermarks && watermarks.length > 0) {
      document.getElementById('kpi-watermark').innerText = watermarks[0].high_watermark;
    } else {
      document.getElementById('kpi-watermark').innerText = '1970-01-01T00:00:00Z';
    }
  }

  renderCharts();
}

// Render Charts
function renderCharts() {
  if (typeof Chart === 'undefined') return;

  const validRuns = [...cachedRuns].filter(r => r.status === 'SUCCESS').slice(0, 10).reverse();
  const labels = validRuns.map((r, i) => `Run #${i + 1}`);
  const speeds = validRuns.map(r => r.records_per_sec || 0);

  // 1. Throughput Line Chart
  const throughputCanvas = document.getElementById('chart-throughput');
  if (throughputCanvas) {
    if (throughputChartInstance) throughputChartInstance.destroy();
    throughputChartInstance = new Chart(throughputCanvas, {
      type: 'line',
      data: {
        labels: labels.length ? labels : ['Run 1'],
        datasets: [{
          label: 'Throughput (records/sec)',
          data: speeds.length ? speeds : [0],
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.08)',
          fill: true,
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 4,
          pointBackgroundColor: '#38bdf8',
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#0d121c',
            titleColor: '#fff',
            bodyColor: '#94a3b8',
            borderColor: '#1e293b',
            borderWidth: 1,
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: { color: '#64748b', font: { size: 10 } }
          },
          y: {
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: { color: '#64748b', font: { size: 10 } }
          }
        }
      }
    });
  }

  // 2. Operations Split Donut Chart
  let totalInserts = 0, totalUpdates = 0, totalDeletes = 0, totalDLQ = 0;
  cachedRuns.forEach(r => {
    totalInserts += r.inserted_count || 0;
    totalUpdates += r.updated_count || 0;
    totalDeletes += r.deleted_count || 0;
    totalDLQ += r.quarantined_count || 0;
  });

  const splitCanvas = document.getElementById('chart-split');
  if (splitCanvas) {
    if (splitChartInstance) splitChartInstance.destroy();
    splitChartInstance = new Chart(splitCanvas, {
      type: 'doughnut',
      data: {
        labels: ['Inserts', 'Updates', 'Deletes', 'Quarantined'],
        datasets: [{
          data: (totalInserts + totalUpdates + totalDeletes + totalDLQ > 0)
            ? [totalInserts, totalUpdates, totalDeletes, totalDLQ]
            : [1, 0, 0, 0],
          backgroundColor: ['#10b981', '#3b82f6', '#f59e0b', '#ef4444'],
          borderWidth: 0,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'right',
            labels: { color: '#94a3b8', boxWidth: 10, font: { size: 11 } }
          }
        },
        cutout: '72%'
      }
    });
  }
}

// 1. Overview Renderer
function renderOverview() {
  const tbody = document.getElementById('overview-runs-body');
  if (!tbody) return;

  const slice = cachedRuns.slice(0, 5);
  if (!slice.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-state">No pipeline runs recorded yet. Click "Ingest" above to trigger a run.</td></tr>`;
    return;
  }

  tbody.innerHTML = slice.map(r => `
    <tr>
      <td><code class="mono">${r.run_id}</code></td>
      <td>${r.start_time ? r.start_time.substring(0, 19).replace('T', ' ') : '--'}</td>
      <td>${getStatusTag(r.status)}</td>
      <td><strong>${(r.input_count || 0).toLocaleString()}</strong></td>
      <td style="color: #34d399; font-weight: 600;">+${(r.inserted_count || 0).toLocaleString()}</td>
      <td style="color: #60a5fa;">${(r.updated_count || 0).toLocaleString()}</td>
      <td style="color: #fbbf24;">-${(r.deleted_count || 0).toLocaleString()}</td>
      <td style="color: #f87171;">${(r.quarantined_count || 0).toLocaleString()}</td>
      <td>${r.duration_ms ? r.duration_ms.toFixed(1) + 'ms' : '--'}</td>
      <td>${r.records_per_sec ? r.records_per_sec.toFixed(0) + ' r/s' : '--'}</td>
    </tr>
  `).join('');
}

// 2. Executions Renderer
let execFilter = 'ALL';
function setExecFilter(filter, el) {
  execFilter = filter;
  document.querySelectorAll('#view-executions .filter-chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  renderExecutions();
}

function filterExecutions() {
  renderExecutions();
}

function renderExecutions() {
  const tbody = document.getElementById('executions-tbody');
  if (!tbody) return;

  const query = (document.getElementById('exec-search')?.value || '').toLowerCase().trim();
  let filtered = cachedRuns.filter(r => {
    if (execFilter === 'SUCCESS' && r.status !== 'SUCCESS') return false;
    if (execFilter === 'FAILED' && r.status !== 'FAILED') return false;
    if (execFilter === 'SKIPPED' && !r.status.includes('SKIPPED')) return false;
    if (query && !r.run_id.toLowerCase().includes(query)) return false;
    return true;
  });

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty-state">No execution runs matching current filter.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(r => `
    <tr>
      <td><code class="mono">${r.run_id}</code></td>
      <td>${r.start_time ? r.start_time.substring(0, 19).replace('T', ' ') : '--'}</td>
      <td>${getStatusTag(r.status)}</td>
      <td><strong>${(r.input_count || 0).toLocaleString()}</strong></td>
      <td style="color: #34d399; font-weight: 600;">+${r.inserted_count || 0}</td>
      <td style="color: #60a5fa;">${r.updated_count || 0}</td>
      <td style="color: #fbbf24;">-${r.deleted_count || 0}</td>
      <td style="color: #f87171;">${r.quarantined_count || 0}</td>
      <td style="color: #94a3b8;">${r.duplicate_count || 0}</td>
      <td>${r.duration_ms ? r.duration_ms.toFixed(1) + 'ms' : '--'}</td>
      <td>${r.records_per_sec ? r.records_per_sec.toFixed(0) + ' r/s' : '--'}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="verifyRunReconciliation('${r.run_id}')">Reconcile</button>
      </td>
    </tr>
  `).join('');
}

// 3. Event Explorer Renderer
function switchEventTab(tab) {
  currentEventTab = tab;
  document.getElementById('chip-target-tab').classList.toggle('active', tab === 'target');
  document.getElementById('chip-history-tab').classList.toggle('active', tab === 'history');
  renderEvents();
}

function searchEvents() {
  renderEvents();
}

async function renderEvents() {
  const thead = document.getElementById('events-thead');
  const tbody = document.getElementById('events-tbody');
  const search = document.getElementById('events-search')?.value.trim();

  if (currentEventTab === 'target') {
    thead.innerHTML = `
      <tr>
        <th>Natural Key</th>
        <th>Entity Type</th>
        <th>Version</th>
        <th>Status</th>
        <th>First Seen</th>
        <th>Last Updated (UTC)</th>
        <th>Payload Action</th>
      </tr>
    `;
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Querying active target records...</td></tr>`;
    
    const url = search ? `/api/target-records?search=${encodeURIComponent(search)}` : '/api/target-records?limit=50';
    const targets = await apiGet(url);
    cachedTargets = targets || [];

    if (!cachedTargets.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No target records found.</td></tr>`;
      return;
    }

    tbody.innerHTML = cachedTargets.map(t => `
      <tr>
        <td><strong>${t.natural_key}</strong></td>
        <td><span class="status-tag status-info">${t.entity_type}</span></td>
        <td><code class="mono">v${t.version}</code></td>
        <td>${t.is_deleted ? '<span class="status-tag status-danger">TOMBSTONED</span>' : '<span class="status-tag status-success">ACTIVE</span>'}</td>
        <td>${t.first_seen_at ? t.first_seen_at.substring(0, 19).replace('T', ' ') : '--'}</td>
        <td>${t.last_updated_at ? t.last_updated_at.substring(0, 19).replace('T', ' ') : '--'}</td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick='inspectRecord("Target Record: ${t.natural_key}", "Curated Active Entity Snapshot", ${JSON.stringify(t.payload)})'>View JSON</button>
        </td>
      </tr>
    `).join('');

  } else {
    thead.innerHTML = `
      <tr>
        <th>Mutation ID</th>
        <th>Natural Key</th>
        <th>Operation</th>
        <th>Temporal Order</th>
        <th>Event Timestamp</th>
        <th>Run ID</th>
        <th>Payload Action</th>
      </tr>
    `;
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Querying CDC event history...</td></tr>`;

    const url = search ? `/api/events?natural_key=${encodeURIComponent(search)}` : '/api/events?limit=50';
    const history = await apiGet(url);
    cachedHistory = history || [];

    if (!cachedHistory.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No event mutations found.</td></tr>`;
      return;
    }

    tbody.innerHTML = cachedHistory.map(h => `
      <tr>
        <td><code class="mono">${h.history_id.substring(0, 10)}...</code></td>
        <td><strong>${h.natural_key}</strong></td>
        <td><span class="status-tag ${h.operation_type === 'INSERT' ? 'status-success' : h.operation_type === 'UPDATE' ? 'status-info' : 'status-danger'}">${h.operation_type}</span></td>
        <td>${h.is_out_of_order ? '<span class="status-tag status-warning">LATE ARRIVAL</span>' : '<span class="status-tag status-success">IN-ORDER</span>'}</td>
        <td>${h.event_timestamp ? h.event_timestamp.substring(0, 19).replace('T', ' ') : '--'}</td>
        <td><code class="mono">${h.run_id}</code></td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick='inspectRecord("CDC Event: ${h.natural_key}", "Operation: ${h.operation_type}", ${JSON.stringify(h.payload)})'>View JSON</button>
        </td>
      </tr>
    `).join('');
  }
}

// 4. Dead-Letter Queue (DLQ) Renderer
let dlqFilter = 'ALL';
function setDLQFilter(filter, el) {
  dlqFilter = filter;
  document.querySelectorAll('#dlq-filter-chips .filter-chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  renderQuarantine();
}

function filterDLQ() {
  renderQuarantine();
}

function renderQuarantine() {
  const tbody = document.getElementById('quarantine-tbody');
  if (!tbody) return;

  const query = (document.getElementById('dlq-search')?.value || '').toLowerCase().trim();

  let filtered = cachedDLQ.filter(q => {
    if (dlqFilter !== 'ALL' && q.error_code !== dlqFilter) return false;
    if (query && !q.error_code.toLowerCase().includes(query) && !q.error_message.toLowerCase().includes(query)) return false;
    return true;
  });

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state" style="color: #34d399;">Dead-Letter Queue Clean! 0 anomalies matching filter.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(q => `
    <tr>
      <td><code class="mono">${q.quarantine_id.substring(0, 12)}...</code></td>
      <td><span class="status-tag status-danger">${q.error_code}</span></td>
      <td>${q.error_message}</td>
      <td><span class="status-tag status-warning">${q.occurrence_count || 1}x</span></td>
      <td>${q.source_file}</td>
      <td><code class="mono">${q.run_id}</code></td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick='inspectRecord("Quarantined Anomaly: ${q.quarantine_id.substring(0, 8)}", "${q.error_code}: ${q.error_message}", ${JSON.stringify(q.raw_payload)})'>Inspect</button>
      </td>
    </tr>
  `).join('');
}

// 5. Conservation Proof Renderer
async function renderReconciliation() {
  const data = await apiGet('/api/reconciliation');
  const tbody = document.getElementById('reconciliation-tbody');
  if (!tbody || !data) return;

  const verified = data.recent_runs_reconciliation || [];
  if (!verified.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-state">No reconciliation audit runs yet.</td></tr>`;
    return;
  }

  tbody.innerHTML = verified.map(v => `
    <tr>
      <td><code class="mono">${v.run_id}</code></td>
      <td><strong>${v.metrics.input_count}</strong></td>
      <td style="color: #34d399;">+${v.metrics.inserted_count}</td>
      <td style="color: #60a5fa;">${v.metrics.updated_count}</td>
      <td style="color: #fbbf24;">-${v.metrics.deleted_count}</td>
      <td style="color: #f87171;">${v.metrics.quarantined_count}</td>
      <td style="color: #94a3b8;">${v.metrics.duplicate_count}</td>
      <td>${v.metrics.sum_of_components}</td>
      <td><strong style="color: ${v.metrics.discrepancy === 0 ? '#34d399' : '#f87171'};">${v.metrics.discrepancy}</strong></td>
      <td>
        ${v.is_mathematically_sound 
          ? '<span class="status-tag status-success">✓ 100% BALANCED</span>' 
          : '<span class="status-tag status-danger">✕ DISCREPANCY</span>'}
      </td>
    </tr>
  `).join('');
}

// 6. Watermark Registry Renderer
async function renderWatermarks() {
  const watermarks = await apiGet('/api/watermarks');
  const tbody = document.getElementById('watermarks-tbody');
  if (!tbody) return;

  if (!watermarks || !watermarks.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-state">No watermarks registered.</td></tr>`;
    return;
  }

  tbody.innerHTML = watermarks.map(w => `
    <tr>
      <td><span class="status-tag status-info">${w.entity_name}</span></td>
      <td><code class="mono" style="color: #38bdf8; font-size: 13px;">${w.high_watermark}</code></td>
      <td>${w.updated_at ? w.updated_at.substring(0, 19).replace('T', ' ') : '--'}</td>
      <td><code class="mono">${w.last_run_id}</code></td>
    </tr>
  `).join('');
}

// 7. System Health Renderer
async function renderHealth() {
  const overview = await apiGet('/api/overview');
  const tbody = document.getElementById('health-tables-body');
  if (!tbody || !overview) return;

  const tables = [
    { name: 'source_events', desc: 'Raw Landing Audit Log', pk: 'event_id', role: 'Immutable Audit Ledger' },
    { name: 'target_records', desc: 'Curated Active Entities (SCD-4)', pk: 'entity_id', role: 'Current Point-in-Time Snapshot' },
    { name: 'event_history', desc: 'Append-Only CDC Mutations', pk: 'history_id', role: 'Time-Travel Audit Ledger' },
    { name: 'quarantine', desc: 'Dead-Letter Queue (DLQ)', pk: 'quarantine_id', role: 'Zero Silent Data Loss Queue' },
    { name: 'pipeline_runs', desc: 'Execution Metrics & Latency', pk: 'run_id', role: 'Operational Observability' },
    { name: 'watermark_state', desc: 'High Watermarks per Entity', pk: 'entity_name', role: 'Incremental State Horizon' },
    { name: 'file_registry', desc: 'SHA-256 File Checksums', pk: 'file_hash', role: 'Idempotency Cache' },
  ];

  tbody.innerHTML = tables.map(t => `
    <tr>
      <td><code class="mono" style="color: #38bdf8;">${t.name}</code></td>
      <td>${t.desc}</td>
      <td><strong>${(overview.table_counts[t.name] || 0).toLocaleString()}</strong> rows</td>
      <td><code class="mono">${t.pk}</code></td>
      <td><span class="status-tag status-info">${t.role}</span></td>
    </tr>
  `).join('');
}

// Actions
async function runIngestion() {
  const btn = document.getElementById('btn-trigger');
  const file = document.getElementById('runner-file-select')?.value;
  const force = document.getElementById('runner-force')?.checked;
  const inc = document.getElementById('runner-incremental')?.checked;

  btn.disabled = true;
  btn.innerHTML = `<i data-lucide="loader" style="width: 12px; height: 12px; animation: spin 1s linear infinite;"></i> Ingesting...`;

  const res = await apiPost('/api/ingest', {
    source_file: file,
    entity_name: "CUSTOMER",
    force_reprocess: force,
    incremental: inc,
  });

  btn.disabled = false;
  btn.innerHTML = `<i data-lucide="play" style="width: 12px; height: 12px;"></i> Ingest`;

  if (res) {
    await loadAllData();
    navigate(currentView);
  }
}

async function generateSample() {
  const res = await apiPost('/api/generate-sample?count=100', {});
  if (res) {
    alert("Generated fresh 100 dirty records into data/source/sample_data.json!");
    await loadAllData();
    navigate(currentView);
  }
}

async function verifyRunReconciliation(runId) {
  const res = await apiGet(`/api/runs/${runId}/reconcile`);
  if (res) {
    alert(
      `Conservation Balance for ${runId}:\n\n` +
      `• Audit Soundness: ${res.is_mathematically_sound ? "100% BALANCED" : "DISCREPANCY"}\n` +
      `• Total Input: ${res.metrics.input_count}\n` +
      `• Inserts: ${res.metrics.inserted_count}\n` +
      `• Updates: ${res.metrics.updated_count}\n` +
      `• Deletes: ${res.metrics.deleted_count}\n` +
      `• DLQ Rejects: ${res.metrics.quarantined_count}\n` +
      `• Duplicates: ${res.metrics.duplicate_count}\n` +
      `• Mathematical Discrepancy: ${res.metrics.discrepancy}`
    );
  }
}

// Detail Drawer
function inspectRecord(title, subtitle, payload) {
  document.getElementById('drawer-title').innerText = title;
  document.getElementById('drawer-subtitle').innerText = subtitle;
  
  let formatted = payload;
  try {
    const parsed = typeof payload === 'string' ? JSON.parse(payload) : payload;
    formatted = JSON.stringify(parsed, null, 2);
  } catch (e) {}

  document.getElementById('drawer-json').innerText = formatted;
  document.getElementById('payload-drawer').style.display = 'flex';
}

function closeDrawer(event) {
  document.getElementById('payload-drawer').style.display = 'none';
}

function copyDrawerJSON() {
  const text = document.getElementById('drawer-json').innerText;
  navigator.clipboard.writeText(text);
  alert("JSON Payload copied to clipboard!");
}

function getStatusTag(status) {
  if (status === 'SUCCESS') return `<span class="status-tag status-success">SUCCESS</span>`;
  if (status === 'FAILED') return `<span class="status-tag status-danger">FAILED</span>`;
  if (status.includes('SKIPPED')) return `<span class="status-tag status-warning">SKIPPED</span>`;
  return `<span class="status-tag status-info">${status}</span>`;
}

// Initialize on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  loadAllData().then(() => {
    navigate('overview');
  });
});
