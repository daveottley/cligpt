# memory_manager.py

import sys
import os
import datetime
import re
import json
import subprocess
import uuid
from config import MODEL, CONTEXT_FILE, PERMANENT_MEMORY_FILE, DELIMITER
from model_capabilities import get_recent_history_token_budget

REQUIRED_PERMANENT_MEMORIES = ["name", "topics_of_interest"]

def get_neofetch_output():
    try:
        # Run neofetch with the --stdout flag to capture its output.
        output = subprocess.check_output(["neofetch", "--stdout"], text=True)
        return output
    except Exception as e:
        return f"Neofetch output unavailable: {e}"

def estimate_tokens(text):
    """Estimate token count by splitting text on whitespace."""
    return len(text.split())

def load_context_blocks():
    """Load context blocks from CONTEXT_FILE as a list of blocks."""
    if not os.path.exists(CONTEXT_FILE):
        return []
    with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
    return content.split(DELIMITER) if content else []

def strip_context_metadata_from_answer(answer_text):
    """Remove display-only footers before reusing history as model context."""
    text = answer_text.strip()
    for marker in ("### Token Usage", "Token Usage:", "Sources:"):
        if text.startswith(marker):
            return ""
        index = text.find("\n" + marker)
        if index != -1:
            text = text[:index].rstrip()
    note_index = text.find("\n*Additional web-search sources were returned")
    if note_index != -1:
        text = text[:note_index].rstrip()
    return text

def context_block_to_conversation_text(block):
    """
    Convert a stored context block into the minimal conversation text that should
    be sent back to the model. context.txt remains a full audit log; this strips
    timestamps, response IDs, topic tags, usage, sources, and other display-only
    metadata from the prompt-side copy.
    """
    lines = block.strip().splitlines()
    user_index = None
    for index, line in enumerate(lines):
        if line.startswith(">>> "):
            user_index = index
            break
    if user_index is None:
        return ""

    user_text = lines[user_index][4:].strip()
    answer_lines = lines[user_index + 1:]
    if answer_lines and answer_lines[-1].startswith("Topic Tags:"):
        answer_lines = answer_lines[:-1]

    answer_text = "\n".join(answer_lines).strip()
    model_prefix = re.match(r"^\[[^\]]+\]\s*(.*)$", answer_text, flags=re.S)
    if model_prefix:
        answer_text = model_prefix.group(1).strip()
    answer_text = strip_context_metadata_from_answer(answer_text)

    parts = []
    if user_text:
        parts.append(f"User: {user_text}")
    if answer_text:
        parts.append(f"Assistant: {answer_text}")
    return "\n".join(parts)

def save_context_block(block):
    """Append a block to CONTEXT_FILE."""
    with open(CONTEXT_FILE, "a", encoding="utf-8") as f:
        f.write(block + DELIMITER)

def load_permanent_memories():
    """Load permanent memories from PERMANENT_MEMORY_FILE as a list of entries."""
    if not os.path.exists(PERMANENT_MEMORY_FILE):
        return []
    with open(PERMANENT_MEMORY_FILE, "r", encoding="utf-8") as f:
        try:
            memories = json.load(f)
            return memories if isinstance(memories, list) else []
        except json.JSONDecodeError:
            return []

def save_permanent_memories(memories):
    """Save the list of permanent memories to PERMANENT_MEMORY_FILE."""
    with open(PERMANENT_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memories, f, indent=2)

def add_permanent_memory(memory_data):
    # If a string is passed, require a colon-separated key-value pair.
    if isinstance(memory_data, str):
        if ':' not in memory_data:
            raise ValueError("Permanent memory must be provided in 'key: value' format.\n"
                  "Try 'subject: full memory', like 'age: I am 39 years old.'\n"
                  "Memory discarded.")
        key, value = memory_data.split(':', 1)
        key = key.strip()
        value = value.strip()
        memory_data = {key: value}
    
    memories = load_permanent_memories()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {"id": len(memories) + 1, "timestamp": timestamp}
    entry.update(memory_data)
    memories.append(entry)
    save_permanent_memories(memories)
    return entry

def ensure_required_permanent_memories():
    memories = load_permanent_memories()
    for key in REQUIRED_PERMANENT_MEMORIES:
        # Check if any memory entry contains this key.
        if not any(key in mem for mem in memories):
            # If not, prompt the user for the value.
            value = input(f"Permanent memory for '{key}' not found. Please provide your {key}: ")
            add_permanent_memory({key: value})
            print(f"Added permanent memory for '{key}'.")

def view_permanent_memory():
    """Return a list of permanent memory entries as formatted strings."""
    memories = load_permanent_memories()
    lines = []
    for mem in memories:
        display_text = mem.get("text")
        if not display_text:
            # Combine all keys except 'id' and 'timestamp'
            display_text = "; ".join(f"{k}: {v}" for k, v in mem.items() if k not in ("id", "timestamp"))
        lines.append(f"[{mem['id']}] ({mem['timestamp']}) {display_text}")
    return lines

def forget_permanent_memory(entry_id):
    """Remove a permanent memory entry by its id."""
    memories = load_permanent_memories()
    new_memories = [mem for mem in memories if mem["id"] != entry_id]
    # Reassign IDs sequentially
    for idx, mem in enumerate(new_memories, start=1):
        mem["id"] = idx
    save_permanent_memories(new_memories)
    return new_memories

def export_permanent_memory(output_file):
    """Export permanent memories to the specified file in JSON format."""
    memories = load_permanent_memories()
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(memories, f, indent=2)
    return output_file

def prune_context(user_prompt, model=MODEL, token_budget=None, include_metadata=False):
    """
    Assemble context for the AI prompt in prioritized order:
      1. Permanent memories (always included)
      2. Recent context blocks (within the last hour)
    Older blocks can be added if there’s remaining token space.
    By default, recent blocks are sanitized to conversation text only. When
    include_metadata is true, selected raw context.txt blocks are included.
    
    Returns:
      pruned_context (str), count of selected non-permanent blocks,
      a dummy aggregated topic_tags, and the oldest timestamp found.
    """
    now = datetime.datetime.now()

    # Load permanent memories and format them as context blocks.
    permanent_memories = load_permanent_memories()
    perm_texts = []
    for mem in permanent_memories:
        if "text" in mem:
            text_value = mem["text"]
        else:
            # Combine any keys besides 'id' and 'timestamp'
            other_keys = [f"{k}: {v}" for k, v in mem.items() if k not in ("id", "timestamp")]
            text_value = "; ".join(other_keys)
        perm_texts.append(f"[{mem['timestamp']}] (PERMANENT) {text_value}")
    permanent_context = "\n".join(perm_texts)

    blocks = load_context_blocks()
    selected_blocks = []
    accumulated_tokens = estimate_tokens(permanent_context)
    max_context_tokens = token_budget or get_recent_history_token_budget(model)

    for block in blocks:
        # Check if block is recent (within last hour)
        is_recent = False
        m_time = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", block)
        if m_time:
            try:
                block_time = datetime.datetime.strptime(m_time.group(1), "%Y-%m-%d %H:%M:%S")
                if now - block_time <= datetime.timedelta(hours=1):
                    is_recent = True
            except Exception:
                pass
        if is_recent:
            conversation_block = block.strip() if include_metadata else context_block_to_conversation_text(block)
            if not conversation_block:
                continue
            tokens = estimate_tokens(conversation_block)
            if accumulated_tokens + tokens > max_context_tokens:
                break
            selected_blocks.append(conversation_block)
            accumulated_tokens += tokens

    # Assemble final context: permanent memories come first.
    pruned_context = permanent_context
    if permanent_context:
        pruned_context += "\n" + DELIMITER
    pruned_context += DELIMITER.join(selected_blocks)

    # Dummy topic tags and oldest timestamp calculation.
    topic_tags = "permanent, recent" if selected_blocks else "permanent"
    oldest_timestamp = "None"
    if selected_blocks:
        times = []
        for block in selected_blocks:
            m_time = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", block)
            if m_time:
                try:
                    t = datetime.datetime.strptime(m_time.group(1), "%Y-%m-%d %H:%M:%S")
                    times.append(t)
                except Exception:
                    pass
        if times:
            oldest_timestamp = min(times).strftime("%Y-%m-%d %H:%M:%S")
    return pruned_context, len(selected_blocks), topic_tags, oldest_timestamp

def add_to_context(user_prompt, answer_text, topics, reasoning_effort="medium", response_id=None):
    """
    Append a new conversation block to the context file.
    """
    if response_id is None:
        response_id = str(uuid.uuid4())
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    topics_str = ", ".join(topics) if topics else "None"
    block = (
        f"[{timestamp}]\n"
        f"Response ID: {response_id}\n"
        f">>> {user_prompt}\n"
        f"[{MODEL} - {reasoning_effort}] {answer_text}\n"
        f"Topic Tags: {topics_str}"
    )
    save_context_block(block)
    return response_id
