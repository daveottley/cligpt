# cli_interface.py

import sys
import argparse
from ai_client import single_query
from memory_manager import add_permanent_memory, view_permanent_memory, forget_permanent_memory, export_permanent_memory

def interactive_mode(reasoning_effort):
    print("Entering interactive mode. Type 'exit' or 'quit' to leave.")
    print("You may use special commands in chat:")
    print("  --remember <text>    : Mark the given text block for permanent memorization")
    print("  :view-memory         : View all permanent memories")
    print("  :forget-memory <id>  : Remove a permanent memory by its id")
    try:
        while True:
            user_input = input(">>> ").strip()
            if user_input.lower() in ["exit", "quit"]:
                print("Exiting interactive mode.")
                break
            if user_input == "":
                continue
            # Check for in-chat memory commands (colon-prefixed)
            if user_input.startswith("--remember "):
                text = user_input[len("--remember "):].strip()
                entry = add_permanent_memory(text)
                print(f"Added permanent memory [{entry['id']}] at {entry['timestamp']}.")
                continue
            elif user_input.startswith(":view-memory"):
                memories = view_permanent_memory()
                if memories:
                    for mem in memories:
                        print(mem)
                else:
                    print("No permanent memories found.")
                continue
            elif user_input.startswith(":forget-memory"):
                parts = user_input.split()
                if len(parts) < 2:
                    print("Usage: :forget-memory <id>")
                    continue
                try:
                    entry_id = int(parts[1])
                    forget_permanent_memory(entry_id)
                    print(f"Permanent memory with id {entry_id} has been removed.")
                except ValueError:
                    print("Invalid id. Must be an integer.")
                continue
            elif user_input.startswith(":export-memory"):
                parts = user_input.split()
                if len(parts) < 2:
                    print("Usage: :export-memory <output_file>")
                    continue
                output_file = parts[1]
                export_permanent_memory(output_file)
                print(f"Permanent memories exported to {output_file}.")
                continue
            # Otherwise, send the prompt to the AI.
            single_query(user_input, reasoning_effort=reasoning_effort)
    except (KeyboardInterrupt, EOFError):
        print("\nExiting interactive mode.")

def parse_args():
    parser = argparse.ArgumentParser(
        description="CLI GPT Help Agent with context and permanent memory management"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # One-off query command
    parser_query = subparsers.add_parser("query", help="Run a one-off query")
    parser_query.add_argument("prompt", type=str, help="User prompt to query the AI")
    parser_query.add_argument(
        "--reasoning", choices=["high", "medium", "low"], default="medium",
        help="Set reasoning effort (default: medium)"
    )

    # Permanent memory commands
    parser_remember = subparsers.add_parser("remember", help="Mark text for permanent memorization")
    parser_remember.add_argument("text", type=str, help="Text to be remembered permanently")

    parser_view = subparsers.add_parser("view-memory", help="View permanent memory entries")

    parser_forget = subparsers.add_parser("forget-memory", help="Forget a permanent memory by its id")
    parser_forget.add_argument("id", type=int, help="ID of the permanent memory to forget")

    parser_export = subparsers.add_parser("export-memory", help="Export permanent memories to a file")
    parser_export.add_argument("output", type=str, help="Output file path")

    # If no subcommand is provided, default to interactive mode.
    return parser.parse_args()

def main():
    args = parse_args()
    if args.command == "query":
        single_query(args.prompt, reasoning_effort=args.reasoning)
    elif args.command == "remember":
        entry = add_permanent_memory(args.text)
        print(f"Added permanent memory [{entry['id']}] at {entry['timestamp']}.")
    elif args.command == "view-memory":
        memories = view_permanent_memory()
        if memories:
            for mem in memories:
                print(mem)
        else:
            print("No permanent memories found.")
    elif args.command == "forget-memory":
        forget_permanent_memory(args.id)
        print(f"Permanent memory with id {args.id} has been removed.")
    elif args.command == "export-memory":
        export_permanent_memory(args.output)
        print(f"Permanent memories exported to {args.output}.")
    else:
        # If no command is given, start interactive mode.
        reasoning_effort = "medium"
        # Check for an optional reasoning flag among sys.argv
        if len(sys.argv) > 1 and sys.argv[1] in ["-high", "-medium", "-low"]:
            flag = sys.argv[1]
            if flag == "-high":
                reasoning_effort = "high"
            elif flag == "-low":
                reasoning_effort = "low"
            else:
                reasoning_effort = "medium"
        interactive_mode(reasoning_effort)

if __name__ == "__main__":
    main()

