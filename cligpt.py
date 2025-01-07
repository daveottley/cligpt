import sys
import os
import platform
from openai import OpenAI
import textwrap

MODEL = "o1-preview"
MAX_COMPLETION_TOKENS = 20_000
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
    """Start a REPL loop, sending user input to OpenAI until 'exit' or 'quit'."""
    print("Entering REPL mode. Type 'exit' or 'quit' to leave.")
    while True:
        user_prompt = input(">>> ")
        if user_prompt.lower() in ["exit", "quit"]:
            print("Exiting REPL mode.")
            break
        if user_prompt.strip() == "":
            continue  # Skip empty lines
        response_text = single_query(user_prompt)
        print(response_text)
        print("-" * 40)  # Separator after each response

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
