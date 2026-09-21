const byId = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="inspector-token"]').content;
const terminal = new Set(["completed", "blocked", "needs_verification", "needs_login", "max_steps", "timeout", "stuck", "error"]);
let state = null;
let busy = false;
let automatic = false;
let timerBase = 0;
let timerSyncedAt = performance.now();
let frameRequestInFlight = false;
let inputMode = "text";
let recognition = null;
let voiceListening = false;
let voiceGeneration = 0;
let voiceSession = null;
let voicePlanTimer = null;
let voicePlanInFlight = false;
let voicePlanQueued = null;
let voiceFallbackCommand = "reset";

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);

const formatTimer = (milliseconds) => {
  const total = Math.max(0, Math.floor(milliseconds));
  const minutes = Math.floor(total / 60000);
  const seconds = Math.floor(total % 60000 / 1000);
  const millis = total % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
};

function elapsedNow() {
  const running = state?.started_at_epoch_ms && !terminal.has(state.status);
  return timerBase + (running ? performance.now() - timerSyncedAt : 0);
}

function syncTimer() {
  timerBase = state?.elapsed_ms || 0;
  timerSyncedAt = performance.now();
}

function drawTimer() {
  const elapsed = elapsedNow();
  byId("timer").textContent = formatTimer(elapsed);
  const decisions = state?.history?.length || 0;
  byId("decision-rate").textContent = elapsed > 0
    ? (decisions / (elapsed / 1000)).toFixed(2)
    : "0.00";
  requestAnimationFrame(drawTimer);
}
requestAnimationFrame(drawTimer);

async function call(name, body = {}) {
  const payload = { ...body };
  if (name !== "reset" && state?.run_id) payload.run_id = state.run_id;
  const response = await fetch(`/api/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Inspector-Token": token },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "OpenUltra request failed");
  state = data;
  syncTimer();
  render();
  return data;
}

function setControls() {
  const active = state?.page && !terminal.has(state.status);
  const hasPage = Boolean(state?.page);
  byId("start").disabled = busy || voiceListening;
  byId("start").className = hasPage ? "secondary" : "primary";
  byId("continue-task").hidden = !hasPage;
  byId("continue-task").disabled = busy || voiceListening || !hasPage;
  byId("goal").disabled = busy;
  byId("text-mode").disabled = busy || voiceListening;
  byId("voice-mode").disabled = busy || voiceListening;
  byId("run-transcript").disabled = busy || voiceListening || !byId("goal").value.trim();
  byId("run").disabled = busy || !active || automatic;
  byId("run").hidden = automatic;
  byId("stop").hidden = !automatic;
  byId("loading").hidden = !(busy && !state?.page);
}

function renderInputMode() {
  const voice = inputMode === "voice";
  byId("text-mode").classList.toggle("active", !voice);
  byId("voice-mode").classList.toggle("active", voice);
  byId("text-mode").setAttribute("aria-pressed", String(!voice));
  byId("voice-mode").setAttribute("aria-pressed", String(voice));
  byId("voice-state").hidden = !voice;
  byId("goal").placeholder = voice
    ? "Your spoken task appears here as you talk"
    : "Describe what you want the browser to do";
  byId("goal").required = !voice;
  byId("start").textContent = voice ? "Speak new task" : "Start new task";
  byId("continue-task").textContent = voice
    ? "Speak and continue"
    : "Continue from current page";
  updateTranscriptFallback();
  setControls();
}

function updateTranscriptFallback(force = false) {
  const available = inputMode === "voice" && Boolean(byId("goal").value.trim());
  byId("run-transcript").hidden = !(available && (force || !voiceListening));
}

function clearVoicePlanning() {
  clearTimeout(voicePlanTimer);
  voicePlanTimer = null;
  voicePlanQueued = null;
}

function stopVoice({ cancelled = false, preserveSession = false } = {}) {
  voiceListening = false;
  try { recognition?.stop(); } catch { /* The recognizer may already be stopped. */ }
  recognition = null;
  clearVoicePlanning();
  if (!preserveSession) voiceSession = null;
  byId("cancel-voice").hidden = true;
  byId("voice-state").classList.remove("listening");
  byId("voice-label").textContent = cancelled ? "Listening cancelled" : "Ready to listen";
  updateTranscriptFallback();
  setControls();
}

function transcriptTail(fullText, committedText) {
  const full = fullText.replace(/\s+/g, " ").trim();
  const committed = committedText.replace(/\s+/g, " ").trim();
  if (!full.toLocaleLowerCase().startsWith(committed.toLocaleLowerCase())) return "";
  return full.slice(committed.length)
    .replace(/^\s*(?:,|;|\band\s+then\b|\bthen\b|\band\b)+\s*/i, "")
    .trim();
}

async function postVoicePlan(transcript) {
  const response = await fetch("/api/voice-plan", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Inspector-Token": token },
    body: JSON.stringify({ transcript }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Voice decision failed");
  return data;
}

async function flushVoicePlan() {
  if (voicePlanInFlight || !voicePlanQueued || !voiceSession?.listening) return;
  const request = voicePlanQueued;
  voicePlanQueued = null;
  voicePlanInFlight = true;
  byId("voice-label").textContent = "Understanding";
  try {
    const plan = await postVoicePlan(request.transcript);
    if (
      voiceSession?.generation === request.generation
      && voiceSession.listening
      && !voiceSession.committedText
      && !voiceSession.final
      && plan.decision === "act"
    ) {
      voiceSession.committedText = plan.candidate;
      byId("voice-label").textContent = "Acting while you speak";
      void executeVoiceTask(voiceSession.command, plan.candidate);
    } else if (voiceSession?.generation === request.generation && voiceSession.listening) {
      byId("voice-label").textContent = "Listening";
    }
  } catch (error) {
    if (voiceSession?.generation === request.generation) {
      byId("voice-label").textContent = "Listening";
      byId("error").textContent = error.message;
      byId("error").hidden = false;
    }
  } finally {
    voicePlanInFlight = false;
    if (voicePlanQueued) void flushVoicePlan();
  }
}

function scheduleVoicePlan(transcript, generation) {
  clearTimeout(voicePlanTimer);
  voicePlanQueued = { transcript, generation };
  voicePlanTimer = setTimeout(() => void flushVoicePlan(), 200);
}

async function executeVoiceTask(command, goal) {
  if (!goal.trim()) return;
  if (busy) {
    if (voiceSession) voiceSession.pendingTask = { command, goal };
    return;
  }
  await startTask(command, goal);
  if (voiceSession?.pendingTask && !busy) {
    const pending = voiceSession.pendingTask;
    voiceSession.pendingTask = null;
    await executeVoiceTask(pending.command, pending.goal);
  }
}

function finalizeVoiceTranscript(transcript) {
  if (!voiceSession || voiceSession.final) return;
  voiceSession.final = true;
  voiceSession.listening = false;
  const committed = voiceSession.committedText;
  const tail = committed ? transcriptTail(transcript, committed) : transcript;
  clearVoicePlanning();
  try { recognition?.stop(); } catch { /* The recognizer may already be stopped. */ }
  recognition = null;
  voiceListening = false;
  byId("cancel-voice").hidden = true;
  byId("voice-state").classList.remove("listening");
  if (committed && !tail) {
    byId("voice-label").textContent = "Spoken command committed";
  } else if (tail) {
    byId("voice-label").textContent = committed ? "Continuing spoken task" : "Task captured";
    const command = committed ? "retask" : voiceSession.command;
    voiceSession.pendingTask = { command, goal: tail };
    void executeVoiceTask(command, tail);
  }
  updateTranscriptFallback();
  setControls();
}

function listenForTask(command) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    voiceFallbackCommand = command;
    byId("error").textContent = "Live voice needs Chrome or Edge speech recognition. You can still type or paste the transcript and run it.";
    byId("error").hidden = false;
    updateTranscriptFallback(true);
    return;
  }
  stopVoice();
  const generation = ++voiceGeneration;
  const nextRecognition = new SpeechRecognition();
  recognition = nextRecognition;
  voiceListening = true;
  voiceFallbackCommand = command;
  voiceSession = {
    generation,
    command,
    committedText: "",
    final: false,
    latestTranscript: "",
    listening: true,
    pendingTask: null,
  };
  nextRecognition.continuous = true;
  nextRecognition.interimResults = true;
  nextRecognition.lang = "en-US";
  nextRecognition.maxAlternatives = 1;
  byId("error").hidden = true;
  byId("voice-state").classList.add("listening");
  byId("voice-label").textContent = "Listening";
  byId("cancel-voice").hidden = false;
  setControls();

  nextRecognition.onresult = (event) => {
    if (generation !== voiceGeneration) return;
    const transcript = Array.from(event.results)
      .map((result) => result[0]?.transcript || "")
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
    if (transcript) byId("goal").value = transcript;
    const final = Array.from(event.results).every((result) => result.isFinal);
    if (voiceSession) voiceSession.latestTranscript = transcript;
    updateTranscriptFallback();
    if (final && transcript) finalizeVoiceTranscript(transcript);
    else if (transcript && !voiceSession?.committedText) scheduleVoicePlan(transcript, generation);
  };
  nextRecognition.onerror = (event) => {
    if (generation !== voiceGeneration) return;
    const captured = byId("goal").value.trim();
    const committed = voiceSession?.committedText || "";
    const remaining = committed ? transcriptTail(captured, committed) : captured;
    if (committed) {
      voiceFallbackCommand = "retask";
      if (remaining) byId("goal").value = remaining;
    }
    stopVoice({ preserveSession: false });
    byId("error").textContent = event.error === "network"
      ? "Browser speech recognition is unavailable. Run the captured transcript below, or open OpenUltra in Chrome or Edge for live voice."
      : `Voice input failed: ${event.error || "unknown error"}. You can still run the captured transcript.`;
    byId("error").hidden = false;
    if (remaining) updateTranscriptFallback(true);
  };
  nextRecognition.onend = () => {
    if (generation !== voiceGeneration || !voiceListening) return;
    const transcript = voiceSession?.latestTranscript || byId("goal").value.trim();
    if (transcript) finalizeVoiceTranscript(transcript);
    else {
      stopVoice();
      byId("voice-label").textContent = "No speech captured";
    }
  };
  nextRecognition.start();
}

function requestTask(command) {
  if (inputMode === "voice") listenForTask(command);
  else startTask(command);
}

function render() {
  if (!state) return;
  const labels = {
    idle: "Ready for a task",
    ready: "Ready for the next step",
    predicted: "Action selected",
    completed: "Task completed",
    blocked: "No supported action remains",
    needs_verification: "Outcome needs review",
    needs_login: "Sign in required",
    max_steps: "Action limit reached",
    timeout: "Time limit reached",
    stuck: "No further progress",
    error: "Runtime error",
  };
  byId("status").textContent = labels[state.status] || state.reason || state.status;
  byId("status-dot").className = `status-dot ${terminal.has(state.status) ? (state.status === "completed" ? "" : "error") : state.page ? "active" : ""}`;
  byId("engine-state").textContent = state.network_model_calls === 0 ? "Offline model" : "Model connected";

  const latestDecision = state.decision?.inference_ms
    ?? state.history.at(-1)?.inference_ms
    ?? null;
  byId("decision-ms").textContent = latestDecision == null ? "--" : latestDecision.toFixed(1);
  byId("step-count").textContent = String(state.history.length);

  const page = state.page;
  if (!page) {
    byId("empty").hidden = false;
    byId("screenshot").hidden = true;
    byId("current-action").textContent = "No task running";
    setControls();
    return;
  }

  byId("empty").hidden = true;
  byId("screenshot").hidden = !page.screenshot;
  if (page.screenshot) byId("screenshot").src = `data:image/jpeg;base64,${page.screenshot}`;
  byId("url").textContent = page.url;
  byId("page-title").textContent = page.title || "Untitled page";

  const selectedAction = state.policy?.executed_action || state.decision?.proposed_action;
  const chosen = state.actions.find((action) => action.action_id === selectedAction);
  byId("current-action").textContent = chosen?.description || state.reason || "Observing the page";
  byId("history").innerHTML = state.history.slice(-8).reverse().map((row) => `
    <div class="trace-row">
      <span>${String(row.step).padStart(2, "0")}</span>
      <p>${escapeHtml(row.description)}</p>
    </div>
  `).join("");
  setControls();
}

async function runAutomatically() {
  automatic = true;
  setControls();
  try {
    for (let index = 0; index < state.max_steps * 2 && automatic; index += 1) {
      byId("status").textContent = "Running";
      if (state.status !== "predicted") await call("predict");
      if (terminal.has(state.status)) break;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      if (!automatic) break;
      if (state.status === "predicted") {
        await call("act", { fingerprint: state.page.fingerprint });
      }
      if (terminal.has(state.status)) break;
      await new Promise((resolve) => requestAnimationFrame(resolve));
    }
  } finally {
    automatic = false;
    busy = false;
    setControls();
  }
}

async function startTask(command = "reset", goal = byId("goal").value) {
  if (busy) return;
  busy = true;
  automatic = false;
  byId("error").hidden = true;
  byId("status").textContent = "Opening browser";
  setControls();
  try {
    await call(command, {
      goal,
      max_steps: 24,
      max_seconds: 180,
      max_candidates: 20,
    });
    await runAutomatically();
  } catch (error) {
    automatic = false;
    busy = false;
    try {
      const response = await fetch("/api/state");
      if (response.ok) state = await response.json();
    } catch {
      // Preserve the command error when the local service is unavailable.
    }
    syncTimer();
    render();
    byId("error").textContent = error.message;
    byId("error").hidden = false;
    byId("status").textContent = "Paused";
    setControls();
  }
}

byId("task-form").addEventListener("submit", (event) => {
  event.preventDefault();
  requestTask("reset");
});

byId("continue-task").addEventListener("click", () => requestTask("retask"));
byId("text-mode").addEventListener("click", () => {
  if (voiceListening) stopVoice({ cancelled: true });
  inputMode = "text";
  renderInputMode();
  byId("goal").focus();
});
byId("voice-mode").addEventListener("click", () => {
  inputMode = "voice";
  renderInputMode();
});
byId("cancel-voice").addEventListener("click", () => {
  voiceGeneration += 1;
  stopVoice({ cancelled: true });
});
byId("run-transcript").addEventListener("click", () => {
  const transcript = byId("goal").value.trim();
  if (!transcript || busy) return;
  byId("error").hidden = true;
  void startTask(voiceFallbackCommand, transcript);
});
byId("goal").addEventListener("input", () => updateTranscriptFallback());

byId("run").addEventListener("click", () => {
  if (!busy && state?.page && !terminal.has(state.status)) {
    busy = true;
    runAutomatically().catch((error) => {
      byId("error").textContent = error.message;
      byId("error").hidden = false;
    });
  }
});

byId("stop").addEventListener("click", () => {
  automatic = false;
  byId("status").textContent = "Pausing after this step";
  setControls();
});

fetch("/api/state").then((response) => response.json()).then((value) => {
  state = value;
  if (value.goal) byId("goal").value = value.goal;
  syncTimer();
  render();
}).catch(() => { byId("status").textContent = "Local service unavailable"; });

renderInputMode();

async function refreshFrame() {
  if (frameRequestInFlight || !state?.page || document.hidden) return;
  frameRequestInFlight = true;
  try {
    const response = await fetch("/api/frame", {
      headers: { "X-Inspector-Token": token },
      cache: "no-store",
    });
    if (!response.ok || response.status === 204) return;
    const frame = await response.json();
    if (frame.screenshot && frame.fresh) {
      byId("screenshot").src = `data:image/jpeg;base64,${frame.screenshot}`;
      byId("screenshot").hidden = false;
      byId("frame-state").textContent = "Live preview";
    } else if (!frame.fresh) {
      byId("frame-state").textContent = "Last frame";
    }
  } catch {
    byId("frame-state").textContent = "Preview paused";
  } finally {
    frameRequestInFlight = false;
  }
}

setInterval(refreshFrame, 400);
