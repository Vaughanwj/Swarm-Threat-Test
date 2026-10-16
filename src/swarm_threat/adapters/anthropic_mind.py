"""AgentMind adapter backed by the Claude API.

One request per decision, using structured outputs so the answer is always schema-valid JSON.
The system prompt is the same for every call in a run, so it is marked for prompt caching.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Sequence

import anthropic

from .. import cost
from ..core.ports import Decision, DecisionRequest, EventLog
from . import prompting

CACHE_WARM_SECONDS = 240  # treat a cached prefix as cold after this long (the cache lives 5 minutes)


class BudgetExceeded(RuntimeError):
    pass


class AnthropicMind:
    def __init__(
        self,
        system_text: str,
        log: EventLog,
        model: str = "claude-haiku-5-5",
        effort: str = "low",
        max_tokens: int = 8000,
        concurrency: int = 10,
        timeout: float = 120.0,
        max_retries: int = 4,
        attempts: int = 2,
        workspace_id: str | None = None,
        budget_usd: float | None = None,
        prices: dict | None = None,
        warm_cache: bool = True,
    ) -> None:
        # Keys that are not scoped to a workspace must name one on every request.
        workspace_id = workspace_id or os.environ.get("ANTHROPIC_WORKSPACE_ID")
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self.client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries, default_headers=headers)
        self.system_text = system_text
        self.log = log
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.concurrency = concurrency
        self.attempts = attempts
        self.budget_usd = budget_usd
        self.price = cost.price_for(prices or {}, model)
        self.warm_cache = warm_cache
        self.spent_usd = 0.0
        self._lock = threading.Lock()
        self._last_seen: dict[str, float] = {}

    def decide_batch(self, requests: Sequence[DecisionRequest]) -> list[Decision]:
        if len(requests) <= 1 or self.concurrency <= 1:
            return [self.decide(r) for r in requests]
        out: list[Decision] = []
        rest = list(requests)
        kind = rest[0].kind
        if self.warm_cache and time.monotonic() - self._last_seen.get(kind, -1e9) > CACHE_WARM_SECONDS:
            # Each decision kind has its own cached prefix (the output schema is part of it). Send one
            # request alone so the others read the cache instead of all writing it at once.
            out.append(self.decide(rest.pop(0)))
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(rest))) as pool:
            out.extend(pool.map(self.decide, rest))
        self._last_seen[kind] = time.monotonic()
        return out

    def decide(self, req: DecisionRequest) -> Decision:
        user_text = prompting.user_prompt(req)
        schema = prompting.schema(req)
        for attempt in range(1, self.attempts + 1):
            if self.budget_usd is not None and self.spent_usd >= self.budget_usd:
                raise BudgetExceeded(f"spent ${self.spent_usd:.4f} of a ${self.budget_usd:.2f} budget")
            started = time.monotonic()
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=[{"type": "text", "text": self.system_text, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user_text}],
                    output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                )
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                    anthropic.NotFoundError, anthropic.BadRequestError):
                raise  # configuration problems: stop the run rather than fill it with fallbacks
            except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                self._log_call(req, attempt, started, user_text, error=f"{type(e).__name__}: {e}")
                continue

            self._log_call(req, attempt, started, user_text, response=response)
            if response.stop_reason in ("refusal", "max_tokens"):
                continue
            text = next((b.text for b in response.content if b.type == "text"), None)
            try:
                return prompting.parse(req, json.loads(text or ""))
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
                self.log.record("parse_error", agent=req.agent_id, kind=req.kind, error=str(e), text=(text or "")[:500])
        return prompting.fallback(req)

    def _log_call(self, req: DecisionRequest, attempt: int, started: float, user_text: str,
                  response: Any = None, error: str | None = None) -> None:
        row: dict[str, Any] = {
            "kind": req.kind, "agent": req.agent_id, "generation": req.world.generation,
            "round": req.world.round, "model": self.model, "effort": self.effort, "attempt": attempt,
            "latency_s": round(time.monotonic() - started, 3), "user_chars": len(user_text),
            "system_chars": len(self.system_text),
        }
        if response is not None:
            u = response.usage
            row.update(
                stop_reason=response.stop_reason,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                request_id=getattr(response, "_request_id", None),
            )
            usd = cost.call_cost(row, self.price)
            with self._lock:
                self.spent_usd += usd
                row["usd"] = round(usd, 6)
                row["spent_usd"] = round(self.spent_usd, 6)
        if error:
            row["error"] = error
        self.log.record("llm_call", **row)
