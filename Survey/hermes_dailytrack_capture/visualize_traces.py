#!/usr/bin/env python3
"""Local viewer for Hermes OpenAI raw trace captures."""

from __future__ import annotations

import argparse
import html
import json
import os
import socket
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


ROOT_DIR = Path(__file__).resolve().parent
RUNS_DIR = ROOT_DIR / "runs"
JSONL_NAME = "openai_raw_calls.jsonl"


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hermes Trace Viewer</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #627182;
      --line: #d8dee6;
      --accent: #0f766e;
      --accent-dark: #0b5f59;
      --warn: #a15c00;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--sans);
      background: var(--bg);
      color: var(--ink);
    }

    header {
      min-height: 64px;
      padding: 14px 20px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }

    h1 {
      margin: 0;
      font-size: 20px;
      line-height: 1.2;
      letter-spacing: 0;
    }

    .subtle {
      color: var(--muted);
      font-size: 13px;
    }

    .layout {
      min-height: calc(100vh - 64px);
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
    }

    aside {
      border-right: 1px solid var(--line);
      background: #ffffff;
      padding: 14px;
      overflow: auto;
      max-height: calc(100vh - 64px);
    }

    main {
      padding: 16px;
      overflow: auto;
      max-height: calc(100vh - 64px);
    }

    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
      margin: 10px 0 6px;
    }

    select, input, button {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: var(--ink);
    }

    select, input {
      width: 100%;
      padding: 9px 10px;
    }

    button {
      padding: 8px 10px;
      cursor: pointer;
    }

    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #ffffff;
    }

    button.primary:hover { background: var(--accent-dark); }

    .row {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .row > * { min-width: 0; }
    .row button { flex: 0 0 auto; }

    .inline-check {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
      text-transform: none;
    }

    .inline-check input {
      width: auto;
      padding: 0;
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin: 14px 0;
    }

    .stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfd;
      min-height: 64px;
    }

    .stat strong {
      display: block;
      font-size: 18px;
      margin-bottom: 4px;
    }

    .call-list {
      display: grid;
      gap: 8px;
    }

    .call {
      width: 100%;
      text-align: left;
      border-radius: 8px;
      padding: 10px;
      background: #ffffff;
      border: 1px solid var(--line);
    }

    .call.active {
      border-color: var(--accent);
      box-shadow: inset 3px 0 0 var(--accent);
    }

    .call-title {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-weight: 700;
      margin-bottom: 6px;
    }

    .call-title span {
      overflow-wrap: anywhere;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 2px 7px;
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
      background: #fbfcfd;
    }

    .badge.live {
      color: var(--warn);
      border-color: #e7bb74;
      background: #fff8e7;
    }

    .meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }

    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .tabs button.active {
      border-color: var(--accent);
      color: var(--accent-dark);
      font-weight: 700;
    }

    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
      margin-bottom: 14px;
    }

    .panel h2 {
      margin: 0 0 8px;
      font-size: 16px;
      letter-spacing: 0;
    }

    pre {
      margin: 0;
      padding: 14px;
      overflow: auto;
      min-height: 300px;
      max-height: 70vh;
      background: #101418;
      color: #edf2f7;
      border-radius: 8px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }

    .append-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }

    .empty {
      padding: 32px;
      text-align: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #ffffff;
    }

    .file-links {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    a.file-link {
      color: var(--accent-dark);
      border: 1px solid var(--line);
      padding: 6px 8px;
      border-radius: 6px;
      text-decoration: none;
      background: #ffffff;
      font-size: 13px;
    }

    @media (max-width: 920px) {
      .layout { grid-template-columns: 1fr; }
      aside { max-height: none; border-right: none; border-bottom: 1px solid var(--line); }
      main { max-height: none; }
      .grid { grid-template-columns: 1fr; }
      .append-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Hermes Trace Viewer</h1>
      <div class="subtle" id="rootLabel">Loading capture directory...</div>
    </div>
    <div class="row">
      <label class="inline-check"><input type="checkbox" id="autoRefreshInput" checked> Auto Refresh</label>
      <button id="refreshButton">Refresh</button>
      <button id="latestButton" class="primary">Latest Trace</button>
    </div>
  </header>

  <div class="layout">
    <aside>
      <label for="runSelect">Run</label>
      <select id="runSelect"></select>

      <div class="stats">
        <div class="stat">
          <strong id="callCount">0</strong>
          <span class="subtle">captured calls</span>
        </div>
        <div class="stat">
          <strong id="runState">-</strong>
          <span class="subtle">run status</span>
        </div>
      </div>

      <label for="filterInput">Filter traces</label>
      <div class="row">
        <input id="filterInput" placeholder="api, model, text, call index">
        <button id="clearFilterButton">Clear</button>
      </div>

      <label>Trace Calls</label>
      <div class="call-list" id="callList"></div>
    </aside>

    <main>
      <div id="emptyState" class="empty">Select a run to inspect captured OpenAI SDK requests and responses.</div>
      <div id="detail" hidden>
        <section class="panel">
          <div class="toolbar">
            <div>
              <h2 id="detailTitle">Trace</h2>
              <div class="meta" id="detailMeta"></div>
            </div>
            <div class="tabs">
              <button data-tab="summary" class="active">Summary</button>
              <button data-tab="request">Request</button>
              <button data-tab="response">Response</button>
              <button data-tab="append">Append View</button>
              <button data-tab="events">Stream Events</button>
              <button data-tab="full">Full Raw</button>
            </div>
          </div>
          <div class="file-links" id="fileLinks"></div>
        </section>

        <section class="panel" id="summaryPanel">
          <div class="grid">
            <div>
              <h2>Request</h2>
              <pre id="requestPreview"></pre>
            </div>
            <div>
              <h2>Response</h2>
              <pre id="responsePreview"></pre>
            </div>
          </div>
        </section>

        <section class="panel" id="appendPanel" hidden>
          <div class="append-grid">
            <div>
              <h2>Incoming Append</h2>
              <pre id="incomingAppendView"></pre>
            </div>
            <div>
              <h2>This Response</h2>
              <pre id="currentResponseView"></pre>
            </div>
            <div>
              <h2>Outgoing Append</h2>
              <pre id="outgoingAppendView"></pre>
            </div>
          </div>
        </section>

        <section class="panel" id="jsonPanel" hidden>
          <pre id="jsonView"></pre>
        </section>
      </div>
    </main>
  </div>

  <script>
    const state = {
      runs: [],
      currentRun: null,
      calls: [],
      currentCallIndex: null,
      currentCall: null,
      tab: 'summary',
      filter: '',
    };

    const els = {
      rootLabel: document.getElementById('rootLabel'),
      runSelect: document.getElementById('runSelect'),
      callCount: document.getElementById('callCount'),
      runState: document.getElementById('runState'),
      filterInput: document.getElementById('filterInput'),
      clearFilterButton: document.getElementById('clearFilterButton'),
      callList: document.getElementById('callList'),
      autoRefreshInput: document.getElementById('autoRefreshInput'),
      refreshButton: document.getElementById('refreshButton'),
      latestButton: document.getElementById('latestButton'),
      emptyState: document.getElementById('emptyState'),
      detail: document.getElementById('detail'),
      detailTitle: document.getElementById('detailTitle'),
      detailMeta: document.getElementById('detailMeta'),
      fileLinks: document.getElementById('fileLinks'),
      summaryPanel: document.getElementById('summaryPanel'),
      appendPanel: document.getElementById('appendPanel'),
      jsonPanel: document.getElementById('jsonPanel'),
      requestPreview: document.getElementById('requestPreview'),
      responsePreview: document.getElementById('responsePreview'),
      incomingAppendView: document.getElementById('incomingAppendView'),
      currentResponseView: document.getElementById('currentResponseView'),
      outgoingAppendView: document.getElementById('outgoingAppendView'),
      jsonView: document.getElementById('jsonView'),
    };

    function qs(name) {
      return new URLSearchParams(window.location.search).get(name);
    }

    function setUrl(runId, callIndex) {
      const url = new URL(window.location.href);
      if (runId) url.searchParams.set('run', runId);
      if (callIndex !== null && callIndex !== undefined) url.searchParams.set('call', String(callIndex));
      window.history.replaceState({}, '', url);
    }

    async function fetchJson(path) {
      const response = await fetch(path);
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`${response.status} ${response.statusText}: ${text}`);
      }
      return response.json();
    }

    function formatJson(value) {
      return JSON.stringify(value ?? null, null, 2);
    }

    function truncate(value, limit = 14000) {
      const text = typeof value === 'string' ? value : formatJson(value);
      if (text.length <= limit) return text;
      return `${text.slice(0, limit)}\n\n... truncated ${text.length - limit} characters in preview; use the raw tab for the full value ...`;
    }

    function describeInput(input) {
      if (Array.isArray(input)) return `${input.length} input item(s)`;
      if (typeof input === 'string') return `${input.length} chars input`;
      if (input && typeof input === 'object') return `${Object.keys(input).length} input key(s)`;
      return 'no input';
    }

    function callSearchText(call) {
      return [
        call.api,
        call.call_index,
        call.model,
        call.previous_response_id,
        call.preview,
        call.started_at,
        call.finished_at,
      ].filter(Boolean).join(' ').toLowerCase();
    }

    function renderRuns() {
      els.runSelect.innerHTML = '';
      for (const run of state.runs) {
        const option = document.createElement('option');
        option.value = run.id;
        option.textContent = `${run.id} (${run.call_count} calls${run.in_progress ? ', live' : ''})`;
        els.runSelect.appendChild(option);
      }
      if (state.currentRun) els.runSelect.value = state.currentRun.id;
    }

    function renderCallList() {
      els.callList.innerHTML = '';
      const needle = state.filter.trim().toLowerCase();
      const calls = needle
        ? state.calls.filter((call) => callSearchText(call).includes(needle))
        : state.calls;

      if (!calls.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = state.calls.length ? 'No calls match the filter.' : 'No calls captured yet.';
        els.callList.appendChild(empty);
        return;
      }

      for (const call of calls) {
        const button = document.createElement('button');
        button.className = 'call' + (call.call_index === state.currentCallIndex ? ' active' : '');
        button.addEventListener('click', () => selectCall(call.call_index));

        const title = document.createElement('div');
        title.className = 'call-title';
        const label = document.createElement('span');
        label.textContent = `#${call.call_index} ${call.api}`;
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = call.model || 'unknown model';
        title.append(label, badge);

        const meta = document.createElement('div');
        meta.className = 'meta';
        const parts = [
          call.started_at || '',
          call.duration_ms ? `${call.duration_ms} ms` : '',
          call.stream_event_count ? `${call.stream_event_count} events` : '',
          call.request_input_summary || '',
        ].filter(Boolean);
        meta.textContent = parts.join(' | ');

        if (call.preview) {
          const preview = document.createElement('div');
          preview.className = 'meta';
          preview.textContent = call.preview;
          button.append(title, meta, preview);
        } else {
          button.append(title, meta);
        }

        els.callList.appendChild(button);
      }
    }

    function renderRunSummary() {
      const run = state.currentRun;
      els.callCount.textContent = run ? String(run.call_count) : '0';
      els.runState.textContent = run ? (run.in_progress ? 'Live' : `Exit ${run.exit_code ?? '?'}`) : '-';
      els.runState.className = run && run.in_progress ? 'badge live' : '';
      renderFileLinks();
    }

    function renderFileLinks() {
      els.fileLinks.innerHTML = '';
      const run = state.currentRun;
      if (!run) return;

      for (const file of run.files || []) {
        const link = document.createElement('a');
        link.className = 'file-link';
        link.href = `/api/runs/${encodeURIComponent(run.id)}/files/${encodeURIComponent(file)}`;
        link.target = '_blank';
        link.rel = 'noreferrer';
        link.textContent = file;
        els.fileLinks.appendChild(link);
      }
    }

    function responseText(response) {
      if (!response || typeof response !== 'object') return response ?? null;
      if (response.output_text) return response.output_text;
      if (Array.isArray(response.output)) return response.output;
      if (response.note || response.stream_event_count) return response;
      return response;
    }

    function renderDetail() {
      const call = state.currentCall;
      if (!call) {
        els.emptyState.hidden = false;
        els.detail.hidden = true;
        return;
      }

      els.emptyState.hidden = true;
      els.detail.hidden = false;

      const request = call.request_kwargs || {};
      els.detailTitle.textContent = `#${call.call_index} ${call.api}`;
      els.detailMeta.textContent = [
        `pid ${call.pid ?? '?'}`,
        call.thread || '',
        call.started_at || '',
        call.finished_at || '',
        request.model ? `model ${request.model}` : '',
        request.previous_response_id ? `previous ${request.previous_response_id}` : '',
      ].filter(Boolean).join(' | ');

      els.requestPreview.textContent = truncate({
        model: request.model,
        instructions: request.instructions,
        input: request.input,
        tools: request.tools,
        previous_response_id: request.previous_response_id,
        tool_choice: request.tool_choice,
        reasoning: request.reasoning,
        text: request.text,
      });
      els.responsePreview.textContent = truncate(responseText(call.response));

      const tabButtons = document.querySelectorAll('.tabs button');
      tabButtons.forEach((button) => {
        button.classList.toggle('active', button.dataset.tab === state.tab);
      });

      els.summaryPanel.hidden = state.tab !== 'summary';
      els.appendPanel.hidden = state.tab !== 'append';
      els.jsonPanel.hidden = state.tab === 'summary' || state.tab === 'append';

      if (state.tab === 'request') {
        els.jsonView.textContent = formatJson(call.request_kwargs);
      } else if (state.tab === 'response') {
        els.jsonView.textContent = formatJson(call.response);
      } else if (state.tab === 'append') {
        const append = call._append_view || { canonical: false, note: 'No append view is available for this call.' };
        els.incomingAppendView.textContent = truncate(append.incoming_append ?? append.note);
        els.currentResponseView.textContent = truncate(append.this_response ?? call.response);
        els.outgoingAppendView.textContent = truncate(append.outgoing_append ?? append.outgoing_note ?? null);
      } else if (state.tab === 'events') {
        els.jsonView.textContent = formatJson(call.stream_events || []);
      } else if (state.tab === 'full') {
        els.jsonView.textContent = formatJson(call);
      }
    }

    async function loadRuns(preferredRunId) {
      const data = await fetchJson('/api/runs');
      state.runs = data.runs;
      els.rootLabel.textContent = data.root;
      if (!state.runs.length) {
        renderRuns();
        renderRunSummary();
        renderCallList();
        renderDetail();
        return;
      }
      const selected = state.runs.find((run) => run.id === preferredRunId) || state.runs[0];
      state.currentRun = selected;
      renderRuns();
      await loadCalls(selected.id, qs('call'));
    }

    async function loadCalls(runId, preferredCallIndex) {
      const data = await fetchJson(`/api/runs/${encodeURIComponent(runId)}/calls`);
      state.currentRun = data.run;
      state.calls = data.calls;
      renderRuns();
      renderRunSummary();

      let selectedIndex = preferredCallIndex === null || preferredCallIndex === undefined
        ? null
        : Number(preferredCallIndex);
      if (!Number.isFinite(selectedIndex) || !state.calls.some((call) => call.call_index === selectedIndex)) {
        selectedIndex = state.calls.length ? state.calls[state.calls.length - 1].call_index : null;
      }

      renderCallList();
      if (selectedIndex !== null) {
        await selectCall(selectedIndex);
      } else {
        state.currentCall = null;
        renderDetail();
      }
    }

    async function selectCall(callIndex) {
      state.currentCallIndex = callIndex;
      renderCallList();
      const runId = state.currentRun.id;
      state.currentCall = await fetchJson(`/api/runs/${encodeURIComponent(runId)}/calls/${encodeURIComponent(callIndex)}`);
      setUrl(runId, callIndex);
      renderDetail();
    }

    document.querySelectorAll('.tabs button').forEach((button) => {
      button.addEventListener('click', () => {
        state.tab = button.dataset.tab;
        renderDetail();
      });
    });

    els.runSelect.addEventListener('change', async () => {
      await loadCalls(els.runSelect.value, null);
    });

    els.filterInput.addEventListener('input', () => {
      state.filter = els.filterInput.value;
      renderCallList();
    });

    els.clearFilterButton.addEventListener('click', () => {
      state.filter = '';
      els.filterInput.value = '';
      renderCallList();
    });

    els.refreshButton.addEventListener('click', async () => {
      const currentRunId = state.currentRun ? state.currentRun.id : qs('run');
      const currentCall = state.currentCallIndex;
      await loadRuns(currentRunId);
      if (currentCall !== null && state.calls.some((call) => call.call_index === currentCall)) {
        await selectCall(currentCall);
      }
    });

    els.latestButton.addEventListener('click', async () => {
      await loadRuns(null);
    });

    setInterval(async () => {
      if (!els.autoRefreshInput.checked) return;
      const currentRunId = state.currentRun ? state.currentRun.id : qs('run');
      const currentCall = state.currentCallIndex;
      await loadRuns(currentRunId);
      if (currentCall !== null && state.calls.some((call) => call.call_index === currentCall)) {
        await selectCall(currentCall);
      }
    }, 10000);

    loadRuns(qs('run')).catch((error) => {
      els.emptyState.hidden = false;
      els.emptyState.textContent = error.stack || error.message || String(error);
      els.detail.hidden = true;
    });
  </script>
</body>
</html>
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not path.exists():
        return calls
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                calls.append(
                    {
                        "api": "parse_error",
                        "call_index": line_number,
                        "started_at": None,
                        "finished_at": None,
                        "request_kwargs": {"line_number": line_number, "raw": line},
                        "response": {"error": str(exc)},
                        "stream_events": [],
                    }
                )
                continue
            calls.append(obj)
    return calls


def safe_run_path(run_id: str) -> Path:
    run_id = unquote(run_id)
    if "/" in run_id or "\\" in run_id or run_id in {"", ".", ".."}:
        raise ValueError("invalid run id")
    candidates = [(RUNS_DIR / run_id).resolve(), (ROOT_DIR / run_id).resolve()]
    for path in candidates:
        if not path.is_dir():
            continue
        if ROOT_DIR.resolve() not in path.parents:
            continue
        if (path / JSONL_NAME).exists():
            return path
    raise ValueError("unknown run id")


def safe_file_path(run_id: str, file_name: str) -> Path:
    allowed = {
        "prompt.txt",
        "hermes_stdout.txt",
        "exit_code.txt",
        "openai_raw_calls.json",
        "openai_raw_calls.jsonl",
    }
    if file_name not in allowed:
        raise ValueError("file is not exposed")
    path = (safe_run_path(run_id) / file_name).resolve()
    if safe_run_path(run_id).resolve() not in path.parents:
        raise ValueError("invalid file path")
    return path


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def read_exit_code(run_path: Path) -> str | None:
    exit_file = run_path / "exit_code.txt"
    if not exit_file.exists():
        return None
    return exit_file.read_text(encoding="utf-8", errors="replace").strip() or None


def run_summary(run_path: Path) -> dict[str, Any]:
    jsonl_path = run_path / JSONL_NAME
    call_count = 0
    first_started = None
    last_finished = None
    last_started = None
    if jsonl_path.exists():
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                call_count += 1
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                first_started = first_started or obj.get("started_at")
                last_started = obj.get("started_at") or last_started
                last_finished = obj.get("finished_at") or last_finished

    files = [
        name
        for name in ["prompt.txt", "hermes_stdout.txt", "exit_code.txt", "openai_raw_calls.json", "openai_raw_calls.jsonl"]
        if (run_path / name).exists()
    ]
    exit_code = read_exit_code(run_path)
    stat = jsonl_path.stat() if jsonl_path.exists() else run_path.stat()
    return {
        "id": run_path.name,
        "path": str(run_path),
        "call_count": call_count,
        "jsonl_bytes": file_size(jsonl_path),
        "mtime": stat.st_mtime,
        "first_started_at": first_started,
        "last_started_at": last_started,
        "last_finished_at": last_finished,
        "exit_code": exit_code,
        "in_progress": exit_code is None,
        "files": files,
    }


def list_runs() -> list[dict[str, Any]]:
    candidates: list[Path] = []
    if RUNS_DIR.exists():
        candidates.extend(path for path in RUNS_DIR.iterdir() if path.is_dir())
    candidates.extend(
        path
        for path in ROOT_DIR.iterdir()
        if path.is_dir() and path.name != "runs" and (path / JSONL_NAME).exists()
    )
    runs = [run_summary(path) for path in candidates if (path / JSONL_NAME).exists()]
    runs.sort(key=lambda item: (item["mtime"], item["id"]), reverse=True)
    return runs


def duration_ms(call: dict[str, Any]) -> int | None:
    start = call.get("started_at")
    finish = call.get("finished_at")
    if not start or not finish:
        return None
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        finish_dt = datetime.fromisoformat(finish.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((finish_dt - start_dt).total_seconds() * 1000)


def preview_from_response(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip().replace("\n", " ")[:180]
    output = response.get("output")
    if isinstance(output, list):
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("content"), list):
                for content in item["content"]:
                    if isinstance(content, dict):
                        text = content.get("text") or content.get("content")
                        if isinstance(text, str):
                            text_parts.append(text)
            elif isinstance(item.get("content"), str):
                text_parts.append(item["content"])
            if isinstance(item.get("name"), str):
                text_parts.append(item["name"])
        joined = " ".join(part.strip().replace("\n", " ") for part in text_parts if part.strip())
        return joined[:180]
    note = response.get("note")
    if isinstance(note, str):
        return note[:180]
    return ""


def summarize_call(call: dict[str, Any]) -> dict[str, Any]:
    request = call.get("request_kwargs") or {}
    response = call.get("response")
    stream_events = call.get("stream_events") or []
    input_value = request.get("input")
    return {
        "api": call.get("api"),
        "call_index": call.get("call_index"),
        "started_at": call.get("started_at"),
        "finished_at": call.get("finished_at"),
        "duration_ms": duration_ms(call),
        "pid": call.get("pid"),
        "thread": call.get("thread"),
        "model": request.get("model"),
        "previous_response_id": request.get("previous_response_id"),
        "request_input_summary": summarize_input(input_value),
        "tool_count": len(request.get("tools") or []) if isinstance(request.get("tools"), list) else None,
        "stream_event_count": len(stream_events),
        "preview": preview_from_response(response),
    }


def summarize_input(input_value: Any) -> str:
    if isinstance(input_value, list):
        return f"{len(input_value)} input item(s)"
    if isinstance(input_value, str):
        return f"{len(input_value)} chars input"
    if isinstance(input_value, dict):
        return f"{len(input_value)} input key(s)"
    if input_value is None:
        return "no input"
    return type(input_value).__name__


def compact_request_parameters(request: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in request.items() if key not in {"instructions", "input", "tools"}}


def is_prefix(prefix: Any, full: Any) -> bool:
    if not isinstance(prefix, list) or not isinstance(full, list):
        return False
    if len(prefix) > len(full):
        return False
    return prefix == full[: len(prefix)]


def build_append_view(calls: list[dict[str, Any]], call: dict[str, Any]) -> dict[str, Any]:
    """Build a turn-local view around Hermes' append-only transcript replay."""
    if call.get("api") != "responses.stream":
        return {
            "canonical": False,
            "note": "Append View is defined for canonical responses.stream calls. responses.create records are SDK wrapper duplicates.",
        }

    streams = sorted(
        [item for item in calls if item.get("api") == "responses.stream"],
        key=lambda item: int(item.get("call_index") or 0),
    )
    positions = {item.get("call_index"): index for index, item in enumerate(streams)}
    index = positions.get(call.get("call_index"))
    if index is None:
        return {"canonical": False, "note": "Canonical stream call was not found in this run."}

    request = call.get("request_kwargs") or {}
    current_input = request.get("input") or []
    previous_call = streams[index - 1] if index > 0 else None
    next_call = streams[index + 1] if index + 1 < len(streams) else None

    if previous_call is None:
        incoming_append: Any = {
            "kind": "initial_system_prompt",
            "note": "First canonical call: there is no previous append. This panel shows the static prompt envelope.",
            "request_parameters": compact_request_parameters(request),
            "instructions": request.get("instructions"),
            "tools": request.get("tools"),
            "initial_input": current_input,
        }
        incoming_prefix_ok = True
    else:
        previous_input = (previous_call.get("request_kwargs") or {}).get("input") or []
        incoming_prefix_ok = is_prefix(previous_input, current_input)
        incoming_append = current_input[len(previous_input) :] if incoming_prefix_ok else {
            "error": "Current input is not prefixed by the previous canonical input.",
            "previous_input_items": len(previous_input),
            "current_input_items": len(current_input),
        }

    if next_call is None:
        outgoing_append = None
        outgoing_prefix_ok = None
        outgoing_note = "No next canonical request exists, so the post-response append cannot be observed from a later request."
    else:
        next_input = (next_call.get("request_kwargs") or {}).get("input") or []
        outgoing_prefix_ok = is_prefix(current_input, next_input)
        outgoing_append = next_input[len(current_input) :] if outgoing_prefix_ok else {
            "error": "Next input is not prefixed by this canonical input.",
            "current_input_items": len(current_input),
            "next_input_items": len(next_input),
        }
        outgoing_note = None

    return {
        "canonical": True,
        "call_index": call.get("call_index"),
        "previous_call_index": previous_call.get("call_index") if previous_call else None,
        "next_call_index": next_call.get("call_index") if next_call else None,
        "incoming_prefix_ok": incoming_prefix_ok,
        "outgoing_prefix_ok": outgoing_prefix_ok,
        "current_input_items": len(current_input) if isinstance(current_input, list) else None,
        "incoming_append_items": len(incoming_append) if isinstance(incoming_append, list) else None,
        "outgoing_append_items": len(outgoing_append) if isinstance(outgoing_append, list) else None,
        "incoming_append": incoming_append,
        "this_response": call.get("response"),
        "outgoing_append": outgoing_append,
        "outgoing_note": outgoing_note,
    }


class TraceHandler(BaseHTTPRequestHandler):
    server_version = "HermesTraceViewer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (utc_now(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self.send_html(INDEX_HTML)
            elif parsed.path == "/api/runs":
                self.send_json({"root": str(ROOT_DIR), "runs": list_runs()})
            elif parsed.path.startswith("/api/runs/"):
                self.handle_run_api(parsed.path)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
        except BrokenPipeError:
            pass
        except Exception as exc:  # pragma: no cover - local diagnostic server
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, repr(exc))

    def handle_run_api(self, path: str) -> None:
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "runs":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        run_id = parts[2]
        run_path = safe_run_path(run_id)

        if len(parts) == 4 and parts[3] == "calls":
            calls = load_jsonl(run_path / JSONL_NAME)
            self.send_json({"run": run_summary(run_path), "calls": [summarize_call(call) for call in calls]})
            return

        if len(parts) == 5 and parts[3] == "calls":
            call_index = int(parts[4])
            calls = load_jsonl(run_path / JSONL_NAME)
            for call in calls:
                if call.get("call_index") == call_index:
                    call["_append_view"] = build_append_view(calls, call)
                    self.send_json(call)
                    return
            self.send_error(HTTPStatus.NOT_FOUND, "call not found")
            return

        if len(parts) == 5 and parts[3] == "files":
            file_path = safe_file_path(run_id, parts[4])
            data = file_path.read_bytes()
            content_type = "application/json" if file_path.suffix == ".json" else "text/plain; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def send_json(self, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, document: str) -> None:
        data = document.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def choose_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no open port found from {preferred} to {preferred + 49}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve Hermes raw trace captures in a browser.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="Preferred port. Default: 8765")
    parser.add_argument("--no-open", action="store_true", help="Do not try to open the browser.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    port = choose_port(args.host, args.port)
    server = ThreadingHTTPServer((args.host, port), TraceHandler)
    url = f"http://{args.host}:{port}/"
    print(f"Hermes trace viewer: {url}", flush=True)
    print(f"Capture root: {ROOT_DIR}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping trace viewer.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
