import json
import os
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from config import STATE_DIR


OFFICIAL_MODEL_COMPARE_URL = "https://developers.openai.com/api/docs/models/compare"
MODEL_LIMIT_CACHE_SECONDS = 3 * 24 * 60 * 60
SAFE_INPUT_CONTEXT_FRACTION = 0.80
RECENT_HISTORY_FRACTION = 0.20
RECENT_HISTORY_MIN_TOKENS = 4_000
RECENT_HISTORY_MAX_TOKENS = 120_000


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_data(self, data):
        text = " ".join(data.split())
        if text:
            self.items.append(text)


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    display_name: str
    max_context_tokens: int
    max_output_tokens: int
    safe_input_tokens: int
    reasoning_efforts: tuple[str, ...]
    tools: tuple[str, ...]
    supports_images: bool
    supports_input_files: bool
    supports_file_search: bool
    supports_web_search: bool
    supports_streaming: bool
    supports_store: bool
    default_reasoning_effort: str = "medium"
    recommended_max_direct_files: int = 10
    default_directory_strategy: str = "file_search"
    source_url: str = ""
    confidence: str = "manual"
    input_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    output_price_per_million: float | None = None

    def supports_reasoning_effort(self, effort):
        return effort in self.reasoning_efforts


def state_root():
    root = os.getenv("GPT_HOME") or os.getcwd()
    path = os.path.join(root, STATE_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def model_limit_cache_path():
    return os.path.join(state_root(), "openai_model_limits.json")


def parse_number(text):
    if not text or text == "-":
        return None
    try:
        return int(str(text).replace(",", ""))
    except ValueError:
        return None


def parse_price(text):
    if not text or text == "-":
        return None
    try:
        return float(str(text).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def display_name_to_model_id(display_name):
    return display_name.strip().lower().replace(" ", "-")


def parse_model_compare_html(html):
    parser = TextExtractor()
    parser.feed(html)
    items = parser.items
    models = {}
    for index, item in enumerate(items):
        if not item.startswith("GPT-"):
            continue
        section = items[index:index + 80]
        if "Context" not in section or "Max Output Tokens" not in section:
            continue
        try:
            context_index = section.index("Context")
            if section[context_index + 1] == "Window":
                context_window = parse_number(section[context_index + 2])
            else:
                context_window = parse_number(section[context_index + 1])
            max_output = parse_number(section[section.index("Max Output Tokens") + 1])
        except (ValueError, IndexError):
            continue
        if not context_window or not max_output:
            continue

        model_id = display_name_to_model_id(item)
        record = {
            "display_name": item,
            "max_context_tokens": context_window,
            "max_output_tokens": max_output,
            "source_url": OFFICIAL_MODEL_COMPARE_URL,
        }
        try:
            pricing_index = section.index("Pricing")
            input_index = section.index("Input", pricing_index)
            cached_index = section.index("Cached Input", pricing_index)
            output_index = section.index("Output", cached_index)
            record["input_price_per_million"] = parse_price(section[input_index + 1])
            record["cached_input_price_per_million"] = parse_price(section[cached_index + 1])
            record["output_price_per_million"] = parse_price(section[output_index + 1])
        except (ValueError, IndexError):
            pass
        models[model_id] = record
    return models


def fetch_official_model_limits(timeout=10):
    request = Request(
        OFFICIAL_MODEL_COMPARE_URL,
        headers={"User-Agent": "cligpt/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", "replace")
    return parse_model_compare_html(html)


def read_model_limit_cache():
    path = model_limit_cache_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_model_limit_cache(models):
    payload = {
        "fetched_at": time.time(),
        "source_url": OFFICIAL_MODEL_COMPARE_URL,
        "models": models,
    }
    with open(model_limit_cache_path(), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return payload


def load_official_model_limits():
    cached = read_model_limit_cache()
    if cached and time.time() - (cached.get("fetched_at") or 0) < MODEL_LIMIT_CACHE_SECONDS:
        return cached.get("models", {}) or {}
    try:
        models = fetch_official_model_limits()
    except Exception:
        return (cached or {}).get("models", {}) or {}
    if models:
        return write_model_limit_cache(models).get("models", {})
    return (cached or {}).get("models", {}) or {}


def safe_input_from_limits(max_context_tokens, max_output_tokens):
    usable_input = max(max_context_tokens - max_output_tokens, 1)
    return max(1, int(usable_input * SAFE_INPUT_CONTEXT_FRACTION))


def apply_official_limits(capabilities, official):
    if not official:
        return capabilities
    max_context = official.get("max_context_tokens") or capabilities.max_context_tokens
    max_output = official.get("max_output_tokens") or capabilities.max_output_tokens
    return ModelCapabilities(
        model_id=capabilities.model_id,
        display_name=official.get("display_name") or capabilities.display_name,
        max_context_tokens=max_context,
        max_output_tokens=max_output,
        safe_input_tokens=safe_input_from_limits(max_context, max_output),
        reasoning_efforts=capabilities.reasoning_efforts,
        tools=capabilities.tools,
        supports_images=capabilities.supports_images,
        supports_input_files=capabilities.supports_input_files,
        supports_file_search=capabilities.supports_file_search,
        supports_web_search=capabilities.supports_web_search,
        supports_streaming=capabilities.supports_streaming,
        supports_store=capabilities.supports_store,
        default_reasoning_effort=capabilities.default_reasoning_effort,
        recommended_max_direct_files=capabilities.recommended_max_direct_files,
        default_directory_strategy=capabilities.default_directory_strategy,
        source_url=official.get("source_url") or capabilities.source_url,
        confidence="official-openai-docs-cache",
        input_price_per_million=(
            official.get("input_price_per_million")
            if official.get("input_price_per_million") is not None
            else capabilities.input_price_per_million
        ),
        cached_input_price_per_million=(
            official.get("cached_input_price_per_million")
            if official.get("cached_input_price_per_million") is not None
            else capabilities.cached_input_price_per_million
        ),
        output_price_per_million=(
            official.get("output_price_per_million")
            if official.get("output_price_per_million") is not None
            else capabilities.output_price_per_million
        ),
    )


class GPT55(ModelCapabilities):
    def __init__(self):
        super().__init__(
            model_id="gpt-5.5",
            display_name="GPT-5.5",
            max_context_tokens=1_050_000,
            max_output_tokens=128_000,
            safe_input_tokens=safe_input_from_limits(1_050_000, 128_000),
            reasoning_efforts=("low", "medium", "high", "xhigh"),
            tools=("web_search", "file_search", "get_system_profile"),
            supports_images=True,
            supports_input_files=True,
            supports_file_search=True,
            supports_web_search=True,
            supports_streaming=True,
            supports_store=True,
            source_url=OFFICIAL_MODEL_COMPARE_URL,
            confidence="official-openai-docs-fallback",
            input_price_per_million=5.00,
            cached_input_price_per_million=0.50,
            output_price_per_million=30.00,
        )


class GPT54(ModelCapabilities):
    def __init__(self):
        super().__init__(
            model_id="gpt-5.4",
            display_name="GPT-5.4",
            max_context_tokens=1_050_000,
            max_output_tokens=128_000,
            safe_input_tokens=safe_input_from_limits(1_050_000, 128_000),
            reasoning_efforts=("low", "medium", "high", "xhigh"),
            tools=("web_search", "file_search", "get_system_profile"),
            supports_images=True,
            supports_input_files=True,
            supports_file_search=True,
            supports_web_search=True,
            supports_streaming=True,
            supports_store=True,
            source_url=OFFICIAL_MODEL_COMPARE_URL,
            confidence="official-openai-docs-fallback",
            input_price_per_million=2.50,
            cached_input_price_per_million=0.25,
            output_price_per_million=15.00,
        )


class GPT40(ModelCapabilities):
    def __init__(self):
        super().__init__(
            model_id="gpt-4o",
            display_name="GPT-4o",
            max_context_tokens=128_000,
            max_output_tokens=16_384,
            safe_input_tokens=safe_input_from_limits(128_000, 16_384),
            reasoning_efforts=(),
            tools=("web_search", "file_search", "get_system_profile"),
            supports_images=True,
            supports_input_files=True,
            supports_file_search=True,
            supports_web_search=True,
            supports_streaming=True,
            supports_store=True,
            default_reasoning_effort="",
            recommended_max_direct_files=5,
            source_url="https://platform.openai.com/docs/models",
            confidence="manual",
            input_price_per_million=2.50,
            cached_input_price_per_million=1.25,
            output_price_per_million=10.00,
        )


class GPT40Mini(ModelCapabilities):
    def __init__(self):
        super().__init__(
            model_id="gpt-4o-mini",
            display_name="GPT-4o mini",
            max_context_tokens=128_000,
            max_output_tokens=16_384,
            safe_input_tokens=safe_input_from_limits(128_000, 16_384),
            reasoning_efforts=(),
            tools=("web_search", "file_search", "get_system_profile"),
            supports_images=True,
            supports_input_files=True,
            supports_file_search=True,
            supports_web_search=True,
            supports_streaming=True,
            supports_store=True,
            default_reasoning_effort="",
            recommended_max_direct_files=5,
            source_url="https://platform.openai.com/docs/models",
            confidence="manual",
            input_price_per_million=0.15,
            cached_input_price_per_million=0.075,
            output_price_per_million=0.60,
        )


BASE_MODEL_CAPABILITIES = {
    "gpt-5.5": GPT55(),
    "gpt-5.4": GPT54(),
    "gpt-4o": GPT40(),
    "gpt-4.0": GPT40(),
    "gpt-4o-mini": GPT40Mini(),
    "gpt-4.0mini": GPT40Mini(),
    "gpt-4.0-mini": GPT40Mini(),
}


def base_model_capabilities(model_id):
    if model_id in BASE_MODEL_CAPABILITIES:
        return BASE_MODEL_CAPABILITIES[model_id]
    if model_id.startswith("gpt-5"):
        fallback = GPT55()
        return ModelCapabilities(
            **{
                **fallback.__dict__,
                "model_id": model_id,
                "display_name": model_id,
            }
        )
    if model_id.startswith("gpt-4o-mini"):
        return GPT40Mini()
    if model_id.startswith("gpt-4o") or model_id.startswith("gpt-4"):
        return GPT40()
    return ModelCapabilities(
        model_id=model_id,
        display_name=model_id,
        max_context_tokens=128_000,
        max_output_tokens=16_384,
        safe_input_tokens=safe_input_from_limits(128_000, 16_384),
        reasoning_efforts=(),
        tools=(),
        supports_images=False,
        supports_input_files=True,
        supports_file_search=False,
        supports_web_search=False,
        supports_streaming=True,
        supports_store=True,
        default_reasoning_effort="",
        recommended_max_direct_files=3,
        default_directory_strategy="direct",
        confidence="fallback",
    )


def get_model_capabilities(model_id):
    capabilities = base_model_capabilities(model_id)
    official_limits = load_official_model_limits()
    official = official_limits.get(model_id)
    if not official and model_id in {"gpt-4.0", "gpt-4.0mini", "gpt-4.0-mini"}:
        official = official_limits.get(capabilities.model_id)
    return apply_official_limits(capabilities, official)


def get_recent_history_token_budget(model_id):
    capabilities = get_model_capabilities(model_id)
    budget = int(capabilities.safe_input_tokens * RECENT_HISTORY_FRACTION)
    budget = max(RECENT_HISTORY_MIN_TOKENS, budget)
    budget = min(RECENT_HISTORY_MAX_TOKENS, budget)
    return min(capabilities.safe_input_tokens, budget)
