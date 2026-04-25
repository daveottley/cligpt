# ai_client.py

import sys
import os
import platform
import json
import shutil
import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from openai import OpenAI
from config import MAX_CONTEXT_TOKENS, MODEL, FAST_MODEL, SYSTEM_MESSAGE_FILE, CONTEXT_FILE
from memory_manager import (
        get_neofetch_output,
        prune_context,
        add_to_context,
        save_context_block,
)

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TOPIC_TAG_SCHEMA = {
    "type": "json_schema",
    "name": "topic_tags",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 5 concise topic tags, broad to specific."
            }
        },
        "required": ["topics"],
        "additionalProperties": False
    }
}

def supports_reasoning_effort(model):
    """Return whether the model family accepts the reasoning_effort parameter."""
    return model.startswith(("gpt-5", "o1", "o3", "o4"))

def resolve_output_width(width=None):
    if width:
        return max(1, width), "flag"
    return max(1, shutil.get_terminal_size((80, 20)).columns - 1), "terminal"

def get_nested_attr(obj, *names, default=None):
    current = obj
    for name in names:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return current if current is not None else default

def extract_response_text(response):
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    chunks = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
    return "".join(chunks)

def source_to_text(source):
    if isinstance(source, dict):
        return json.dumps(source, ensure_ascii=False, sort_keys=True)
    if hasattr(source, "model_dump"):
        return json.dumps(source.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    if hasattr(source, "__dict__"):
        return json.dumps(vars(source), ensure_ascii=False, sort_keys=True, default=str)
    return str(source)

def response_output_items(response):
    return get_nested_attr(response, "output", default=[]) or []

def content_items(item):
    return get_nested_attr(item, "content", default=[]) or []

def annotation_items(content):
    return get_nested_attr(content, "annotations", default=[]) or []

def comparable_url(url):
    if not url:
        return url
    parts = urlsplit(url)
    filtered_query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
        ],
        doseq=True,
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, filtered_query, ""))

def collect_final_answer_citations(response):
    citations = []
    seen_urls = set()

    for item in response_output_items(response):
        for content in content_items(item):
            for annotation in annotation_items(content):
                url = get_nested_attr(annotation, "url")
                comparable = comparable_url(url)
                if not url or comparable in seen_urls:
                    continue
                title = get_nested_attr(annotation, "title", default=url)
                citations.append({"title": title, "url": url, "text": source_to_text(annotation)})
                seen_urls.add(comparable)

    return citations

def collect_uncited_web_sources(response, cited_urls):
    sources = []
    seen_urls = {comparable_url(url) for url in cited_urls}

    for item in response_output_items(response):
        action_sources = get_nested_attr(item, "action", "sources", default=[]) or []
        for source in action_sources:
            url = get_nested_attr(source, "url")
            comparable = comparable_url(url)
            if not url or comparable in seen_urls:
                continue
            title = get_nested_attr(source, "title", default=url)
            sources.append({"title": title, "url": url, "text": source_to_text(source)})
            seen_urls.add(comparable)

    return sources

def log_uncited_web_sources(user_prompt, sources):
    if not sources:
        return None

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"[{timestamp}]",
        "Uncited web-search sources returned by the OpenAI web search tool.",
        "These sources were not final-answer citations.",
        f">>> {user_prompt}",
    ]
    for index, source in enumerate(sources, start=1):
        lines.extend([
            f"[{index}]",
            f"Title: {source['title']}",
            f"URL: {source['url']}",
            f"Complete source text: {source['text']}",
        ])
    save_context_block("\n".join(lines))
    return os.path.abspath(CONTEXT_FILE)

class StreamingLineWrapper:
    def __init__(self, width):
        self.width = max(1, width)
        self.line = ""
        self.word = ""

    def feed(self, text):
        output = []
        for char in text:
            if char == "\n":
                self._flush_word(output)
                output.append(self.line.rstrip() + "\n" if self.line else "\n")
                self.line = ""
            elif char.isspace():
                self._flush_word(output)
            else:
                self.word += char
        return "".join(output)

    def finish(self):
        output = []
        self._flush_word(output)
        if self.line:
            output.append(self.line.rstrip())
            self.line = ""
        return "".join(output)

    def _flush_word(self, output):
        if not self.word:
            return

        word = self.word
        self.word = ""

        while len(word) > self.width:
            if self.line:
                output.append(self.line.rstrip() + "\n")
                self.line = ""
            output.append(word[:self.width] + "\n")
            word = word[self.width:]

        if not word:
            return
        if not self.line:
            self.line = word
        elif len(self.line) + 1 + len(word) <= self.width:
            self.line += " " + word
        else:
            output.append(self.line.rstrip() + "\n")
            self.line = word

def load_system_message():
    """Load and format the system message from SYSTEM_MESSAGE_FILE."""
    operating_system = platform.system()
    version = platform.release()
    if operating_system == "Linux":
        try:
            import distro
            distribution = distro.name()
            version = distro.version()
        except ImportError:
            distribution = "Linux"
    else:
        distribution = operating_system
    shell_path = os.getenv("SHELL", "")
    shell = os.path.basename(shell_path) if shell_path else "unknown"
    editor = os.getenv("EDITOR", "unknown")
    with open(SYSTEM_MESSAGE_FILE, "r", encoding="utf-8") as f:
        template = f.read().strip()
    
    # Get neofetch output
    neofetch_info = get_neofetch_output()

    # Format the system message and append neofetch info.
    formatted_message = template.format(
        distribution=distribution,
        operating_system=operating_system,
        version=version,
        shell=shell,
        editor=editor
    ).strip()
    
    return formatted_message + "\n\n" + neofetch_info

def extract_topic_tags(user_prompt, answer_text, model=FAST_MODEL):
    response = client.responses.create(
        model=model,
        instructions=(
            "Extract up to 5 concise topic tags from the user's prompt and "
            "assistant answer. Use broad-to-specific tags. Return only the "
            "structured data requested."
        ),
        input=[
            {
                "role": "user",
                "content": (
                    f"User prompt:\n{user_prompt}\n\n"
                    f"Assistant answer:\n{answer_text}"
                ),
            }
        ],
        max_output_tokens=120,
        text={"format": TOPIC_TAG_SCHEMA},
        store=False,
    )

    raw_content = extract_response_text(response)
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        return []

    topics = parsed.get("topics", [])
    if not isinstance(topics, list):
        return []
    return [str(topic).strip() for topic in topics if str(topic).strip()][:5]

def single_query(
    user_prompt,
    reasoning_effort="medium",
    debug=False,
    model=None,
    width=None,
    web_search=True,
):
    """
    Send a query to the AI using the specified reasoning effort.
    A header is printed at the beginning of each response:
      [<model_name> - <reasoning_effort>]
    The assistant response is streamed to stdout as it is received.
    """
    # Default parameters if not provided.
    if not reasoning_effort:
        reasoning_effort = "medium"
    if not model:
        model = MODEL
    output_width, width_source = resolve_output_width(width)

    system_message = load_system_message()
    pruned_context, chat_blocks, topic_tags, oldest_block = prune_context(user_prompt)
    
    def estimate_tokens_local(text):
        return len(text.split())
    system_tokens = estimate_tokens_local(system_message)
    context_tokens = estimate_tokens_local(pruned_context)
    user_tokens = estimate_tokens_local(user_prompt)
    total_context_tokens = system_tokens + context_tokens + user_tokens

    # Build header
    web_label = "web:on" if web_search else "web:off"
    header_basic = f"[{model} - {reasoning_effort} - {web_label} - width: {output_width} ({width_source})]"
    debug_header = (f"[Context Tokens: {total_context_tokens}]\n"
                    f"  [System Message: {system_tokens}]\n"
                    f"  [Pruned Context: {context_tokens}]\n"
                    f"    [Chat Blocks: {chat_blocks}]\n"
                    f"    [Topic Tags: {topic_tags}]\n"
                    f"    [Oldest Block: {oldest_block}]\n"
                    f"  [User Prompt: {user_tokens}]\n"
                    f"  [Web Search: {'enabled' if web_search else 'disabled'}]\n"
                    f"  [Output Width: {output_width} ({width_source})]\n")
    
    if debug:
        sys.stdout.write(header_basic + "\n" + debug_header)
    else:
        sys.stdout.write(header_basic + "\n")
    sys.stdout.flush()
        
    combined_system = (
        system_message
        + "\n\n"
        + pruned_context
        + "\n\n"
        + (
            f"Format the visible answer for a terminal with a hard maximum "
            f"line length of {output_width} characters. Prefer lines as close "
            f"to {output_width} characters as natural wording allows. Do not "
            f"use lines longer than {output_width} characters."
            f"\n\nWeb search is {'enabled' if web_search else 'disabled'} for "
            f"this request. When web search is enabled, use it for current, "
            f"fast-changing, or source-sensitive facts. If web search is used, "
            f"make source URLs visible in the answer."
        )
    )
    
    request_args = {
        "model": model,
        "instructions": combined_system,
        "input": [{"role": "user", "content": user_prompt}],
        "max_output_tokens": MAX_CONTEXT_TOKENS,
        "stream": True,
        "text": {"format": {"type": "text"}},
        "store": True
    }
    if web_search:
        request_args["tools"] = [{"type": "web_search"}]
        request_args["tool_choice"] = "auto"
        request_args["include"] = ["web_search_call.action.sources"]
    if supports_reasoning_effort(model):
        request_args["reasoning"] = {"effort": reasoning_effort}

    stream = client.responses.create(**request_args)

    visible_chunks = []
    reasoning_tokens_used = 0
    completed_response = None
    wrapper = StreamingLineWrapper(output_width)

    if debug:
        sys.stdout.write("[streaming response]\n\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()

    for event in stream:
        event_type = getattr(event, "type", "")
        if event_type == "response.output_text.delta":
            content = getattr(event, "delta", "")
            visible_text = wrapper.feed(content)
            if visible_text:
                visible_chunks.append(visible_text)
                sys.stdout.write(visible_text)
                sys.stdout.flush()
        elif event_type == "response.refusal.delta":
            content = getattr(event, "delta", "")
            visible_text = wrapper.feed(content)
            if visible_text:
                visible_chunks.append(visible_text)
                sys.stdout.write(visible_text)
                sys.stdout.flush()
        elif event_type == "response.completed":
            completed_response = getattr(event, "response", None)
            reasoning_tokens_used = (
                get_nested_attr(
                    event,
                    "response",
                    "usage",
                    "output_tokens_details",
                    "reasoning_tokens",
                    default=0,
                )
                or 0
            )
        elif event_type in {"response.failed", "error", "response.error"}:
            error = get_nested_attr(event, "response", "error") or getattr(event, "error", None)
            raise RuntimeError(f"OpenAI response stream failed: {error}")

    final_text = wrapper.finish()
    if final_text:
        visible_chunks.append(final_text)
        sys.stdout.write(final_text)

    answer_text = "".join(visible_chunks)

    final_citations = collect_final_answer_citations(completed_response) if completed_response else []
    if final_citations:
        sys.stdout.write("\n\nSources:\n")
        for index, citation in enumerate(final_citations, start=1):
            sys.stdout.write(f"[{index}] {citation['title']}: {citation['url']}\n")

    cited_urls = {citation["url"] for citation in final_citations}
    uncited_sources = (
        collect_uncited_web_sources(completed_response, cited_urls)
        if completed_response else []
    )
    uncited_context_path = log_uncited_web_sources(user_prompt, uncited_sources)
    if uncited_context_path:
        note = (
            "\n*Additional web-search sources were returned but not cited in "
            f"the final answer; they were logged to {uncited_context_path}.*"
        )
        sys.stdout.write(note + "\n")
        answer_text += note

    if debug:
        sys.stdout.write(f"\n\n[Reasoning Tokens: {reasoning_tokens_used}]\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()
    
    try:
        topics = extract_topic_tags(user_prompt, answer_text)
    except Exception as exc:
        if debug:
            sys.stderr.write(f"[Topic tag extraction failed: {exc}]\n")
        topics = []
    add_to_context(user_prompt, answer_text, topics, reasoning_effort)
    return answer_text
