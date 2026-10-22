const API_URL = window.AUDITOR_API_URL || "http://localhost:8000";
const filesInput = document.querySelector("#files"), selected = document.querySelector("#selected-files"), message = document.querySelector("#message");
let report;

function showFiles(files) { selected.innerHTML = [...files].map(f => `<li>${escapeHtml(f.name)} (${Math.ceil(f.size / 1024)} KB)</li>`).join(""); }
function escapeHtml(value) { const div = document.createElement("div"); div.textContent = value; return div.innerHTML; }
function setMessage(text, error = false) { message.textContent = text; message.className = error ? "error" : ""; }
function render() {
  document.querySelector("#results").hidden = false;
  const findings = report.findings || [], counts = ["files_analyzed", "unsupported_operations_count"].map(k => [k.replaceAll("_", " "), report[k] || 0]);
  counts.unshift(["findings", findings.length], ["status", report.status]);
  document.querySelector("#summary").innerHTML = counts.map(([k,v]) => `<div class="stat"><strong>${escapeHtml(String(v))}</strong>${escapeHtml(k)}</div>`).join("");
  document.querySelector("#errors").innerHTML = (report.errors || []).map(e => `<p class="errors">${escapeHtml(e)}</p>`).join("");
  const rules = [...new Set(findings.map(f => f.rule_id))]; document.querySelector("#rule").innerHTML = '<option value="">All rules</option>' + rules.map(r => `<option>${escapeHtml(r)}</option>`).join(""); renderFindings();
}
function renderFindings() { const severity = document.querySelector("#severity").value, rule = document.querySelector("#rule").value; const shown = report.findings.filter(f => (!severity || f.severity === severity) && (!rule || f.rule_id === rule)); document.querySelector("#findings").innerHTML = shown.length ? shown.map(f => `<article class="finding ${f.severity}"><h3>${escapeHtml(f.rule_id)} · ${escapeHtml(f.title)}</h3><p class="meta">${escapeHtml(f.severity)} severity · ${escapeHtml(f.location?.file || "unknown file")} ${escapeHtml(f.location?.operation || "")}</p><p>${escapeHtml(f.explanation)}</p><p><strong>Risk evidence:</strong> ${escapeHtml(f.evidence.join("; "))}</p><p><strong>Remediation:</strong> ${escapeHtml(f.remediation)}</p></article>`).join("") : "<p>No findings match the selected filters.</p>"; }
filesInput.addEventListener("change", () => showFiles(filesInput.files));
document.querySelector("#analyze").addEventListener("click", async () => { if (!filesInput.files.length) return setMessage("Choose at least one supported file.", true); const form = new FormData(); [...filesInput.files].forEach(f => form.append("files", f)); setMessage("Analyzing uploaded manifests…"); try { const response = await fetch(`${API_URL}/api/audits`, {method:"POST", body:form}); report = await response.json(); if (!response.ok) throw new Error(report.detail || (report.errors || ["Analysis failed"])[0]); render(); setMessage(`Audit completed: ${report.findings.length} finding(s).`); } catch (err) { setMessage(err.message || "Could not reach the API.", true); } });
document.querySelector("#sample").addEventListener("click", () => { const sample = {version:1,collection:"users",operations:[{op:"create_index",keys:{email:1},unique:true,build_context:"high_write"},{op:"backfill",field:"status",batched:false,checkpointed:false,throttled:false}]}; const file = new File([JSON.stringify(sample, null, 2)], "sample-risks.migration.json", {type:"application/json"}); const transfer = new DataTransfer(); transfer.items.add(file); filesInput.files = transfer.files; showFiles(filesInput.files); setMessage("Sample loaded. Run audit to analyze it with the backend."); });
document.querySelector("#severity").addEventListener("change", renderFindings); document.querySelector("#rule").addEventListener("change", renderFindings);
document.querySelector("#download").addEventListener("click", () => { if (!report) return; const link=document.createElement("a"); link.href=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json"})); link.download="migration-audit-report.json"; link.click(); URL.revokeObjectURL(link.href); });
