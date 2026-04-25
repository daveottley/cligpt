# ai_client.py

import sys
import os
import platform
import datetime
import re
from openai import OpenAI
from config import MAX_CONTEXT_TOKENS, MODEL, FAST_MODEL, PRESENCE_PENALTY, SYSTEM_MESSAGE_FILE
from memory_manager import (
        get_neofetch_output,
        prune_context,
        add_to_context,
        estimate_tokens
)
import time

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def supports_reasoning_effort(model):
    """Return whether the model family accepts the reasoning_effort parameter."""
    return model.startswith(("gpt-5", "o1", "o3", "o4"))

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

def single_query(user_prompt, reasoning_effort="medium", debug=False, model=None):
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

    system_message = load_system_message()
    pruned_context, chat_blocks, topic_tags, oldest_block = prune_context(user_prompt)
    
    def estimate_tokens_local(text):
        return len(text.split())
    system_tokens = estimate_tokens_local(system_message)
    context_tokens = estimate_tokens_local(pruned_context)
    user_tokens = estimate_tokens_local(user_prompt)
    total_context_tokens = system_tokens + context_tokens + user_tokens

    # Build header
    header_basic = f"[{model} - {reasoning_effort}]"
    debug_header = (f"[Context Tokens: {total_context_tokens}]\n"
                    f"  [System Message: {system_tokens}]\n"
                    f"  [Pruned Context: {context_tokens}]\n"
                    f"    [Chat Blocks: {chat_blocks}]\n"
                    f"    [Topic Tags: {topic_tags}]\n"
                    f"    [Oldest Block: {oldest_block}]\n"
                    f"  [User Prompt: {user_tokens}]\n")
    
    if debug:
        sys.stdout.write(header_basic + "\n" + debug_header)
    else:
        sys.stdout.write(header_basic + "\n")
    sys.stdout.flush()
        
    combined_system = system_message + "\n\n" + pruned_context
    messages = [
        {"role": "system", "content": combined_system},
        {"role": "user", "content": user_prompt}
    ]
    
    request_args = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": MAX_CONTEXT_TOKENS,
        "n": 1,
        "presence_penalty": PRESENCE_PENALTY,
        "stream": True,
        "stream_options": {"include_usage": True},
        "store": True
    }
    if supports_reasoning_effort(model):
        request_args["reasoning_effort"] = reasoning_effort

    stream = client.chat.completions.create(**request_args)

    answer_chunks = []
    reasoning_tokens_used = 0

    if debug:
        sys.stdout.write("[streaming response]\n\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()

    for chunk in stream:
        if getattr(chunk, "usage", None):
            completion_details = getattr(chunk.usage, "completion_tokens_details", None)
            reasoning_tokens_used = getattr(completion_details, "reasoning_tokens", 0) or 0
            continue

        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            answer_chunks.append(content)
            sys.stdout.write(content)
            sys.stdout.flush()

    answer_text = "".join(answer_chunks)

    if debug:
        sys.stdout.write(f"\n\n[Reasoning Tokens: {reasoning_tokens_used}]\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()
    
    add_to_context(user_prompt, answer_text, [], reasoning_effort)
    return answer_text
