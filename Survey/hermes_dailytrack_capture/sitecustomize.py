from __future__ import annotations

import atexit
import builtins
import datetime as _dt
import json
import os
from pathlib import Path
import threading
import traceback
from typing import Any


_OUT_DIR = Path(os.getenv("HERMES_OPENAI_CAPTURE_DIR", "/tmp/hermes_openai_capture_out")).expanduser()
_OUT_DIR.mkdir(parents=True, exist_ok=True)
_JSONL_PATH = _OUT_DIR / "openai_raw_calls.jsonl"
_AGG_PATH = _OUT_DIR / "openai_raw_calls.json"
_LOCK = threading.RLock()
_CALLS: list[dict[str, Any]] = []
_COUNTER = 0
_PATCHED = False


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _jsonable(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, depth + 1) for item in value]
    for attr in ("model_dump", "to_dict", "dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return _jsonable(method(), depth + 1)
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        try:
            return _jsonable(vars(value), depth + 1)
        except Exception:
            pass
    return repr(value)


def _next_call_id() -> int:
    global _COUNTER
    with _LOCK:
        _COUNTER += 1
        return _COUNTER


def _append_record(record: dict[str, Any]) -> None:
    with _LOCK:
        _CALLS.append(record)
        with _JSONL_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_aggregate() -> None:
    with _LOCK:
        if not _CALLS:
            return
        payload = {
            "created_at": _now(),
            "capture_dir": str(_OUT_DIR),
            "call_count": len(_CALLS),
            "calls": _CALLS,
        }
        tmp = _AGG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_AGG_PATH)


def _base_record(api: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "call_index": _next_call_id(),
        "api": api,
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "started_at": _now(),
        "request_kwargs": _jsonable(kwargs),
    }


def _finish_record(record: dict[str, Any], *, response: Any = None, error: BaseException | None = None) -> None:
    record["finished_at"] = _now()
    if error is not None:
        record["error"] = {
            "type": f"{type(error).__module__}.{type(error).__name__}",
            "message": str(error),
            "traceback": traceback.format_exception(type(error), error, error.__traceback__, limit=8),
        }
    else:
        record["response"] = _jsonable(response)
    _append_record(record)
    _write_aggregate()


class _StreamingIteratorRecorder:
    def __init__(self, wrapped: Any, record: dict[str, Any]) -> None:
        self._wrapped = wrapped
        self._record = record
        self._events: list[Any] = []
        self._closed = False
        response = getattr(wrapped, "response", None)
        if response is not None:
            self._record["http_response_meta"] = _jsonable(
                {
                    "status_code": getattr(response, "status_code", None),
                    "headers": dict(getattr(response, "headers", {}) or {}),
                }
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def __iter__(self):
        try:
            for item in self._wrapped:
                self._events.append(_jsonable(item))
                yield item
        except BaseException as exc:
            self._record["stream_events"] = self._events
            _finish_record(self._record, error=exc)
            self._closed = True
            raise
        else:
            self._record["stream_events"] = self._events
            self._record["response"] = {
                "stream_event_count": len(self._events),
                "note": "streaming response captured as stream_events",
            }
            _finish_record(self._record, response=self._record["response"])
            self._closed = True

    def close(self) -> Any:
        close = getattr(self._wrapped, "close", None)
        if callable(close):
            return close()
        return None


class _ResponsesStreamContextRecorder:
    def __init__(self, wrapped: Any, record: dict[str, Any]) -> None:
        self._wrapped = wrapped
        self._record = record
        self._stream = None
        self._events: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def __enter__(self):
        self._stream = self._wrapped.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        final_response = None
        try:
            if self._stream is not None:
                try:
                    final_response = self._stream.get_final_response()
                except Exception as final_exc:
                    final_response = {
                        "get_final_response_error": {
                            "type": f"{type(final_exc).__module__}.{type(final_exc).__name__}",
                            "message": str(final_exc),
                        }
                    }
            self._record["stream_events"] = self._events
            if exc is not None:
                _finish_record(self._record, error=exc)
            else:
                _finish_record(self._record, response=final_response)
        finally:
            return self._wrapped.__exit__(exc_type, exc, tb)

    def __iter__(self):
        assert self._stream is not None
        for event in self._stream:
            self._events.append(_jsonable(event))
            yield event

    def get_final_response(self):
        if self._stream is None:
            return self._wrapped.get_final_response()
        return self._stream.get_final_response()


def _patch_sync() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    patched_any = False
    try:
        from openai.resources.chat.completions import Completions
        original_chat_create = Completions.create

        def chat_create(self, *args, **kwargs):
            record = _base_record("chat.completions.create", kwargs)
            if args:
                record["request_args"] = _jsonable(args)
            try:
                response = original_chat_create(self, *args, **kwargs)
            except BaseException as exc:
                _finish_record(record, error=exc)
                raise
            if kwargs.get("stream"):
                return _StreamingIteratorRecorder(response, record)
            _finish_record(record, response=response)
            return response

        Completions.create = chat_create
        patched_any = True
    except Exception as exc:
        if type(exc).__name__ != "ModuleNotFoundError":
            _append_record({"api": "patch_error", "target": "chat.completions.create", "error": repr(exc), "created_at": _now()})

    try:
        from openai.resources.responses.responses import Responses
    except Exception:
        try:
            from openai.resources.responses import Responses
        except Exception:
            Responses = None
    if Responses is not None:
        original_responses_create = getattr(Responses, "create", None)
        if callable(original_responses_create):
            def responses_create(self, *args, **kwargs):
                record = _base_record("responses.create", kwargs)
                if args:
                    record["request_args"] = _jsonable(args)
                try:
                    response = original_responses_create(self, *args, **kwargs)
                except BaseException as exc:
                    _finish_record(record, error=exc)
                    raise
                if kwargs.get("stream"):
                    return _StreamingIteratorRecorder(response, record)
                _finish_record(record, response=response)
                return response

            Responses.create = responses_create
            patched_any = True

        original_responses_stream = getattr(Responses, "stream", None)
        if callable(original_responses_stream):
            def responses_stream(self, *args, **kwargs):
                record = _base_record("responses.stream", kwargs)
                if args:
                    record["request_args"] = _jsonable(args)
                try:
                    stream_context = original_responses_stream(self, *args, **kwargs)
                except BaseException as exc:
                    _finish_record(record, error=exc)
                    raise
                return _ResponsesStreamContextRecorder(stream_context, record)

            Responses.stream = responses_stream
            patched_any = True
    if patched_any:
        _PATCHED = True
    return patched_any


_patch_sync()

_ORIGINAL_IMPORT = builtins.__import__


def _capture_import(name, globals=None, locals=None, fromlist=(), level=0):
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    if not _PATCHED and (name == "openai" or name.startswith("openai.")):
        try:
            _patch_sync()
        except Exception:
            pass
    return module


builtins.__import__ = _capture_import
atexit.register(_write_aggregate)
