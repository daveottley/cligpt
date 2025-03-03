# ai_client.py

import sys
import os
import platform
import datetime
import json
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

# JSON schema for structured outputs
RESPONSE_SCHEMA = {
    "name": "StructuredOutput",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "The answer text."},
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of up to 5 unique topic tags (from broad to specific)."
            },
            "reasoning_tokens": {
                "type": "integer",
                "description": "A non-negative integer indicating the reasoning tokens used."
            }
        },
        "required": ["answer", "topics", "reasoning_tokens"],
        "additionalProperties": False
    }
}

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

def single_query(user_prompt, reasoning_effort="medium", debug=False):
    """
    Send a query to the AI using the specified reasoning effort.
    A header is printed at the beginning of each response:
      [<model_name> - <reasoning_effort>]
    Streaming is disabled.
    """
    # Default reasoning_effort to "medium" if not provided.
    if not reasoning_effort:
        reasoning_effort = "medium"

    system_message = load_system_message()
    pruned_context, chat_blocks, topic_tags, oldest_block = prune_context(user_prompt)
    
    def estimate_tokens_local(text):
        return len(text.split())
    system_tokens = estimate_tokens_local(system_message)
    context_tokens = estimate_tokens_local(pruned_context)
    user_tokens = estimate_tokens_local(user_prompt)
    total_context_tokens = system_tokens + context_tokens + user_tokens

    # Build header
    header_basic = f"[{MODEL} - {reasoning_effort}]"
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
    
    # Always disable streaming.
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_completion_tokens=MAX_CONTEXT_TOKENS,
        response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
        n=1,
        presence_penalty=PRESENCE_PENALTY,
        stream=False,
        store=True
    )
    
    raw_content = response.choices[0].message.content
    
    if raw_content is None:
        raw_content = ""
    try:
        structured_output = json.loads(raw_content)
    except json.JSONDecodeError:
        structured_output = {
            "answer": raw_content,
            "topics": [],
            "reasoning_tokens": 0
        }
        
    answer_text = structured_output.get("answer", "")
    topics = structured_output.get("topics", [])
    reasoning_tokens_used = structured_output.get("reasoning_tokens", 0)
    
    if debug:
        header2 = f"[Reasoning Tokens: {reasoning_tokens_used}]\n"
        full_output = header2 + "\n" + answer_text
    else:
        full_output = answer_text

    sys.stdout.write("\n" + full_output + "\n")
    sys.stdout.flush()
    
    add_to_context(user_prompt, answer_text, topics, reasoning_effort)
    return full_output

