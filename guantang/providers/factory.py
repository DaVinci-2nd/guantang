from .base import BaseProvider


def build_provider(model_def: dict, api_key: str, timeout: int = 120, max_retries: int = 2):
    ptype = model_def.get("provider", "openai")
    base_url = model_def.get("base_url")
    model = model_def.get("model", "")
    if ptype == "mock":
        from .mock import MockProvider

        return MockProvider(model=model)
    return BaseProvider(base_url=base_url, api_key=api_key, model=model, timeout=timeout, max_retries=max_retries)
