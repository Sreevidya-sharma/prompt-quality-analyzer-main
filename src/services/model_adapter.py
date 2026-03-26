import os
import time
from pathlib import Path
from typing import Any

from src.utils.config_loader import load_config
from src.utils.paths import project_root

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[misc, assignment]

_BASE = project_root()
_FALLBACK_RESPONSE = (
    "I could not generate a response right now. Please try again in a moment."
)


def _load_model_config() -> dict[str, Any]:
    cfg = load_config(str(_BASE / "configs" / "base.yaml"))
    return cfg.get("model", {})


def _model_version() -> str:
    mc = _load_model_config()
    return str(mc.get("model_version") or "v1.0")


class ModelAdapter:
    def infer(self, prompt: str) -> dict[str, Any]:
        if not isinstance(prompt, str):
            prompt = str(prompt) if prompt is not None else ""
        prompt = prompt.strip()
        if not prompt:
            prompt = " "

        model_cfg = _load_model_config()
        model_name = model_cfg.get("openai_model", "gpt-4o-mini")
        timeout = float(model_cfg.get("timeout_seconds", 45))
        max_tokens = int(model_cfg.get("max_tokens", 1024))

        mv = _model_version()
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key or OpenAI is None:
            return {
                "response": _FALLBACK_RESPONSE,
                "tokens": len(_FALLBACK_RESPONSE.split()),
                "latency": 0.0,
                "model_version": mv,
            }

        client = OpenAI(api_key=api_key, timeout=timeout)
        t0 = time.perf_counter()
        try:
            completion = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
        except Exception:
            latency = round(time.perf_counter() - t0, 3)
            return {
                "response": _FALLBACK_RESPONSE,
                "tokens": len(_FALLBACK_RESPONSE.split()),
                "latency": latency,
                "model_version": mv,
            }

        latency = round(time.perf_counter() - t0, 3)
        choice = completion.choices[0] if completion.choices else None
        message = choice.message if choice else None
        content = (message.content or "").strip() if message else ""
        if not content:
            content = _FALLBACK_RESPONSE

        usage = completion.usage
        if usage is not None and getattr(usage, "completion_tokens", None) is not None:
            tokens = int(usage.completion_tokens)
        elif usage is not None and getattr(usage, "total_tokens", None) is not None:
            tokens = int(usage.total_tokens)
        else:
            tokens = max(1, len(content.split()))

        return {
            "response": content,
            "tokens": tokens,
            "latency": latency,
            "model_version": mv,
        }


def infer(prompt: str) -> dict[str, Any]:
    return ModelAdapter().infer(prompt)


if __name__ == "__main__":
    adapter = ModelAdapter()
    samples = [
        "Why does sleep matter for cognition?",
        "What is cognitive decline?",
        "Give one brief fact.",
    ]
    for sample in samples:
        print(f"Prompt: {sample}")
        print(adapter.infer(sample))
