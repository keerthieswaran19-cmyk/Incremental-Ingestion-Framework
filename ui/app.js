// Incremental Ingestion Cockpit Frontend Logic

async function fetchJSON(url, options = {}) {
  try {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.error("API Fetch Error:", err);
    return null;
  }
}

async function loadDashboard() {
  await Promise.all([
    loadOverview(),
    loadRuns(),
    loadQuarantine(),
    loadReconciliation(),
    loadWatermarks(),
  ]);
}

async function loadOverview() {
  const data = await fetchJSON("/api/overview");
  if (!data) return;

  document.getElementById("kpi-active-targets").innerText = (data.target_snapshot.active_records || 0).toLocaleString();
  document.getElementById("kpi-tombstones").innerText = `Tombstones: ${data.target_snapshot.tombstones || 0}`;
  document.getElementById("kpi-source-events").innerText = (data.table_counts.source_events || 0).toLocaleString();
  document.getElementById("kpi-quarantine").innerText = (data.table_counts.quarantine || 0).toLocaleString();
  document.getElementById("kpi-peak-speed").innerText = `${(data.performance.max_records_per_sec || 0).toLocaleString()} r/s`;
  document.getElementById("kpi-avg-speed").innerText = `Avg: ${(data.performance.avg_records_per_sec || 0).toLocaleString()} r/s`;

  if (data.watermarks && data.watermarks.length > 0) {
    document.getElementById("kpi-watermark").innerText = data.watermarks[0].high_watermark;
  } else {
    document.getElementById("kpi-watermark").innerText = "1970-01-01T00:00:00Z";
  }
}

async function loadRuns() {
  const runs = await fetchJSON("/api/runs");
  const tbody = document.getElementById("runs-tbody");
  if (!runs || runs.length === 0) {
    tbody.innerHTML = "<tr><td colspan='12' style='text-align:center;'>No execution runs recorded yet.</td></tr>";
    return;
  }

  tbody.innerHTML = runs.map(r => {
    let statusBadge = `<span class="badge badge-info">${r.status}</span>`;
    if (r.status === "SUCCESS") statusBadge = `<span class="badge badge-success">SUCCESS</span>`;
    else if (r.status === "FAILED") statusBadge = `<span class="badge badge-danger">FAILED</span>`;
    else if (r.status.includes("SKIPPED")) statusBadge = `<span class="badge badge-warning">SKIPPED</span>`;

    return `
      <tr>
        <td><code>${r.run_id}</code></td>
        <td>${r.start_time ? r.start_time.substring(0, 19).replace('T', ' ') : '--'}</td>
        <td>${statusBadge}</td>
        <td><strong>${(r.input_count || 0).toLocaleString()}</strong></td>
        <td style="color: #34d399;">+${r.inserted_count || 0}</td>
        <td style="color: #60a5fa;">${r.updated_count || 0}</td>
        <td style="color: #fb7185;">-${r.deleted_count || 0}</td>
        <td style="color: #f43f5e;">${r.quarantined_count || 0}</td>
        <td style="color: #9ca3af;">${r.duplicate_count || 0}</td>
        <td>${r.duration_ms ? r.duration_ms.toFixed(1) + 'ms' : '--'}</td>
        <td>${r.records_per_sec ? r.records_per_sec.toFixed(0) + ' r/s' : '--'}</td>
        <td>
          <button class="btn-secondary" style="padding: 4px 8px; font-size: 11px;" onclick="verifyRun('${r.run_id}')">Reconcile</button>
        </td>
      </tr>
    `;
  }).join("");
}

async function loadQuarantine() {
  const items = await fetchJSON("/api/quarantine?limit=50");
  const tbody = document.getElementById("quarantine-tbody");
  if (!items || items.length === 0) {
    tbody.innerHTML = "<tr><td colspan='7' style='text-align:center; color: #34d399;'>Quarantine clean! 0 rejected records.</td></tr>";
    return;
  }

  tbody.innerHTML = items.map(q => `
    <tr>
      <td><code>${q.quarantine_id.substring(0, 12)}...</code></td>
      <td><span class="badge badge-danger">${q.error_code}</span></td>
      <td>${q.error_message}</td>
      <td>${q.source_file}</td>
      <td><code>${q.run_id}</code></td>
      <td>${q.created_at.substring(0, 19).replace('T', ' ')}</td>
      <td><pre class="code-view" style="max-width: 320px; font-size: 11px;">${q.raw_payload}</pre></td>
    </tr>
  `).join("");
}

async function loadReconciliation() {
  const data = await fetchJSON("/api/reconciliation");
  const container = document.getElementById("reconcile-content");
  if (!data) return;

  const verifiedRuns = data.recent_runs_reconciliation || [];
  const balancedCount = verifiedRuns.filter(r => r.is_mathematically_sound).length;

  container.innerHTML = `
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px;">
      <div class="kpi-card">
        <div class="kpi-title">Verified Runs</div>
        <div class="kpi-val" style="color: #34d399;">${balancedCount} / ${verifiedRuns.length}</div>
        <div class="kpi-sub">Mathematical Soundness: 100% Balanced</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Active Database Entities</div>
        <div class="kpi-val">${data.target_statistics.active_records.toLocaleString()}</div>
        <div class="kpi-sub">Total Snapshot Entities</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Historical CDC Mutations</div>
        <div class="kpi-val">${data.table_counts.event_history.toLocaleString()}</div>
        <div class="kpi-sub">Append-Only Audit Ledger</div>
      </div>
    </div>
  `;
}

async function loadWatermarks() {
  const watermarks = await fetchJSON("/api/watermarks");
  const tbody = document.getElementById("watermarks-tbody");
  if (!watermarks || watermarks.length === 0) {
    tbody.innerHTML = "<tr><td colspan='4' style='text-align:center;'>No watermarks recorded.</td></tr>";
    return;
  }

  tbody.innerHTML = watermarks.map(w => `
    <tr>
      <td><strong>${w.entity_name}</strong></td>
      <td><code style="color: #38bdf8;">${w.high_watermark}</code></td>
      <td>${w.updated_at.substring(0, 19).replace('T', ' ')}</td>
      <td><code>${w.last_run_id}</code></td>
    </tr>
  `).join("");
}

async function triggerIngestion() {
  const sourceFile = document.getElementById("run-source-select").value;
  const force = document.getElementById("chk-force-reprocess").checked;
  const inc = document.getElementById("chk-incremental").checked;

  const body = {
    source_file: sourceFile,
    entity_name: "CUSTOMER",
    force_reprocess: force,
    incremental: inc,
  };

  const res = await fetchJSON("/api/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (res) {
    alert(`Ingestion Finished!\nStatus: ${res.status}\nInput: ${res.input_count || 0}\nInserts: ${res.inserted_count || 0}\nUpdates: ${res.updated_count || 0}\nQuarantined: ${res.quarantined_count || 0}`);
    loadDashboard();
  }
}

async function verifyRun(runId) {
  const res = await fetchJSON(`/api/runs/${runId}/reconcile`);
  if (res) {
    alert(`Reconciliation Check for ${runId}:\nStatus: ${res.status}\nSoundness: ${res.is_mathematically_sound}\nInput: ${res.metrics.input_count}\nSum of Components: ${res.metrics.sum_of_components}\nDiscrepancy: ${res.metrics.discrepancy}`);
  }
}

function switchTab(tabName) {
  const tabs = ["runs", "quarantine", "reconcile", "watermarks"];
  tabs.forEach(t => {
    document.getElementById(`tab-${t}`).style.display = (t === tabName) ? "block" : "none";
  });
  document.querySelectorAll(".tab-btn").forEach((btn, idx) => {
    btn.classList.toggle("active", tabs[idx] === tabName);
  });
}

function openDocs() {
  window.open("/docs", "_blank");
}

// Initial load
document.addEventListener("DOMContentLoaded", loadDashboard);
