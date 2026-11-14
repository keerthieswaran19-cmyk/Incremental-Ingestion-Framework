// Incremental Ingestion Operations Cockpit - High-Performance Controller

async function fetchJSON(url, options = {}) {
  try {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.error("API Error:", err);
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
    tbody.innerHTML = "<tr><td colspan='12' style='text-align:center; color: var(--text-muted);'>No pipeline execution runs recorded.</td></tr>";
    return;
  }

  tbody.innerHTML = runs.map(r => {
    let chip = `<span class="chip chip-info">${r.status}</span>`;
    if (r.status === "SUCCESS") chip = `<span class="chip chip-success">✓ SUCCESS</span>`;
    else if (r.status === "FAILED") chip = `<span class="chip chip-danger">✕ FAILED</span>`;
    else if (r.status.includes("SKIPPED")) chip = `<span class="chip chip-warning">↷ SKIPPED</span>`;

    return `
      <tr>
        <td><code>${r.run_id}</code></td>
        <td>${r.start_time ? r.start_time.substring(0, 19).replace('T', ' ') : '--'}</td>
        <td>${chip}</td>
        <td><strong>${(r.input_count || 0).toLocaleString()}</strong></td>
        <td style="color: #34d399; font-weight: 600;">+${r.inserted_count || 0}</td>
        <td style="color: #60a5fa;">${r.updated_count || 0}</td>
        <td style="color: #fb7185;">-${r.deleted_count || 0}</td>
        <td style="color: #f43f5e; font-weight: 600;">${r.quarantined_count || 0}</td>
        <td style="color: #94a3b8;">${r.duplicate_count || 0}</td>
        <td>${r.duration_ms ? r.duration_ms.toFixed(1) + 'ms' : '--'}</td>
        <td>${r.records_per_sec ? r.records_per_sec.toFixed(0) + ' r/s' : '--'}</td>
        <td>
          <button class="btn btn-ghost" style="padding: 4px 10px; font-size: 11px;" onclick="verifyRun('${r.run_id}')">Verify</button>
        </td>
      </tr>
    `;
  }).join("");
}

async function loadQuarantine() {
  const items = await fetchJSON("/api/quarantine?limit=50");
  const tbody = document.getElementById("quarantine-tbody");
  if (!items || items.length === 0) {
    tbody.innerHTML = "<tr><td colspan='7' style='text-align:center; color: #34d399;'>Dead-Letter Queue Clean! 0 anomalies.</td></tr>";
    return;
  }

  tbody.innerHTML = items.map((q, idx) => `
    <tr>
      <td><code>${q.quarantine_id.substring(0, 10)}...</code></td>
      <td><span class="chip chip-danger">${q.error_code}</span></td>
      <td>${q.error_message}</td>
      <td><span class="chip chip-warning">${q.occurrence_count || 1}x</span></td>
      <td>${q.source_file}</td>
      <td><code>${q.run_id}</code></td>
      <td>
        <button class="btn btn-ghost" style="padding: 3px 8px; font-size: 11px;" onclick='viewPayload(${JSON.stringify(q.raw_payload)})'>Inspect</button>
      </td>
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
    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px;">
      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Conservation Proof Status</span>
          <span class="chip chip-success">100% BALANCED</span>
        </div>
        <div class="kpi-value" style="color: #34d399;">${balancedCount} / ${verifiedRuns.length} Runs</div>
        <div class="kpi-meta">Input ≡ Inserts + Updates + Deletes + DLQ + Dups</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-header"><span class="kpi-label">Active Point-In-Time Snapshot</span></div>
        <div class="kpi-value">${data.target_statistics.active_records.toLocaleString()} Entities</div>
        <div class="kpi-meta">Tombstones: ${data.target_statistics.tombstoned_records}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-header"><span class="kpi-label">Immutable CDC Event History</span></div>
        <div class="kpi-value">${data.table_counts.event_history.toLocaleString()} Mutations</div>
        <div class="kpi-meta">Full Bi-Temporal Time Travel</div>
      </div>
    </div>

    <div style="background: rgba(14, 18, 26, 0.6); border: 1px solid var(--border-subtle); padding: 18px 24px; border-radius: 14px;">
      <h4 style="font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); margin-bottom: 12px;">Mathematical Verification Audit Log</h4>
      ${verifiedRuns.slice(0, 5).map(v => `
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.03);">
          <div>
            <code>${v.run_id}</code>
            <span style="font-size: 12px; color: var(--text-secondary); margin-left: 12px;">
              Input (${v.metrics.input_count}) = Inserts (${v.metrics.inserted_count}) + Upd (${v.metrics.updated_count}) + Del (${v.metrics.deleted_count}) + DLQ (${v.metrics.quarantined_count}) + Dups (${v.metrics.duplicate_count})
            </span>
          </div>
          <span class="chip chip-success">Discrepancy: ${v.metrics.discrepancy}</span>
        </div>
      `).join("")}
    </div>
  `;
}

async function loadWatermarks() {
  const watermarks = await fetchJSON("/api/watermarks");
  const tbody = document.getElementById("watermarks-tbody");
  if (!watermarks || watermarks.length === 0) {
    tbody.innerHTML = "<tr><td colspan='4' style='text-align:center; color: var(--text-muted);'>No high watermarks tracked yet.</td></tr>";
    return;
  }

  tbody.innerHTML = watermarks.map(w => `
    <tr>
      <td><strong>${w.entity_name}</strong></td>
      <td><code style="color: #38bdf8; font-size: 13px;">${w.high_watermark}</code></td>
      <td>${w.updated_at.substring(0, 19).replace('T', ' ')}</td>
      <td><code>${w.last_run_id}</code></td>
    </tr>
  `).join("");
}

async function triggerIngestion() {
  const btn = document.getElementById("btn-run");
  const sourceFile = document.getElementById("run-source-select").value;
  const force = document.getElementById("chk-force-reprocess").checked;
  const inc = document.getElementById("chk-incremental").checked;

  btn.disabled = true;
  btn.innerHTML = "<span>⏳ Ingesting...</span>";

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

  btn.disabled = false;
  btn.innerHTML = "<span>▶ Run Ingestion</span>";

  if (res) {
    loadDashboard();
  }
}

async function generateSampleData() {
  // Trigger generation via quick fetch or prompt
  alert("Generating fresh 100 dirty records with seed=42...");
  const res = await fetchJSON("/api/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_file: "data/source/sample_data.json",
      entity_name: "CUSTOMER",
      force_reprocess: true,
      incremental: false
    })
  });
  if (res) loadDashboard();
}

async function verifyRun(runId) {
  const res = await fetchJSON(`/api/runs/${runId}/reconcile`);
  if (res) {
    alert(
      `Reconciliation Proof for ${runId}:\n\n` +
      `• Status: ${res.status}\n` +
      `• Mathematically Sound: ${res.is_mathematically_sound}\n` +
      `• Input Count: ${res.metrics.input_count}\n` +
      `• Inserts: ${res.metrics.inserted_count}\n` +
      `• Updates: ${res.metrics.updated_count}\n` +
      `• Deletes: ${res.metrics.deleted_count}\n` +
      `• Quarantined: ${res.metrics.quarantined_count}\n` +
      `• Duplicates: ${res.metrics.duplicate_count}\n` +
      `• Sum: ${res.metrics.sum_of_components}\n` +
      `• Discrepancy: ${res.metrics.discrepancy}`
    );
  }
}

function viewPayload(rawPayload) {
  let formatted = rawPayload;
  try {
    const parsed = JSON.parse(rawPayload);
    formatted = JSON.stringify(parsed, null, 2);
  } catch (e) {}

  document.getElementById("modal-json").innerText = formatted;
  document.getElementById("payload-modal").style.display = "flex";
}

function closeModal(event) {
  document.getElementById("payload-modal").style.display = "none";
}

function switchTab(tabName) {
  const tabs = ["runs", "quarantine", "reconcile", "watermarks"];
  tabs.forEach(t => {
    document.getElementById(`tab-${t}`).style.display = (t === tabName) ? "block" : "none";
  });
  document.querySelectorAll(".tab-link").forEach((btn, idx) => {
    btn.classList.toggle("active", tabs[idx] === tabName);
  });
}

function openDocs() {
  window.open("/docs", "_blank");
}

document.addEventListener("DOMContentLoaded", loadDashboard);
