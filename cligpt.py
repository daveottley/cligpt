#!/usr/bin/env python3
import sys
import os
import platform
import datetime
import re
import subprocess
import json
from openai import OpenAI

# Constants
MODEL = "o3-mini"
FAST_MODEL = "gpt-3.5-turbo"
STREAM = True
N = 1
PRESENCE_PENALTY = 0
SYSTEM_MESSAGE_FILE = "system_message.txt"
CONTEXT_FILE = "context.txt"
MAX_CONTEXT_TOKENS = 150_000
DELIMITER = "\n" + "-" * 15 + "\n"

# Define the JSON Schema for structured output (using Structured Outputs)
# Note the added "name" and "strict" keys.
RESPONSE_SCHEMA = {
    "name": "StructuredOutput",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "The answer text."
            },
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

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def perform_command_substitution(input_text):
    """
    Perform command substitution on the input text.
    Searches for patterns matching $(command) and replaces each occurrence
    with its output. If execution fails, an error message is inserted.
    """
    pattern = re.compile(r'\$\((.+?)\)')

    def replace(match):
        cmd = match.group(1).strip()
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
            return output.decode('utf-8').strip()
        except subprocess.CalledProcessError:
            return f"<error executing command: {cmd}>"

    return pattern.sub(replace, input_text)

def get_relevant_tags(prompt):
    """
    Query FAST_MODEL to obtain the top 10 relevant tags for the prompt.
    Returns a list of lower-case tags.
    """
    messages = [
        {"role": "system", "content": "Return exactly a JSON array of the top 10 relevant tags for the following text."},
        {"role": "user", "content": prompt}
    ]
    try:
        response = client.chat.completions.create(
            model=FAST_MODEL,
            messages=messages,
            stream=False,
            n=1,
            presence_penalty=PRESENCE_PENALTY
        )
        result_str = response.choices[0].message.content
        tags = json.loads(result_str)
        if isinstance(tags, list):
            return [str(tag).lower() for tag in tags]
        else:
            return []
    except Exception:
        return []

def prune_context_by_topic(current_prompt):
    """
    Parse context.txt and return blocks that are either recent (within the last hour) or, if older,
    that softly match the current prompt. Old blocks are added sequentially in file order until adding
    the next block would push the total token count over MAX_CONTEXT_TOKENS.
    Blocks are delimited by "\n---------------\n".
    """
    if not os.path.exists(CONTEXT_FILE):
        return "", 0, "None", "None"

    with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
    blocks = content.split(DELIMITER)
    now = datetime.datetime.now()

    # Collect all unique topic tags from the context for older blocks 
    all_tags = set()
    for block in blocks:
        m = re.search(r"topic tags:\s*(.*)", block, re.IGNORECASE)
        if m:
            tags_str = m.group(1)
            if tags_str.strip().lower() not in ["none", ""]:
                tags = [tag.strip().lower() for tag in tags_str.split(",") if tag.strip()]
                all_tags.update(tags)
    all_tags_list = list(all_tags)

    # Query FAST_MODEL for matching tags based on the current prompt
    if all_tags_list:
        query = (
            "Given the following list of topic tags:\n"
            f"{', '.join(all_tags_list)}\n"
            "Return exactly a JSON array of the tags that softly match the current prompt: "
            f"{current_prompt}"
        )
        messages = [
            {"role": "system", "content": "You are a tag matching assistant. Return exactly a JSON array of tags that softly match the prompt."},
            {"role": "user", "content": query}
        ]
        try:
            response = client.chat.completions.create(
                model=FAST_MODEL,
                messages=messages,
                stream=False,
                n=1,
                presence_penalty=PRESENCE_PENALTY
            )
            result_str = response.choices[0].message.content
            matching_tags = json.loads(result_str)
            if not isinstance(matching_tags, list):
                matching_tags = []
        except Exception:
            matching_tags = []
    else:
        matching_tags = []

    def estimate_tokens(text):
        return len(text.split())

    accumulated_tokens = 0
    selected_blocks = []

    for block in blocks:
        # Determine eligibility of the block
        is_recent = False
        m_time = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", block)
        if m_time:
            try:
                block_time_str = m_time.group(1)
                block_time = datetime.datetime.strptime(block_time_str, "%Y-%m-%d %H:%M:%S")
                if now - block_time <= datetime.timedelta(hours=1):
                    is_recent = True
            except Exception:
                pass
        qualifies = False
        if is_recent:
            qualifies = True
        else:
            m_tag = re.search(r"topic tags:\s*(.*)", block, re.IGNORECASE)
            if m_tag:
                tags_str = m_tag.group(1)
                if tags_str.strip().lower() not in ["none", ""]:
                    tags = [tag.strip().lower() for tag in tags_str.split(",") if tag.strip()]
                    if any(tag in matching_tags for tag in tags):
                        qualifies = True

        if qualifies:
            block_tokens = estimate_tokens(block)
            if accumulated_tokens + block_tokens > MAX_CONTEXT_TOKENS:
                break
            selected_blocks.append(block)
            accumulated_tokens += block_tokens

    pruned_context = DELIMITER.join(selected_blocks)

    # Compute topic tags (as returned by FAST_MODEL) and the oldest block timestamp
    topic_tags = ", ".join(matching_tags) if matching_tags else "None"
    oldest = None
    for block in selected_blocks:
        m_time = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", block)
        if m_time:
            try:
                t = datetime.datetime.strptime(m_time.group(1), "%Y-%m-%d %H:%M:%S")
                if oldest is None or t < oldest:
                    oldest =  t
            except Exception:
                pass
    oldest_block = oldest.strftime("%Y-%m-%d %H:%M:%S") if oldest else "None"

    return pruned_context, len(selected_blocks), topic_tags, oldest_block

def single_query(user_prompt, reasoning_effort="medium"):
    """
    Send a query to OpenAI using the specified reasoning effort and expect a
    structured JSON output with keys: "answer", "topics", and "reasoning_tokens".
    This implementation uses OpenAI's Structured Outputs functionality by providing a JSON Schema.
    """
    # Gather system and environment information
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
        system_message_template = f.read().strip()
    SYSTEM_MESSAGE = system_message_template.format(
        distribution=distribution,
        operating_system=operating_system,
        version=version,
        shell=shell,
        editor=editor
    ).strip()

    # Retrieve pruned context history using intelligent soft matching
    PRUNED_CONTEXT, chat_blocks, topic_tags, oldest_block = prune_context_by_topic(user_prompt)

    def estimate_tokens(text):
        return len(text.split())
    system_tokens = estimate_tokens(SYSTEM_MESSAGE)
    context_tokens = estimate_tokens(PRUNED_CONTEXT)
    user_tokens = estimate_tokens(user_prompt)
    total_context_tokens = system_tokens + context_tokens + user_tokens

    header = (
        f"[Reasoning: {reasoning_effort}]\n"
        f"[Context Tokens: {total_context_tokens}]\n"
        f"  [System Message: {system_tokens}]\n"
        f"  [Pruned Context: {context_tokens}]\n"
        f"    [Chat Blocks: {chat_blocks}]\n"
        f"    [Topic Tags: {topic_tags}]\n"
        f"    [Oldest Block: {oldest_block}]\n"
        f"  [User Prompt: {user_tokens}]\n"
    )
    sys.stdout.write(header)
    sys.stdout.flush()

    # Combine system message and pruned context for a comprehensive system prompt
    combined_system = SYSTEM_MESSAGE + "\n\n" + PRUNED_CONTEXT

    messages = [
        {"role": "system", "content": combined_system},
        {"role": "user", "content": user_prompt}
    ]

    # Call the API using Structured Outputs (JSON Schema) via response_format
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
        n=N,
        presence_penalty=PRESENCE_PENALTY,
        stream=STREAM,
        store=True
    )

    # Accumulate the streamed response using attribute access and log each chunk
    message_chunks = []
    for chunk in response:
        # Log the entire delta as a dict for debugging
        delta_dict = chunk.choices[0].delta.model_dump()
        # print("Received chunk delta:", delta_dict)
        
        delta = chunk.choices[0].delta
        if delta:
            chunk_message = delta.content if delta.content is not None else ""
            if not chunk_message and hasattr(delta, "function_call"):
                chunk_message = (delta.function_call.arguments
                                 if delta.function_call and delta.function_call.arguments is not None
                                 else "")
            message_chunks.append(chunk_message)
    raw_content = "".join(message_chunks)

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

    #######################
    ### Write to stdout ###
    #######################
    
    header = f"  [Reasoning Tokens: {reasoning_tokens_used}]\n"
    # Check and print finish_reason if available
    finish_reason = chunk.choices[0].finish_reason
    if finish_reason is not None:
        header += f"[Finish reason: {finish_reason}]"
    header += "\n"
        
    full_output = header + "\n" + answer_text
    sys.stdout.write(full_output + "\n")
    sys.stdout.flush()

    #############################
    ### Write to context file ###
    #############################
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(CONTEXT_FILE, "a", encoding="utf-8") as cf:
        cf.write(f"[{timestamp}] >>> {user_prompt}\n")
        cf.write(f"[Response] {answer_text}\n")
        cf.write(f"Topic Tags: {', '.join(topics) if topics else 'None'}\n")
        cf.write(DELIMITER)

    return full_output

def repl_mode(reasoning_effort="medium"):
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    script_dir = os.path.dirname(os.path.realpath(__file__))
    conv_dir = os.path.join(script_dir, "conversations")
    os.makedirs(conv_dir, exist_ok=True)
    log_file_path = os.path.join(conv_dir, f"REPL-conversation-{date_str}-{time_str}.txt")
    with open(log_file_path, "w", encoding="utf-8") as f:
        f.write("--cligpt.py SHUT DOWN UNEXPECTEDLY!! THIS LINE SHOULD NOT BE HERE. Investigate.\n")
    log_file = open(log_file_path, "a", encoding="utf-8")
    normal_exit = False
    intro_line = (
        "Entering REPL mode. Type 'exit' or 'quit' to leave.\n"
        "To change the default reasoning effort, prefix your message with a flag (-high, -medium, -low)."
    )
    print(intro_line)
    log_file.write(intro_line + "\n")
    log_file.flush()

    try:
        while True:
            user_prompt = input(">>> ")
            user_prompt = perform_command_substitution(user_prompt)
            log_file.write(f">>> {user_prompt}\n")
            log_file.flush()
            if user_prompt.lower() in ["exit", "quit"]:
                exit_line = "Exiting REPL mode."
                print(exit_line)
                log_file.write(exit_line + "\n")
                normal_exit = True
                break
            if user_prompt.strip() == "":
                continue
            tokens = user_prompt.split()
            if tokens and tokens[0].lower() in ["-high", "-medium", "-low"]:
                new_flag = tokens[0].lower()
                if new_flag == "-high":
                    reasoning_effort = "high"
                elif new_flag == "-low":
                    reasoning_effort = "low"
                else:
                    reasoning_effort = "medium"
                if len(tokens) == 1:
                    confirmation = f"Default reasoning effort updated to {reasoning_effort}."
                    print(confirmation)
                    log_file.write(confirmation + "\n")
                    log_file.write(DELIMITER)
                    log_file.flush()
                    continue
                else:
                    user_prompt = " ".join(tokens[1:])
            single_query(user_prompt, reasoning_effort)
            log_file.write(DELIMITER)
            log_file.flush()
    finally:
        log_file.close()
        if normal_exit:
            with open(log_file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            filtered_lines = [ln for ln in lines if not ln.startswith("--cligpt.py SHUT DOWN UNEXPECTEDLY!!")]
            with open(log_file_path, "w", encoding="utf-8") as f:
                f.writelines(filtered_lines)

def main():
    if not client.api_key:
        sys.exit(1)
    reasoning_effort = "medium"
    user_prompt = ""
    if len(sys.argv) > 1:
        arg0 = sys.argv[1].lower()
        if arg0 in ["-high", "-medium", "-low"]:
            if arg0 == "-high":
                reasoning_effort = "high"
            elif arg0 == "-low":
                reasoning_effort = "low"
            else:
                reasoning_effort = "medium"
            user_prompt = " ".join(sys.argv[2:]).strip()
        else:
            user_prompt = " ".join(sys.argv[1:]).strip()
    if user_prompt == "":
        repl_mode(reasoning_effort)
    else:
        single_query(user_prompt, reasoning_effort)

if __name__ == "__main__":
    main()

