from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TypeVar
from urllib import request

from simple_hermes.agent.prompting import PLANNER_SYSTEM_MESSAGE, PromptContext, build_planner_prompt

T = TypeVar("T")


@dataclass
class ToolCall:
    name: str
    argument: str


@dataclass
class PlannerDecision:
    """agent 主循环使用的统一规划结果。

    `requires_edit` 和 `requires_test` 必须来自模型侧 planner，而不是本地
    Python 代码用关键词猜测用户意图。主循环只在 planner 明确声明“本轮需要
    编辑/验证”之后，才强制继续推进编辑或测试步骤。
    """

    kind: str
    text: str
    tool_call: Optional[ToolCall] = None
    requires_edit: bool = False
    requires_test: bool = False


class LLMBackend:
    def plan(self, *, message: str, memory_block: str, history_text: str, tools_text: str) -> PlannerDecision:
        raise NotImplementedError


class OpenAICompatibleBackend(LLMBackend):
    """基于普通 HTTP 的最小 OpenAI-compatible planner 后端。"""

    def __init__(self, base_url: str, api_key: str, model: str, api_mode: str = "chat_completions") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.api_mode = api_mode

    @staticmethod
    def _retry_count() -> int:
        raw = os.getenv("SIMPLE_HERMES_BACKEND_RETRIES", "").strip()
        if not raw:
            return 2
        try:
            return max(0, min(int(raw), 5))
        except ValueError:
            return 2

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        if isinstance(exc, (json.JSONDecodeError, ValueError, KeyError)):
            return False
        text = str(exc).lower()
        retry_markers = (
            "connection",
            "closed connection",
            "incomplete chunked read",
            "timed out",
            "timeout",
            "temporarily",
            "reset by peer",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
        )
        return any(marker in text for marker in retry_markers)

    @classmethod
    def _with_retries(cls, operation: Callable[[], T]) -> T:
        attempts = cls._retry_count() + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
                if attempt >= attempts - 1 or not cls._is_retryable_error(exc):
                    raise
                time.sleep(min(0.5 * (2 ** attempt), 4.0))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _load_planner_json(content: str) -> dict:
        """从可能夹杂解释文本的模型回复里提取第一个 JSON 对象。"""
        candidates = []
        fence_match = re.search(r"```json\s*(.*?)\s*```", content, flags=re.IGNORECASE | re.DOTALL)
        if fence_match:
            candidates.append(fence_match.group(1).strip())
        candidates.append(content.strip())

        decoder = json.JSONDecoder()
        last_error: Optional[json.JSONDecodeError] = None
        for candidate in candidates:
            start = candidate.find("{")
            if start < 0:
                continue
            try:
                data, _ = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError as e:
                last_error = e
                continue
            if not isinstance(data, dict):
                raise ValueError("Planner response JSON must be an object.")
            return data
        if last_error is not None:
            raise last_error
        raise ValueError("Planner response did not contain a JSON object.")

    @staticmethod
    def _parse_response_text(content: str) -> PlannerDecision:
        """把不同后端返回的文本统一转换成 agent 主循环认识的决策类型。"""
        data = OpenAICompatibleBackend._load_planner_json(content)
        kind = str(data.get("kind", "text")).strip()
        text = str(data.get("text", "")).strip()
        requires_edit = bool(data.get("requires_edit", False))
        requires_test = bool(data.get("requires_test", False))
        if kind == "tool_call":
            tool = str(data.get("tool", "")).strip()
            argument = str(data.get("argument", "")).strip()
            return PlannerDecision(
                kind="tool_call",
                text=text or f"Use tool {tool}",
                tool_call=ToolCall(tool, argument),
                requires_edit=requires_edit,
                requires_test=requires_test,
            )
        return PlannerDecision(
            kind="text",
            text=text or "The model returned a text decision.",
            tool_call=None,
            requires_edit=requires_edit,
            requires_test=requires_test,
        )

    def _chat_completions_plan(self, prompt: str) -> PlannerDecision:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": PLANNER_SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = raw["choices"][0]["message"]["content"]
        return self._parse_response_text(content)

    def _responses_plan(self, prompt: str) -> PlannerDecision:
        body = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": PLANNER_SYSTEM_MESSAGE}]},
                {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
            ],
        }
        req = request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = raw.get("output_text", "")
        if not content and "output" in raw:
            for item in raw.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") in {"output_text", "text"}:
                            content += c.get("text", "")
        return self._parse_response_text(content)

    def plan(self, *, message: str, memory_block: str, history_text: str, tools_text: str) -> PlannerDecision:
        prompt = build_planner_prompt(
            PromptContext(
                message=message,
                memory_block=memory_block,
                history_text=history_text,
                tools_text=tools_text,
            )
        )
        if self.api_mode == "codex_responses":
            return self._with_retries(lambda: self._responses_plan(prompt))
        return self._with_retries(lambda: self._chat_completions_plan(prompt))


class HermesRuntimeBackend(LLMBackend):
    def __init__(self, client, model: str, extract_content_fn) -> None:
        self.client = client
        self.model = model
        self._extract_content = extract_content_fn

    def plan(self, *, message: str, memory_block: str, history_text: str, tools_text: str) -> PlannerDecision:
        prompt = build_planner_prompt(
            PromptContext(
                message=message,
                memory_block=memory_block,
                history_text=history_text,
                tools_text=tools_text,
            )
        )
        def request_once() -> PlannerDecision:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PLANNER_SYSTEM_MESSAGE},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            content = self._extract_content(response)
            return OpenAICompatibleBackend._parse_response_text(content)

        return OpenAICompatibleBackend._with_retries(request_once)


def _detect_hermes_repo_root() -> Optional[str]:
    explicit = os.getenv("SIMPLE_HERMES_HERMES_ROOT", "").strip()
    if explicit:
        return explicit
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "hermes-agent",
        here.parents[2].parent / "hermes-agent",
        Path.home() / "Desktop/work/hermes-agent",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _backend_from_hermes_runtime() -> Optional[LLMBackend]:
    repo_root = _detect_hermes_repo_root()
    if not repo_root:
        raise RuntimeError("Could not locate the original Hermes repository for hermes-runtime backend mode.")
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from pathlib import Path as _Path
    from hermes_constants import get_hermes_home
    from hermes_cli.env_loader import load_hermes_dotenv
    import yaml
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from agent.auxiliary_client import resolve_provider_client, extract_content_or_reasoning

    hermes_home = get_hermes_home()
    load_hermes_dotenv(hermes_home=hermes_home, project_env=_Path(repo_root) / '.env')

    cfg = {}
    cfg_path = hermes_home / 'config.yaml'
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    model_cfg = cfg.get('model', {}) if isinstance(cfg, dict) else {}
    cfg_provider = ''
    cfg_base_url = ''
    cfg_model = ''
    if isinstance(model_cfg, dict):
        cfg_provider = str(model_cfg.get('provider') or '').strip()
        cfg_base_url = str(model_cfg.get('base_url') or '').strip()
        cfg_model = str(model_cfg.get('default') or model_cfg.get('model') or '').strip()
    elif isinstance(model_cfg, str):
        cfg_model = model_cfg.strip()

    requested = os.getenv('SIMPLE_HERMES_PROVIDER', '').strip() or cfg_provider or 'auto'
    explicit_base_url = os.getenv('SIMPLE_HERMES_BASE_URL', '').strip() or cfg_base_url or None
    runtime = resolve_runtime_provider(requested=requested, explicit_base_url=explicit_base_url)
    runtime_provider = str(runtime.get('provider') or '').strip().lower()

    if not runtime.get('api_key'):
        try:
            fallback_runtime = resolve_runtime_provider(requested='openai-codex')
            if fallback_runtime.get('api_key'):
                runtime = fallback_runtime
                runtime_provider = str(runtime.get('provider') or '').strip().lower()
        except Exception:
            pass

    model = os.getenv('SIMPLE_HERMES_MODEL', '').strip() or cfg_model or os.getenv('HERMES_MODEL', '').strip()
    if not model:
        model = 'gpt-5.4' if runtime_provider == 'openai-codex' else 'gpt-4o-mini'
    if runtime_provider == 'openai-codex' and not any(tok in model.lower() for tok in ('gpt', 'codex', 'o3', 'o4')):
        model = 'gpt-5.4'

    client, resolved_model = resolve_provider_client(
        runtime.get('provider', 'auto'),
        model=model,
        explicit_base_url=runtime.get('base_url', ''),
        explicit_api_key=runtime.get('api_key', ''),
        api_mode=runtime.get('api_mode', 'chat_completions'),
        raw_codex=False,
    )
    if client is None:
        raise RuntimeError('Hermes runtime bridge could not construct a usable client.')
    return HermesRuntimeBackend(client=client, model=resolved_model or model, extract_content_fn=extract_content_or_reasoning)


def backend_from_env() -> Optional[LLMBackend]:
    mode = os.getenv("SIMPLE_HERMES_BACKEND", "rule").strip().lower()
    if mode in {"hermes", "hermes-runtime"}:
        return _backend_from_hermes_runtime()
    if mode not in {"openai", "openai-compatible", "llm"}:
        return None
    base_url = os.getenv("SIMPLE_HERMES_BASE_URL", "").strip()
    api_key = os.getenv("SIMPLE_HERMES_API_KEY", "").strip()
    model = os.getenv("SIMPLE_HERMES_MODEL", "").strip()
    api_mode = os.getenv("SIMPLE_HERMES_API_MODE", "chat_completions").strip() or "chat_completions"
    if not (base_url and api_key and model):
        raise RuntimeError(
            "SIMPLE_HERMES_BACKEND is enabled but SIMPLE_HERMES_BASE_URL, SIMPLE_HERMES_API_KEY, and SIMPLE_HERMES_MODEL are required."
        )
    return OpenAICompatibleBackend(base_url=base_url, api_key=api_key, model=model, api_mode=api_mode)
