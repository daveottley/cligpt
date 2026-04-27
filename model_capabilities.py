from dataclasses import dataclass


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

    def supports_reasoning_effort(self, effort):
        return effort in self.reasoning_efforts


class GPT55(ModelCapabilities):
    def __init__(self):
        super().__init__(
            model_id="gpt-5.5",
            display_name="GPT-5.5",
            max_context_tokens=400_000,
            max_output_tokens=100_000,
            safe_input_tokens=250_000,
            reasoning_efforts=("low", "medium", "high", "xhigh"),
            tools=("web_search", "file_search"),
            supports_images=True,
            supports_input_files=True,
            supports_file_search=True,
            supports_web_search=True,
            supports_streaming=True,
            supports_store=True,
            source_url="https://platform.openai.com/docs/models/gpt-5",
            confidence="inferred-from-gpt-5-family",
        )


class GPT54(ModelCapabilities):
    def __init__(self):
        super().__init__(
            model_id="gpt-5.4",
            display_name="GPT-5.4",
            max_context_tokens=400_000,
            max_output_tokens=100_000,
            safe_input_tokens=250_000,
            reasoning_efforts=("low", "medium", "high", "xhigh"),
            tools=("web_search", "file_search"),
            supports_images=True,
            supports_input_files=True,
            supports_file_search=True,
            supports_web_search=True,
            supports_streaming=True,
            supports_store=True,
            source_url="https://platform.openai.com/docs/models/gpt-5",
            confidence="inferred-from-gpt-5-family",
        )


class GPT40(ModelCapabilities):
    def __init__(self):
        super().__init__(
            model_id="gpt-4o",
            display_name="GPT-4o",
            max_context_tokens=128_000,
            max_output_tokens=16_384,
            safe_input_tokens=96_000,
            reasoning_efforts=(),
            tools=("web_search", "file_search"),
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
        )


class GPT40Mini(ModelCapabilities):
    def __init__(self):
        super().__init__(
            model_id="gpt-4o-mini",
            display_name="GPT-4o mini",
            max_context_tokens=128_000,
            max_output_tokens=16_384,
            safe_input_tokens=96_000,
            reasoning_efforts=(),
            tools=("web_search", "file_search"),
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
        )


MODEL_CAPABILITIES = {
    "gpt-5.5": GPT55(),
    "gpt-5.4": GPT54(),
    "gpt-4o": GPT40(),
    "gpt-4.0": GPT40(),
    "gpt-4o-mini": GPT40Mini(),
    "gpt-4.0mini": GPT40Mini(),
    "gpt-4.0-mini": GPT40Mini(),
}


def get_model_capabilities(model_id):
    if model_id in MODEL_CAPABILITIES:
        return MODEL_CAPABILITIES[model_id]
    if model_id.startswith("gpt-5"):
        return GPT55()
    if model_id.startswith("gpt-4o-mini"):
        return GPT40Mini()
    if model_id.startswith("gpt-4o") or model_id.startswith("gpt-4"):
        return GPT40()
    return ModelCapabilities(
        model_id=model_id,
        display_name=model_id,
        max_context_tokens=128_000,
        max_output_tokens=16_384,
        safe_input_tokens=96_000,
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
