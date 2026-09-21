const byId = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="inspector-token"]').content;
const terminal = new Set(["completed", "blocked", "needs_verification", "max_steps", "timeout", "stuck", "error"]);
let state = null;
let busy = false;
let automatic = false;
let timerBase = 0;
let timerSyncedAt = performance.now();

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);
const percent = (value) => value == null ? "--" : `${(Number(value) * 100).toFixed(value < .01 ? 1 : 0)}%`;
const basename = (value) => String(value || "local model").split("/").filter(Boolean).at(-1);
const formatTimer = (milliseconds) => {
  const total = Math.max(0, Math.floor(milliseconds));
  const minutes = Math.floor(total / 60000);
  const seconds = Math.floor(total % 60000 / 1000);
  const millis = total % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
};

function syncTimer() {
  timerBase = state?.elapsed_ms || 0;
  timerSyncedAt = performance.now();
}

function drawTimer() {
  const running = state?.started_at_epoch_ms && !terminal.has(state.status);
  const elapsed = timerBase + (running ? performance.now() - timerSyncedAt : 0);
  byId("timer").textContent = formatTimer(elapsed);
  requestAnimationFrame(drawTimer);
}
requestAnimationFrame(drawTimer);

async function call(name, body = {}) {
  const response = await fetch(`/api/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Inspector-Token": token },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Inspector request failed");
  state = data;
  syncTimer();
  render();
  return data;
}

function setControls() {
  const active = state?.page && !terminal.has(state.status);
  byId("start").disabled = busy;
  for (const id of ["goal", "start-url", "input-value", "input-name", "allowed-domains", "success-text", "success-prefix", "success-regex"]) byId(id).disabled = busy;
  byId("choose").disabled = busy || !active || state?.status === "predicted";
  byId("execute").disabled = busy || state?.status !== "predicted";
  byId("auto").disabled = busy || !active;
  byId("auto").hidden = automatic;
  byId("stop").hidden = !automatic;
  byId("download").disabled = !state?.history?.length;
  byId("loading").hidden = !busy;
}

async function perform(operation, label) {
  if (busy) return;
  busy = true;
  byId("error").hidden = true;
  byId("status").textContent = label;
  setControls();
  try {
    await operation();
  } catch (error) {
    automatic = false;
    try {
      const response = await fetch("/api/state");
      if (response.ok) state = await response.json();
    } catch {
      // Preserve the command error if the inspector itself is unavailable.
    }
    syncTimer();
    render();
    byId("error").textContent = error.message;
    byId("error").hidden = false;
    byId("status").textContent = "Paused - needs attention";
  } finally {
    busy = false;
    setControls();
  }
}

function actionProbability(actionId) {
  return state?.decision?.probabilities?.[actionId] ?? null;
}

function render() {
  if (!state) return;
  byId("model-tag").textContent = `${basename(state.model)} / MLX / zero network model calls`;
  byId("network-calls").textContent = state.network_model_calls ?? 0;
  const labels = {
    idle: "Ready for a task",
    ready: "Page observed - ready for a decision",
    predicted: "Choice ready - inspect or execute",
    completed: "Task completed and verified",
    blocked: "Stopped - no supported action",
    needs_verification: "Stopped - outcome needs verification",
    max_steps: "Stopped - action budget reached",
    timeout: "Stopped - time budget reached",
    stuck: "Stopped - no observable progress",
    error: "Stopped - runtime error",
  };
  byId("status").textContent = labels[state.status] || state.reason || state.status;
  byId("status-dot").className = `status-dot ${terminal.has(state.status) ? (state.status === "completed" ? "" : "error") : state.page ? "active" : ""}`;
  const page = state.page;
  if (!page) {
    byId("empty").hidden = false;
    byId("screenshot").hidden = true;
    setControls();
    return;
  }
  byId("empty").hidden = true;
  byId("screenshot").hidden = !page.screenshot;
  if (page.screenshot) byId("screenshot").src = `data:image/jpeg;base64,${page.screenshot}`;
  byId("url").textContent = page.url;
  byId("page-title").textContent = page.title;
  byId("action-count").textContent = `${state.actions.length} actions`;

  const selectedAction = state.policy?.executed_action || state.decision?.proposed_action;
  const chosen = state.actions.find((action) => action.action_id === selectedAction);
  byId("choice-title").textContent = chosen?.description || (terminal.has(state.status) ? state.reason : "Choose the next action");
  byId("latency").textContent = state.decision ? `${state.decision.inference_ms.toFixed(1)} ms` : "--";
  byId("confidence").textContent = state.decision ? percent(state.decision.confidence) : "--";
  byId("operation").textContent = state.decision?.operation || "--";
  byId("ranking-note").textContent = state.decision ? "Ranked by Laya" : "Unranked";

  const operations = Object.entries(state.decision?.operation_probabilities || {}).sort((a, b) => b[1] - a[1]);
  byId("operations").innerHTML = operations.map(([name, probability]) => `<span class="operation-chip ${name === state.decision.operation ? "best" : ""}">${escapeHtml(name)} <b>${percent(probability)}</b></span>`).join("");

  const actions = [...state.actions];
  if (state.decision) actions.sort((a, b) => (actionProbability(b.action_id) ?? -1) - (actionProbability(a.action_id) ?? -1));
  byId("choices").innerHTML = actions.length ? actions.map((action, index) => {
    const probability = actionProbability(action.action_id);
    return `<div class="choice ${action.action_id === selectedAction ? "best" : ""}" data-element="${escapeHtml(action.element_id || "")}">
      <span class="choice-id">${String(index + 1).padStart(2, "0")}</span>
      <div class="choice-label">${escapeHtml(action.description)}<small>${escapeHtml(action.kind)}${action.goal_match ? " / planner progress" : ""}</small>${probability != null ? `<div class="bar" style="--probability:${probability * 100}%"></div>` : ""}</div>
      <span class="probability">${percent(probability)}</span>
    </div>`;
  }).join("") : '<p class="muted">No actions remain for this run.</p>';

  const elementIds = new Set(state.actions.map((action) => action.element_id).filter(Boolean));
  const overlayElements = page.elements.filter((element) => elementIds.has(element.element_id));
  const selectedElement = chosen?.element_id;
  byId("targets").innerHTML = overlayElements.map((element, index) => `<div class="target ${element.element_id === selectedElement ? "selected" : ""}" data-element="${escapeHtml(element.element_id)}" style="left:${100 * element.x / page.width}%;top:${100 * element.y / page.height}%;width:${100 * element.width / page.width}%;height:${100 * element.height / page.height}%"><span>${index + 1}</span></div>`).join("");
  byId("targets").hidden = !byId("overlays").checked;

  byId("history").innerHTML = state.history.length ? state.history.map((row) => `<div class="trace-row">
    <span class="number">${String(row.step).padStart(2, "0")}</span>
    <div>${escapeHtml(row.description)}</div>
    <span class="time">${row.inference_ms.toFixed(1)} ms / ${percent(row.confidence)}</span>
    <span class="effect ${row.action_error ? "error" : ""}">${row.action_error ? escapeHtml(row.action_error) : row.changed ? "Page changed" : "No change"}</span>
  </div>`).join("") : '<p class="muted">Executed actions will appear here.</p>';
  byId("step-count").textContent = `${state.history.length} actions / ${formatTimer(state.elapsed_ms)}`;
  byId("model-state").textContent = JSON.stringify({
    goal: state.goal,
    page: { url: page.url, title: page.title, text: page.visible_text },
    actions: state.actions,
    decision: state.decision,
    policy: state.policy,
  }, null, 2);
  setControls();
}

byId("task-form").addEventListener("submit", (event) => {
  event.preventDefault();
  automatic = false;
  const domains = byId("allowed-domains").value.split(",").map((value) => value.trim()).filter(Boolean);
  perform(() => call("reset", {
    goal: byId("goal").value,
    url: byId("start-url").value,
    input_name: byId("input-name").value,
    input_value: byId("input-value").value,
    allowed_domains: domains,
    success_text: byId("success-text").value,
    success_url_prefix: byId("success-prefix").value,
    success_url_regex: byId("success-regex").value,
    max_steps: 24,
    max_seconds: 180,
    max_candidates: 20,
  }), "Opening an isolated browser session");
});

byId("choose").addEventListener("click", () => perform(() => call("predict"), "Laya is comparing the observed actions"));
byId("execute").addEventListener("click", () => perform(() => call("act", { fingerprint: state.page.fingerprint }), "Executing the guarded choice"));
byId("auto").addEventListener("click", () => perform(async () => {
  automatic = true;
  setControls();
  for (let index = 0; index < state.max_steps * 2 && automatic; index += 1) {
    byId("status").textContent = "Running the browser";
    await call("tick");
    if (terminal.has(state.status)) break;
    if (byId("slow").checked) await new Promise((resolve) => setTimeout(resolve, 500));
  }
  automatic = false;
}, "Running the browser"));
byId("stop").addEventListener("click", () => {
  automatic = false;
  byId("status").textContent = "Pausing after the current command";
  setControls();
});
byId("overlays").addEventListener("change", () => { byId("targets").hidden = !byId("overlays").checked; });
byId("choices").addEventListener("pointerover", (event) => {
  const elementId = event.target.closest("[data-element]")?.dataset.element;
  if (!elementId) return;
  document.querySelectorAll(".target").forEach((target) => target.classList.toggle("selected", target.dataset.element === elementId));
});
byId("choices").addEventListener("pointerleave", () => render());
byId("download").addEventListener("click", () => {
  const trace = { ...state, page: state.page ? { ...state.page, screenshot: undefined } : null };
  const blob = new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "laya-browser-trace.json";
  anchor.click();
  URL.revokeObjectURL(url);
});

fetch("/api/state").then((response) => response.json()).then((value) => {
  state = value;
  syncTimer();
  render();
}).catch(() => { byId("status").textContent = "Cannot reach the local inspector"; });
