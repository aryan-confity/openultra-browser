const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scriptPath = path.resolve(__dirname, '../../src/openultra_browser/static/inspector.js');
const script = fs.readFileSync(scriptPath, 'utf8');

function element() {
  const listeners = new Map();
  const classes = new Set();
  return {
    addEventListener(name, callback) { listeners.set(name, callback); },
    dispatch(name, event = {}) { listeners.get(name)?.(event); },
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    setAttribute(name, value) { this[name] = value; },
    focus() { this.focused = true; },
    value: '',
    textContent: '',
    hidden: false,
    disabled: false,
    src: '',
    innerHTML: '',
  };
}

function makeState(status = 'ready', overrides = {}) {
  return {
    status,
    reason: '',
    run_id: 'run-1',
    page: { url: 'https://example.com/', title: 'Example', screenshot: 'image', fingerprint: 'one' },
    actions: [],
    history: [],
    decision: null,
    policy: null,
    elapsed_ms: 1000,
    started_at_epoch_ms: Date.now(),
    max_steps: 4,
    network_model_calls: 0,
    ...overrides,
  };
}

function harness(responses = []) {
  const elements = new Map();
  const requests = [];
  const documentListeners = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {
      getElementById: getElement,
      addEventListener(name, callback) { documentListeners.set(name, callback); },
      querySelector(selector) {
        if (selector === 'meta[name="inspector-token"]') return { content: 'token' };
        throw new Error(`Unexpected selector: ${selector}`);
      },
      hidden: false,
    },
    navigator: { userAgent: 'Chrome' },
    performance: { now: () => 2000 },
    requestAnimationFrame(callback) {
      if (callback.name !== 'drawTimer') queueMicrotask(callback);
      return 1;
    },
    setInterval() { return 1; },
    setTimeout() { return 1; },
    clearTimeout() {},
    fetch: async (url, options) => {
      requests.push({ url, options });
      const next = url === '/api/state'
        ? makeState('idle', { page: null })
        : responses.shift() || makeState('idle', { page: null });
      return { ok: next.ok ?? true, status: next.httpStatus ?? 200, json: async () => next };
    },
  });
  vm.runInContext(script, context, { filename: scriptPath });
  documentListeners.get('DOMContentLoaded')();
  return {
    context, elements, getElement, requests,
    ready: new Promise((resolve) => setImmediate(resolve)),
    evaluate: (code) => vm.runInContext(code, context),
  };
}

test('renders the empty state and formats elapsed time', () => {
  const ui = harness();
  ui.evaluate('state = { status: "idle", reason: "Ready", page: null, history: [], network_model_calls: 0 }');
  ui.evaluate('render(); drawTimer()');
  assert.equal(ui.getElement('empty').hidden, false);
  assert.equal(ui.getElement('current-action').textContent, 'No task running');
  assert.equal(ui.evaluate('formatTimer(61009)'), '01:01.009');
  assert.equal(ui.getElement('decision-rate').textContent, '0.00');
});

test('renders active page, action, history, and model latency', () => {
  const ui = harness();
  ui.context.sample = makeState('predicted', {
    history: [{ step: 1, description: '<click>', inference_ms: 28 }],
    actions: [{ action_id: 'click-1', description: 'Open details' }],
    decision: { proposed_action: 'click-1', inference_ms: 29 },
  });
  ui.evaluate('state = sample; syncTimer(); render(); drawTimer()');
  assert.equal(ui.getElement('empty').hidden, true);
  assert.equal(ui.getElement('url').textContent, 'https://example.com/');
  assert.equal(ui.getElement('current-action').textContent, 'Open details');
  assert.match(ui.getElement('history').innerHTML, /&lt;click&gt;/);
  assert.equal(ui.getElement('decision-ms').textContent, '29.0');
  assert.equal(ui.getElement('step-count').textContent, '1');
});

test('call binds the active run and rejects failed commands', async () => {
  const ui = harness([makeState('ready'), { ok: false, error: 'Command rejected' }]);
  await ui.ready;
  ui.context.sample = makeState('ready');
  ui.evaluate('state = sample');
  await ui.evaluate('call("predict")');
  assert.equal(JSON.parse(ui.requests[1].options.body).run_id, 'run-1');
  await assert.rejects(ui.evaluate('call("act")'), /Command rejected/);
});

test('automatic run predicts, acts, and stops on completion', async () => {
  const predicted = makeState('predicted', {
    decision: { proposed_action: 'click-1' },
    actions: [{ action_id: 'click-1', description: 'Open details' }],
  });
  const ui = harness([predicted, makeState('completed')]);
  await ui.ready;
  ui.context.sample = makeState('ready');
  ui.evaluate('state = sample');
  await ui.evaluate('runAutomatically()');
  assert.deepEqual(ui.requests.map((request) => request.url), ['/api/state', '/api/predict', '/api/act']);
  assert.equal(ui.getElement('stop').hidden, true);
});

test('new task and continuation use separate commands', async () => {
  const ui = harness([makeState('completed'), makeState('completed')]);
  await ui.ready;
  ui.getElement('goal').value = 'Open details';
  await ui.evaluate('startTask("reset", "Open details")');
  await ui.evaluate('startTask("retask", "Go back")');
  assert.deepEqual(ui.requests.map((request) => request.url), ['/api/state', '/api/reset', '/api/retask']);
  assert.equal(JSON.parse(ui.requests[2].options.body).goal, 'Go back');
});

test('text mode and transcript fallback remain runnable', () => {
  const ui = harness();
  ui.getElement('goal').value = 'Open the current tab';
  ui.evaluate('inputMode = "voice"; renderInputMode(); updateTranscriptFallback(true)');
  assert.equal(ui.getElement('run-transcript').hidden, false);
  ui.getElement('text-mode').dispatch('click');
  assert.equal(ui.getElement('goal').focused, true);
  assert.equal(ui.getElement('text-mode').classList.contains('active'), true);
});

test('voice input reports unsupported recognition without discarding text', () => {
  const ui = harness();
  ui.getElement('goal').value = 'Click Rent';
  ui.evaluate('inputMode = "voice"; listenForTask("retask")');
  assert.match(ui.getElement('error').textContent, /Chrome or Edge/);
  assert.equal(ui.getElement('run-transcript').hidden, false);
});

test('voice transcript tail removes only a committed prefix', () => {
  const ui = harness();
  assert.equal(ui.evaluate('transcriptTail("open wikipedia and then search for Ada", "open wikipedia")'), 'search for Ada');
  assert.equal(ui.evaluate('transcriptTail("open youtube", "open wikipedia")'), '');
});

test('live frame refresh updates preview and handles stale frames', async () => {
  const ui = harness([
    { screenshot: 'new-frame', fresh: true },
    { screenshot: 'old-frame', fresh: false },
  ]);
  await ui.ready;
  ui.context.sample = makeState('ready');
  ui.evaluate('state = sample');
  await ui.evaluate('refreshFrame()');
  assert.equal(ui.getElement('screenshot').src, 'data:image/jpeg;base64,new-frame');
  await ui.evaluate('refreshFrame()');
  assert.equal(ui.getElement('frame-state').textContent, 'Last frame');
});

test('recognized final voice command continues the owned page', async () => {
  const ui = harness([makeState('completed')]);
  await ui.ready;
  ui.evaluate(`
    state = { status: "ready", page: { url: "https://example.com/" }, history: [] };
    globalThis.SpeechRecognition = class {
      start() { this.started = true; }
      stop() { this.stopped = true; }
    };
    inputMode = "voice";
    listenForTask("retask");
    recognition.onresult({ resultIndex: 0, results: [{ 0: { transcript: "Click Rent" }, isFinal: true }] });
  `);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(ui.requests[1].url, '/api/retask');
  assert.equal(JSON.parse(ui.requests[1].options.body).goal, 'Click Rent');
  assert.equal(ui.getElement('voice-label').textContent, 'Listening for next command');
});

test('early voice decision executes one complete command and does not repeat its final transcript', async () => {
  const ui = harness([{ decision: 'act', candidate: 'Click Rent' }, makeState('completed')]);
  await ui.ready;
  ui.evaluate(`
    state = { status: "ready", page: { url: "https://example.com/" }, history: [] };
    globalThis.SpeechRecognition = class { start() {} stop() {} };
    inputMode = "voice";
    listenForTask("retask");
    recognition.onresult({ resultIndex: 0, results: [{ 0: { transcript: "Click Rent" }, isFinal: false }] });
  `);
  await ui.evaluate('flushVoicePlan()');
  ui.evaluate('recognition.onresult({ resultIndex: 0, results: [{ 0: { transcript: "Click Rent" }, isFinal: true }] })');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(ui.requests.map((request) => request.url), ['/api/state', '/api/voice-plan', '/api/retask']);
  assert.equal(JSON.parse(ui.requests[2].options.body).goal, 'Click Rent');
});

test('voice cancellation and recognition errors keep the typed transcript available', async () => {
  const ui = harness();
  await ui.ready;
  ui.evaluate(`
    globalThis.SpeechRecognition = class { start() {} stop() {} };
    inputMode = "voice";
    listenForTask("reset");
    recognition.onresult({ resultIndex: 0, results: [{ 0: { transcript: "Open Docs" }, isFinal: false }] });
    recognition.onerror({ error: "network" });
  `);
  assert.match(ui.getElement('error').textContent, /speech service could not connect/);
  assert.equal(ui.getElement('goal').value, 'Open Docs');
  assert.equal(ui.getElement('run-transcript').hidden, false);
  ui.evaluate('listenForTask("reset")');
  ui.getElement('cancel-voice').dispatch('click');
  assert.equal(ui.getElement('voice-label').textContent, 'Listening cancelled');
});

test('failed task request preserves error and releases busy state', async () => {
  const ui = harness([{ ok: false, error: 'Task rejected' }]);
  await ui.ready;
  await ui.evaluate('startTask("reset", "Open Docs")');
  assert.equal(ui.getElement('error').textContent, 'Task rejected');
  assert.equal(ui.evaluate('busy'), false);
});

test('model selector switches the engine and rotates the browser run', async () => {
  const ui = harness([makeState('ready', { model_id: 'von', run_id: 'run-2' })]);
  await ui.ready;
  ui.context.sample = makeState('ready', { model_id: 'laya', semif_available: true });
  ui.evaluate('state = sample; render()');
  assert.equal(ui.getElement('semif-option').disabled, false);
  ui.getElement('model-select').value = 'von';
  ui.getElement('model-select').dispatch('change', { target: ui.getElement('model-select') });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(ui.requests[1].url, '/api/switch-model');
  assert.deepEqual(JSON.parse(ui.requests[1].options.body), {
    model_id: 'von', run_id: 'run-1',
  });
  assert.equal(ui.getElement('model-select').value, 'von');
  assert.equal(ui.evaluate('state.run_id'), 'run-2');
  assert.equal(ui.evaluate('busy'), false);
});

test('failed model switch restores the previous selection', async () => {
  const ui = harness([{ ok: false, error: 'Checkpoint unavailable' }]);
  await ui.ready;
  ui.context.sample = makeState('ready', { model_id: 'laya', semif_available: false });
  ui.evaluate('state = sample; render()');
  assert.equal(ui.getElement('semif-option').disabled, true);
  ui.getElement('model-select').value = 'von';
  ui.getElement('model-select').dispatch('change', { target: ui.getElement('model-select') });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(ui.getElement('model-select').value, 'laya');
  assert.equal(ui.getElement('error').textContent, 'Checkpoint unavailable');
  assert.equal(ui.evaluate('busy'), false);
});
