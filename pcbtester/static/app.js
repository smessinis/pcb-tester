const $ = (id) => document.getElementById(id);

let projectId = null;
let jobId = null;
let filter = "all";

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

async function loadHealth() {
  try {
    const health = await (await fetch("/api/health")).json();
    const kicad = health.kicad ? `KiCad ${health.kicad_version}` : "kicad-cli missing";
    const spice = health.ngspice ? "ngspice" : "ngspice missing";
    $("health").textContent = `${kicad} · ${spice}`;
  } catch (err) {
    $("health").textContent = "API not reachable";
  }
}

function parseStimuli(text) {
  const stimuli = {};
  if (!text.trim()) return stimuli;
  for (const part of text.split(",")) {
    const piece = part.trim();
    if (!piece) continue;
    const eq = piece.indexOf("=");
    if (eq < 1) throw new Error(`Stimulus must look like VBAT=16.8 (got ${piece})`);
    stimuli[piece.slice(0, eq).trim()] = Number(piece.slice(eq + 1));
    if (Number.isNaN(stimuli[piece.slice(0, eq).trim()])) {
      throw new Error(`Not a voltage: ${piece}`);
    }
  }
  return stimuli;
}

async function upload(file) {
  $("upload-status").textContent = "Reading the project and exporting a netlist…";
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/projects", { method: "POST", body });
  const payload = await response.json();
  if (!response.ok) {
    $("upload-status").textContent = payload.detail || "Upload failed";
    return;
  }
  projectId = payload.project_id;
  $("upload-status").textContent = "";
  $("setup").classList.remove("hidden");
  $("project-name").textContent = payload.name;
  const bits = [payload.schematic, payload.pcb].filter(Boolean).join(" · ");
  $("project-meta").textContent = bits + (payload.netlist_error ? ` — netlist: ${payload.netlist_error}` : "");
  const mcus = payload.mcus || [];
  $("mcu-line").textContent = mcus.length
    ? mcus.map((mcu) => `${mcu.ref} ${mcu.value || ""} ${mcu.database ? "→ " + mcu.database : "(no pin database)"}`).join(" · ")
    : "No STM32 with a pin database was recognised yet.";
  $("checks").innerHTML = (payload.checks || []).map((check) => `
    <label class="check-row">
      <input type="checkbox" value="${esc(check.id)}" ${check.default ? "checked" : ""}>
      <span><strong>${esc(check.title)}</strong><span>${esc(check.description)}</span></span>
    </label>`).join("");
  const spice = payload.spice || [];
  $("spice").innerHTML = spice.length ? spice.map((item) => `
    <label class="spice-row">
      <input type="checkbox" value="${esc(item.id)}" ${item.simulatable ? "checked" : ""}>
      <span><strong>${esc(item.title)}</strong><span>${esc(item.reason)}</span></span>
    </label>`).join("") : "<p class='hint'>None recognised. The SPICE check will say so.</p>";
}

async function run() {
  const checks = [...document.querySelectorAll("#checks input:checked")].map((el) => el.value);
  const spiceIds = [...document.querySelectorAll("#spice input:checked")].map((el) => el.value);
  let stimuli = {};
  try {
    stimuli = parseStimuli($("stimuli").value);
  } catch (err) {
    $("upload-status").textContent = err.message;
    return;
  }
  $("run").disabled = true;
  $("progress").classList.remove("hidden");
  $("report").classList.add("hidden");
  $("steps").innerHTML = "";
  const response = await fetch(`/api/projects/${projectId}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ checks, spice_ids: spiceIds, stimuli }),
  });
  const payload = await response.json();
  if (!response.ok) {
    $("run").disabled = false;
    $("upload-status").textContent = payload.detail || "Could not start";
    return;
  }
  jobId = payload.job_id;
  poll();
}

async function poll() {
  const response = await fetch(`/api/jobs/${jobId}`);
  const job = await response.json();
  $("steps").innerHTML = (job.steps || []).map((step) =>
    `<li><span class="tag ${esc(step.status)}">${esc(step.status)}</span> ${esc(step.message)}</li>`
  ).join("");
  if (job.status === "running") {
    setTimeout(poll, 700);
    return;
  }
  $("run").disabled = false;
  if (job.status === "error") {
    $("upload-status").textContent = job.error || "Job failed";
    return;
  }
  showReport(job.report);
}

function showReport(report) {
  $("report").classList.remove("hidden");
  $("export-html").href = `/api/jobs/${jobId}/report.html`;
  $("export-pdf").href = `/api/jobs/${jobId}/report.pdf`;
  const counts = report.counts || {};
  $("summary").innerHTML = `
    <p class="tag ${esc(report.status)}">${esc(report.status)}</p>
    <div class="counts">
      <span class="count">${counts.fail || 0} fail</span>
      <span class="count">${counts.warn || 0} warn</span>
      <span class="count">${counts.pass || 0} pass</span>
      <span class="count">${counts.info || 0} info</span>
    </div>
    <div class="filter">
      ${["all", "fail", "warn", "pass", "info"].map((name) =>
        `<button type="button" data-filter="${name}" class="${filter === name ? "active" : ""}">${name}</button>`
      ).join("")}
    </div>`;
  $("summary").querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      filter = button.dataset.filter;
      showReport(report);
    });
  });
  $("rails").innerHTML = renderRails(report);
  $("results").innerHTML = (report.checks || []).map(renderCheck).join("");
}

function renderRails(report) {
  const power = (report.checks || []).map((check) => check.artifacts && check.artifacts.power).find(Boolean);
  if (!power || !(power.rails || []).length) return "";
  const regs = (power.regulators || []).map((reg) => {
    const volts = reg.expected_voltage == null ? "" : ` → ${reg.expected_voltage} V`;
    return `<li><code>${esc(reg.ref)}</code> ${esc(reg.value)} ${esc(reg.input_net || "?")} to ${esc(reg.output_net || "?")}${esc(volts)}</li>`;
  }).join("");
  const rows = power.rails.map((rail) => {
    const volts = rail.voltage == null ? "—" : `${rail.voltage} V`;
    return `<tr>
      <td><code>${esc(rail.net)}</code></td>
      <td>${esc(volts)}</td>
      <td class="${rail.sourced ? "ok" : "bad"}">${rail.sourced ? "yes" : "no"}</td>
      <td>${esc((rail.sources || []).join(", ") || "none")}</td>
      <td>${(rail.loads || []).length}</td>
      <td>${esc((rail.local_caps || []).slice(0, 6).join(", ") || "—")}</td>
    </tr>`;
  }).join("");
  return `<h3>Power rails</h3>${regs ? `<ul>${regs}</ul>` : ""}
    <table><thead><tr><th>Net</th><th>Voltage</th><th>Sourced</th><th>Source</th><th>Loads</th><th>Local caps</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function renderCheck(check) {
  const findings = (check.findings || []).filter((finding) => filter === "all" || finding.severity === filter);
  const items = findings.map((finding) => {
    const chips = []
      .concat(finding.refs || [], finding.nets || [], finding.pins || [])
      .map((chip) => `<code>${esc(chip)}</code>`).join(" ");
    const details = finding.details ? `<pre>${esc(finding.details)}</pre>` : "";
    return `<details>
      <summary><span class="tag ${esc(finding.severity)}">${esc(finding.severity)}</span> ${esc(finding.message)}</summary>
      <div class="chips">${chips}</div>${details}
    </details>`;
  }).join("");
  const pinmux = ((check.artifacts || {}).pinmux || []);
  let mux = "";
  if (pinmux.length && (filter === "all" || filter === "fail")) {
    const rows = pinmux.filter((row) => filter !== "fail" || !row.ok).map((row) => `<tr>
      <td class="${row.ok ? "ok" : "bad"}">${row.ok ? "ok" : "no"}</td>
      <td><code>${esc(row.pin_name)}</code></td>
      <td>${esc(row.pin)}</td>
      <td><code>${esc(row.net)}</code></td>
      <td>${esc(row.function)}</td>
      <td>${row.af == null ? "—" : esc(row.af)}</td>
    </tr>`).join("");
    if (rows) {
      mux = `<h3>Constrained pins</h3><table><thead><tr><th></th><th>Pin</th><th>#</th><th>Net</th><th>Function</th><th>AF</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
  }
  const repl = (check.artifacts || {}).repl;
  const replHtml = repl ? `<h3>Renode skeleton</h3><pre>${esc(repl)}</pre>` : "";
  return `<section>
    <h3><span class="tag ${esc(check.status)}">${esc(check.status)}</span> ${esc(check.title)}</h3>
    <p class="hint">${esc(check.summary)} · ${check.duration_ms || 0} ms</p>
    ${items || "<p class='hint'>Nothing in this filter.</p>"}
    ${mux}
    ${replHtml}
  </section>`;
}

$("file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) upload(file);
});

const drop = $("drop");
drop.addEventListener("dragover", (event) => { event.preventDefault(); });
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  const file = event.dataTransfer.files[0];
  if (file) upload(file);
});
$("run").addEventListener("click", run);
loadHealth();
