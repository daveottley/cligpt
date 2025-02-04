#!/usr/bin/env python3

import sys
import os
import platform
from openai import OpenAI
import textwrap

MODEL = "o3-mini"
MAX_COMPLETION_TOKENS = 32_000
N = 1
PRESENCE_PENALTY = 0
SYSTEM_MESSAGE_FILE = "system_message.txt"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def single_query(user_prompt):
    """
    Send a single query to OpenAI and return the response text.
    """

    # Detect the operating system and version
    operating_system = platform.system()
    version = platform.release()

    # Detect the distribution name (for Linux systems)
    if operating_system == "Linux":
        try:
            # Try to import the 'distro' module for detailed info
            import distro
            distribution = distro.name()
            version = distro.version()
        except ImportError:
            # Fallback if 'distro' is not available
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

    # Fill placeholders with dynamically detected environment variables
    SYSTEM_MESSAGE = system_message_template.format(
        distribution=distribution,
        operating_system=operating_system,
        version=version,
        shell=shell,
        editor=editor
    ).strip()

    # Combine the system and user prompt to comply with new API
    combined_prompt = f"{SYSTEM_MESSAGE}\n\n{user_prompt}"
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "user", "content": combined_prompt},
        ],
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        n=N,
        presence_penalty=PRESENCE_PENALTY
    )

    # Return a list of stripped message content
    answers = []
    for choice in response.choices:
        finish_reason = choice.finish_reason
        content = choice.message.content.strip()
        answers.append(content)
        # If the content is empty, print the finish reason for the user
        if not content:
            print(
                textwrap.fill(
                    f"Received empty content from the model. "
                    f"The reason given was: {finish_reason}",
                    width=79
                )
            )
            return None

    return answers

def repl_mode():
    """
    Start a REPL loop, sending user input to OpenAI until 'exit' or 'quit'.
    Logs conversation in real time to a file named:
        REPL-conversation-<YYYY-MM-DD>-<HHMMSS>.txt
    The file is stored in conversations/ relative to cligpt.py.
    If the user exits normally, the special investigation line is removed.
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

    # Write the special investigation line once at file creation
    with open(log_file_path, "w", encoding="utf-8") as f:
        f.write(
            "--cligpt.py SHUT DOWN UNEXPECTEDLY!! THIS LINE SHOULD NOT "
            "BE HERE. Investigate.\n"
        )

    # Open file for real-time logging
    log_file = open(log_file_path, "a", encoding="utf-8")

    # Track normal exit
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

            all_answers = single_query(user_prompt)
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

        # If normal exit, remove the special investigation line
        if normal_exit:
            with open(log_file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # Filter out the special line
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

    # If we have arguments, run single response mode; otherwise, drop into REPL
    if len(sys.argv) > 1:
        user_prompt = " ".join(sys.argv[1:])
        all_answers = single_query(user_prompt)
        if len(all_answers) == 1:
            # Only one answer returned
            print(all_answers[0])
        else:
            # Display each answer, let the user pick
            for i, ans in enumerate(all_answers, start=1):
                print(f"Answer {i}:\n{ans}\n{'-' * 40}")
    else:
        repl_mode()

if __name__ == "__main__":
    main()
