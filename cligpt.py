#!/usr/bin/env python3

import sys
import os
import platform
from openai import OpenAI
import textwrap

MODEL = "o3-mini"
STREAM = True
# MAX_COMPLETION_TOKENS = 32_000
N = 1
PRESENCE_PENALTY = 0
SYSTEM_MESSAGE_FILE = "system_message.txt"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def single_query(user_prompt, reasoning_effort="medium"):
    """
    Send a single query to OpenAI with the given reasoning_effort and return the response text.
    """

    # Detect the operating system and version
    operating_system = platform.system()
    version = platform.release()

    # Detect the distribution name (for Linux systems)
    if operating_system == "Linux":
        try:
            import distro
            distribution = distro.name()
            version = distro.version()
        except ImportError:
            distribution = "Linux"
    else:
        distribution = operating_system

    # Detect the shell
    shell_path = os.getenv("SHELL", "")
    shell = os.path.basename(shell_path) if shell_path else "unknown"

    # Detect the editor
    editor = os.getenv("EDITOR", "unknown")

    # Build the system message from file
    with open(SYSTEM_MESSAGE_FILE, "r", encoding="utf-8") as f:
        system_message_template = f.read().strip()

    SYSTEM_MESSAGE = system_message_template.format(
        distribution=distribution,
        operating_system=operating_system,
        version=version,
        shell=shell,
        editor=editor
    ).strip()

    # Build the chat completion from a developer and a user message
    response = client.chat.completions.create(
        model=MODEL,
        reasoning_effort=reasoning_effort,
        messages=[
            {"role": "developer", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": user_prompt}
        ],
        # max_completion_tokens=MAX_COMPLETION_TOKENS,
        n=N,
        presence_penalty=PRESENCE_PENALTY,
        stream=STREAM,
        store=True
    )

    answers = []
    message_chunks = []

    # Stream and print each chunk immediately
    for chunk in response:
        chunk_message = chunk.choices[0].delta.content
        if chunk_message:
            sys.stdout.write(chunk_message)
            sys.stdout.flush()
            message_chunks.append(chunk_message)
    sys.stdout.write("\n")
    sys.stdout.flush()

    message_content = ''.join(message_chunks)
    answers.append(message_content)
    return answers

def repl_mode():
    """
    Start a REPL loop. In REPL mode, the reasoning_effort is set to the default "medium".
    """
    import datetime
    import os

    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")

    script_dir = os.path.dirname(os.path.realpath(__file__))
    conv_dir = os.path.join(script_dir, "conversations")
    os.makedirs(conv_dir, exist_ok=True)

    log_file_path = os.path.join(
        conv_dir,
        f"REPL-conversation-{date_str}-{time_str}.txt"
    )

    with open(log_file_path, "w", encoding="utf-8") as f:
        f.write(
            "--cligpt.py SHUT DOWN UNEXPECTEDLY!! THIS LINE SHOULD NOT "
            "BE HERE. Investigate.\n"
        )

    log_file = open(log_file_path, "a", encoding="utf-8")
    normal_exit = False

    intro_line = "Entering REPL mode. Type 'exit' or 'quit' to leave."
    print(intro_line)
    log_file.write(intro_line + "\n")
    log_file.flush()

    try:
        while True:
            user_prompt = input(">>> ")
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

            all_answers = single_query(user_prompt, reasoning_effort="medium")
            if not all_answers:
                continue

            if len(all_answers) == 1:
                print(all_answers[0])
                log_file.write(all_answers[0] + "\n")
            else:
                for i, ans in enumerate(all_answers, start=1):
                    numbered_ans = f"Answer {i}:\n{ans}\n{'-' * 40}"
                    print(numbered_ans)
                    log_file.write(numbered_ans + "\n")

            sep_line = "-" * 40
            print(sep_line)
            log_file.write(sep_line + "\n")
            log_file.flush()

    finally:
        log_file.close()
        if normal_exit:
            with open(log_file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            filtered_lines = [
                ln for ln in lines
                if not ln.startswith(
                    "--cligpt.py SHUT DOWN UNEXPECTEDLY!! THIS LINE"
                )
            ]
            with open(log_file_path, "w", encoding="utf-8") as f:
                f.writelines(filtered_lines)

def main():
    if not client.api_key:
        print("Please set the OPENAI_API_KEY environment variable.")
        sys.exit(1)

    # If arguments are provided, process the flag and prompt.
    if len(sys.argv) > 1:
        # Check if the first argument is a reasoning flag.
        arg0 = sys.argv[1].lower()
        if arg0 in ["-high", "-medium", "-low"]:
            if arg0 == "-high":
                reasoning_effort = "high"
            elif arg0 == "-low":
                reasoning_effort = "low"
            else:
                reasoning_effort = "medium"
            user_prompt = " ".join(sys.argv[2:])
        else:
            reasoning_effort = "medium"
            user_prompt = " ".join(sys.argv[1:])
        all_answers = single_query(user_prompt, reasoning_effort)
        if not STREAM :
            if len(all_answers) == 1:
                print(all_answers[0])
            else:
                for i, ans in enumerate(all_answers, start=1):
                    print(f"Answer {i}:\n{ans}\n{'-' * 40}")
    else:
        repl_mode()

if __name__ == "__main__":
    main()

