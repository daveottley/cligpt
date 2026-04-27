import sys
import re
import subprocess
import argparse
import shlex
from ai_client import (
    print_directory_status,
    single_query,
    sync_directory_vector_stores,
)
from config import DEFAULT_INDEX_CONCURRENCY, MODEL
from config import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_OUTPUT_STYLE,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
)
from model_capabilities import get_model_capabilities
from memory_manager import (
        ensure_required_permanent_memories, 
        add_permanent_memory, 
        view_permanent_memory, 
        forget_permanent_memory, 
        export_permanent_memory,
)

def read_multiline_input(prompt=">>> "):
    """
    Read user input over multiple lines until all occurrences of "$(" have
    matching closing ")" characters.
    """
    query = input(prompt)
    # Continue reading lines if there are unmatched "$(" patterns.
    while query.count("$(") > query.count(")"):
        query += "\n" + input("... ")
    return query

def process_command_substitutions(query):
    """
    Scan the query for all occurrences of command substitution (i.e. $(...))
    and replace each with its shell-expanded output.
    """
    # Pattern to match $( ... ) non-greedily across newlines.
    pattern = r'\$\((.*?)\)'
    
    def replacer(match):
        command = match.group(1)
        try:
            # Run the extracted command in a shell.
            output = subprocess.check_output(command, shell=True, text=True)
            return output.strip()
        except subprocess.CalledProcessError as e:
            print("Error processing command substitution:", e)
            # On error, return the original text.
            return match.group(0)
    
    # Replace every occurrence of $(...) in the query.
    return re.sub(pattern, replacer, query, flags=re.DOTALL)

def positive_int(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed

def has_default_query_prompt(argv):
    options_with_values = {
        "--width", "--model", "-m", "--file", "--image", "--directory",
        "--blob", "--index-concurrency", "--style", "--heartbeat-seconds",
        "--idle-timeout", "--request-timeout",
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in options_with_values:
            index += 2
            continue
        if arg.startswith(("-", "+")):
            index += 1
            continue
        return True
    return False

def format_mode_header(reasoning_effort, debug_mode, width, web_search=True):
    width_label = width if width else "auto"
    web_label = "on" if web_search else "off"
    capabilities = get_model_capabilities(MODEL)
    header = (
        f"[mode: {MODEL} - reasoning effort: {reasoning_effort} - "
        f"web: {web_label} - context: {capabilities.max_context_tokens:,} - "
        f"safe input: {capabilities.safe_input_tokens:,} - width: {width_label}]"
    )
    if debug_mode:
        header += " (Debug mode enabled)"
    return header

def interactive_mode(
    initial_reasoning_effort,
    initial_debug_mode,
    initial_width=None,
    initial_web_search=True,
    initial_style=DEFAULT_OUTPUT_STYLE,
    initial_no_color=False,
    initial_heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
    initial_idle_timeout_seconds=DEFAULT_IDLE_TIMEOUT_SECONDS,
    initial_request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
):
    # Set initial flag values (default reasoning effort defaults to "medium")
    current_reasoning_effort = initial_reasoning_effort or "medium"
    current_debug_mode = initial_debug_mode
    current_width = initial_width
    current_web_search = initial_web_search
    current_style = initial_style
    current_no_color = initial_no_color
    current_heartbeat_seconds = initial_heartbeat_seconds
    current_idle_timeout_seconds = initial_idle_timeout_seconds
    current_request_timeout_seconds = initial_request_timeout_seconds

    # Print initial REPL header.
    print(format_mode_header(current_reasoning_effort, current_debug_mode, current_width, current_web_search))
    print("Entering interactive mode. Type 'exit' or 'quit' to leave.")
    print("You may use special commands in chat:")
    print("  --remember <text>    : Permanently save the given text")
    print("  :view-memory         : Display all permanent memories")
    print("  :forget-memory <id>  : Remove a permanent memory by its ID")
    print("  :export-memory <file>: Export permanent memories to the specified file")
    print("You can adjust flags on the fly by prepending your input with them.")
    print("  Recognized flags: +debug (+d), -debug (-d), --high (-h), --medium (-m), --low (-l), --web, --no-web, --width <num>, --style <plain|codex|compact>, --no-color, --file <file>, --image <image>, --blob <file>, --directory <dir>, --index-concurrency <num>, --allow-partial-index, --wait-index")
    print("If only flags are provided, a confirmation message is printed.")
    
    try:
        while True:
            user_input = read_multiline_input(">>> ").strip()
            
            if user_input.lower() in ["exit", "quit"]:
                print("Exiting interactive mode.")
                break
           
            if not user_input:
                continue
           
            if user_input in ["--help"]:
                print("REPL Help:")
                print("  exit, quit             : Exit interactive mode")
                print("  --remember <key:value> : Save a semantically indexed long-term memory")
                print("  :view-memory           : Display all long-term memories")
                print("  :forget-memory <id>    : Remove a long-term memory by its ID")
                print("  :export-memory <file>  : Export long-term memories to a file")
                print("  Flags: +debug (+d), -debug (-d), --high (-h), --medium (-m), --low (-l), --web, --no-web, --width <num>, --style <plain|codex|compact>, --no-color")
                print("  Stream safety: --heartbeat-seconds <num>, --idle-timeout <num>, --request-timeout <num>")
                print("  Uploads: --file <file>, --image <png|jpg|jpeg|webp|gif>, --blob <file>, --directory <dir>")
                print("  --file accepts PDFs, LibreOffice documents, and raw text/code files.")
                print("  --blob sends a text report with metadata, hashes, hex preview, and strings.")
                print("  Directory uploads recurse up to 5000 files and use a reusable vector-store search index by default.")
                print("  Directory images are OCR/caption indexed; direct --image vision uploads are capped at 25.")
                print("  Querying an incomplete directory index can proceed with the current index while a background sync continues.")
                print("  Use sync-directory <dir> to sync first, index-status <dir> to inspect state, and --wait-index to block before a query.")
                print(f"  Directory indexing defaults to {DEFAULT_INDEX_CONCURRENCY} concurrent uploads; tune with --index-concurrency <num>.")
                print("  Docs: https://platform.openai.com/docs/guides/tools-file-search/ and https://platform.openai.com/docs/guides/images-vision")
                print("  Web search is enabled by default. Use --no-web for offline/model-only answers.")
                print("  Type your query directly to send it to the AI.")
                continue
                
            user_input = process_command_substitutions(user_input)
            prev_input = None
            while prev_input != user_input:
                prev_input = user_input
                user_input = process_command_substitutions(user_input)

            # Handle special in-chat memory commands.
            if user_input.startswith("--remember "):
                text = user_input[len("--remember "):].strip()
                try:
                    entry = add_permanent_memory(text)
                    print(f"Added permanent memory [{entry['id']}] at {entry['timestamp']}.")
                except ValueError as e:
                    print(e)
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
                    print("Invalid ID. Must be an integer.")
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

            # Split the input into shell-like tokens so paths can be quoted.
            try:
                tokens = shlex.split(user_input)
            except ValueError as exc:
                print(f"Could not parse input: {exc}")
                continue
            recognized_flags = {"+debug", "+d", "-debug", "-d", "--high", "-high", "-h",
                               "--medium", "-medium", "-m", "--low", "-low", "-l",
                               "--web", "--no-web", "--allow-partial-index", "--wait-index",
                               "--no-color"}
            flag_tokens = []
            query_tokens = []
            file_paths = []
            image_paths = []
            blob_paths = []
            directory_paths = []
            index_concurrency = DEFAULT_INDEX_CONCURRENCY
            allow_partial_index = False
            wait_index = False
            index = 0
            malformed_flag = False
            while index < len(tokens):
                token = tokens[index]
                if token in {
                    "--width", "--file", "--image", "--blob", "--directory",
                    "--index-concurrency", "--style", "--heartbeat-seconds",
                    "--idle-timeout", "--request-timeout",
                }:
                    if index + 1 >= len(tokens):
                        print(f"Usage: {token} <value>")
                        malformed_flag = True
                        break
                    value = tokens[index + 1]
                    if token == "--width":
                        try:
                            width_value = int(value)
                            if width_value < 1:
                                raise ValueError
                        except ValueError:
                            print("Width must be a positive integer.")
                            malformed_flag = True
                            break
                        flag_tokens.append(("--width", width_value))
                    elif token == "--style":
                        if value not in {"auto", "plain", "codex", "compact"}:
                            print("Style must be auto, plain, codex, or compact.")
                            malformed_flag = True
                            break
                        flag_tokens.append(("--style", value))
                    elif token == "--index-concurrency":
                        try:
                            index_concurrency = positive_int(value)
                        except argparse.ArgumentTypeError:
                            print("Index concurrency must be a positive integer.")
                            malformed_flag = True
                            break
                    elif token in {"--heartbeat-seconds", "--idle-timeout", "--request-timeout"}:
                        try:
                            flag_tokens.append((token, positive_int(value)))
                        except argparse.ArgumentTypeError:
                            print(f"{token} must be a positive integer.")
                            malformed_flag = True
                            break
                    elif token == "--file":
                        file_paths.append(value)
                    elif token == "--image":
                        image_paths.append(value)
                    elif token == "--blob":
                        blob_paths.append(value)
                    elif token == "--directory":
                        directory_paths.append(value)
                    index += 2
                    continue
                if token in recognized_flags:
                    flag_tokens.append((token, None))
                else:
                    query_tokens.append(token)
                index += 1
            if malformed_flag:
                continue
            # Process flag tokens and update current settings.
            for flag, value in flag_tokens:
                if flag in {"+debug", "+d"}:
                    current_debug_mode = True
                    print("Debug mode turned ON.")
                elif flag in {"-debug", "-d"}:
                    current_debug_mode = False
                    print("Debug mode turned OFF.")
                elif flag in {"--high", "-high", "-h"}:
                    current_reasoning_effort = "high"
                    print("Reasoning effort set to high.")
                elif flag in {"--medium", "-medium", "-m"}:
                    current_reasoning_effort = "medium"
                    print("Reasoning effort set to medium.")
                elif flag in {"--low", "-low", "-l"}:
                    current_reasoning_effort = "low"
                    print("Reasoning effort set to low.")
                elif flag == "--web":
                    current_web_search = True
                    print("Web search turned ON.")
                elif flag == "--no-web":
                    current_web_search = False
                    print("Web search turned OFF.")
                elif flag == "--allow-partial-index":
                    allow_partial_index = True
                    print("Partial directory index queries allowed.")
                elif flag == "--wait-index":
                    wait_index = True
                    print("Directory queries will wait for sync first.")
                elif flag == "--no-color":
                    current_no_color = True
                    print("Color output disabled.")
                elif flag == "--width":
                    current_width = value
                    print(f"Response width set to {current_width}.")
                elif flag == "--style":
                    current_style = value
                    print(f"Output style set to {current_style}.")
                elif flag == "--heartbeat-seconds":
                    current_heartbeat_seconds = value
                    print(f"Stream heartbeat set to {current_heartbeat_seconds} seconds.")
                elif flag == "--idle-timeout":
                    current_idle_timeout_seconds = value
                    print(f"Stream idle warning set to {current_idle_timeout_seconds} seconds.")
                elif flag == "--request-timeout":
                    current_request_timeout_seconds = value
                    print(f"Request timeout set to {current_request_timeout_seconds} seconds.")
            # If only flags were provided, reprint the header with updated settings.
            if not query_tokens:
                if file_paths or image_paths or blob_paths or directory_paths:
                    print("Upload flags apply to a query. Add a prompt after the upload paths.")
                print(format_mode_header(current_reasoning_effort, current_debug_mode, current_width, current_web_search))
            else:
                # Otherwise, join query tokens into a query string and process it.
                query = " ".join(query_tokens)
                try:
                    single_query(
                        query,
                        reasoning_effort=current_reasoning_effort,
                        debug=current_debug_mode,
                        width=current_width,
                        web_search=current_web_search,
                        file_paths=file_paths,
                        image_paths=image_paths,
                        blob_paths=blob_paths,
                        directory_paths=directory_paths,
                        index_concurrency=index_concurrency,
                        allow_partial_index=allow_partial_index,
                        wait_index=wait_index,
                        output_style=current_style,
                        no_color=current_no_color,
                        heartbeat_seconds=current_heartbeat_seconds,
                        idle_timeout_seconds=current_idle_timeout_seconds,
                        request_timeout_seconds=current_request_timeout_seconds,
                    )
                except ValueError as exc:
                    print(exc)
    except (KeyboardInterrupt, EOFError):
        print("\nExiting interactive mode.")

def parse_args():
    argv = sys.argv[1:]
    subcmds = {
        "query", "remember", "view-memory", "forget-memory", "export-memory",
        "sync-directory", "index-status",
    }
    if not any(arg in subcmds for arg in argv) and has_default_query_prompt(argv):
        argv = ["query"] + argv

    # Define a parent parser for global flags.
    global_parser = argparse.ArgumentParser(add_help=False, prefix_chars='-+')
    global_parser.add_argument("+debug", "+d", dest="debug", action="store_true",
                               default=argparse.SUPPRESS,
                               help="Enable debug mode")
    global_parser.add_argument("-debug", "-d", dest="debug", action="store_false",
                               default=argparse.SUPPRESS,
                               help="Disable debug mode")
    global_parser.add_argument("--high", dest="reasoning", action="store_const",
                               const="high", default=argparse.SUPPRESS,
                               help="Set reasoning effort to high")
    global_parser.add_argument("--medium", dest="reasoning", action="store_const",
                               const="medium", help="Set reasoning effort to medium (default)",
                               default=argparse.SUPPRESS)
    global_parser.add_argument("--low", dest="reasoning", action="store_const",
                               const="low", default=argparse.SUPPRESS,
                               help="Set reasoning effort to low")
    global_parser.add_argument("--width", dest="width", type=positive_int,
                               default=argparse.SUPPRESS,
                               help="Format responses to this maximum line width")
    global_parser.add_argument("--no-web", dest="web_search", action="store_false",
                               default=argparse.SUPPRESS,
                               help="Disable default web search for this request")
    global_parser.add_argument("--file", dest="file_paths", action="append",
                               default=argparse.SUPPRESS, metavar="FILE",
                               help="Upload a PDF, LibreOffice-convertible document, or raw text/code file. Docs: https://platform.openai.com/docs/guides/pdf-files")
    global_parser.add_argument("--image", dest="image_paths", action="append",
                               default=argparse.SUPPRESS, metavar="IMAGE",
                               help="Upload an image for this request: PNG, JPEG, WEBP, or non-animated GIF. Docs: https://platform.openai.com/docs/guides/images-vision")
    global_parser.add_argument("--blob", dest="blob_paths", action="append",
                               default=argparse.SUPPRESS, metavar="FILE",
                               help="Analyze an arbitrary binary by attaching a text report with metadata, hashes, hex preview, and strings")
    global_parser.add_argument("--directory", dest="directory_paths", action="append",
                               default=argparse.SUPPRESS, metavar="DIR",
                               help="Recursively index DIR in a reusable vector store, up to 5000 files; images are OCR/caption indexed and changed files resync automatically. Docs: https://platform.openai.com/docs/guides/tools-file-search/")
    global_parser.add_argument("--index-concurrency", dest="index_concurrency", type=positive_int,
                               default=argparse.SUPPRESS, metavar="N",
                               help=f"Concurrent file indexing uploads for directory syncs (default: {DEFAULT_INDEX_CONCURRENCY})")
    global_parser.add_argument("--allow-partial-index", dest="allow_partial_index", action="store_true",
                               default=argparse.SUPPRESS,
                               help="For --directory queries, proceed without prompting when the search index is incomplete and start a background sync")
    global_parser.add_argument("--wait-index", dest="wait_index", action="store_true",
                               default=argparse.SUPPRESS,
                               help="For --directory queries, sync the directory first before asking the model")
    global_parser.add_argument("--style", dest="output_style",
                               choices=["auto", "plain", "codex", "compact"],
                               default=argparse.SUPPRESS,
                               help="Output style: auto, plain, codex, or compact")
    global_parser.add_argument("--no-color", dest="no_color", action="store_true",
                               default=argparse.SUPPRESS,
                               help="Disable ANSI color in rich output")
    global_parser.add_argument("--heartbeat-seconds", dest="heartbeat_seconds", type=positive_int,
                               default=argparse.SUPPRESS, metavar="N",
                               help=f"Print a waiting heartbeat every N seconds while the model stream is silent (default: {DEFAULT_HEARTBEAT_SECONDS})")
    global_parser.add_argument("--idle-timeout", dest="idle_timeout_seconds", type=positive_int,
                               default=argparse.SUPPRESS, metavar="N",
                               help=f"Warn after N seconds without stream events (default: {DEFAULT_IDLE_TIMEOUT_SECONDS})")
    global_parser.add_argument("--request-timeout", dest="request_timeout_seconds", type=positive_int,
                               default=argparse.SUPPRESS, metavar="N",
                               help=f"OpenAI request timeout in seconds (default: {DEFAULT_REQUEST_TIMEOUT_SECONDS})")
    
    # Create the main parser.
    parser = argparse.ArgumentParser(
        description=(
            "CLI GPT Help Agent with context, permanent memory, and default-on "
            "OpenAI-hosted web search"
        ),
        parents=[global_parser],
        prefix_chars='-+'
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")
    
    # Define subcommands.
    parser_query = subparsers.add_parser("query", parents=[global_parser],
                                         help="Run a one-off query", prefix_chars='-+')
    parser_query.add_argument("prompt", type=str, help="User prompt to query the AI")
    parser_query.add_argument("-m", "--model", dest="model", default=MODEL,
                              help="Select model to use")
    
    parser_remember = subparsers.add_parser("remember", parents=[global_parser],
                                            help="Save text permanently", prefix_chars='-+')
    parser_remember.add_argument("text", type=str, help="Text to be remembered")
    
    parser_view = subparsers.add_parser("view-memory", parents=[global_parser],
                                        help="View permanent memories", prefix_chars='-+')
    
    parser_forget = subparsers.add_parser("forget-memory", parents=[global_parser],
                                          help="Forget a permanent memory by its id", prefix_chars='-+')
    parser_forget.add_argument("id", type=int, help="ID of the permanent memory to forget")
    
    parser_export = subparsers.add_parser("export-memory", parents=[global_parser],
                                          help="Export permanent memories to a file", prefix_chars='-+')
    parser_export.add_argument("output", type=str, help="Output file path")

    parser_sync = subparsers.add_parser("sync-directory", parents=[global_parser],
                                        help="Synchronize a directory into its reusable file-search index",
                                        prefix_chars='-+')
    parser_sync.add_argument("directory", nargs="+", help="Directory path(s) to sync")

    parser_status = subparsers.add_parser("index-status", parents=[global_parser],
                                          help="Show local sync status for a directory search index",
                                          prefix_chars='-+')
    parser_status.add_argument("directory", nargs="+", help="Directory path(s) to inspect")
    
    return parser.parse_args(argv)

def main():
    ensure_required_permanent_memories()
    args = parse_args()
    if not hasattr(args, "reasoning"):
        args.reasoning = "medium"
    if not hasattr(args, "debug"):
        args.debug = False
    if not hasattr(args, "width"):
        args.width = None
    if not hasattr(args, "web_search"):
        args.web_search = True
    if not hasattr(args, "file_paths"):
        args.file_paths = []
    if not hasattr(args, "image_paths"):
        args.image_paths = []
    if not hasattr(args, "blob_paths"):
        args.blob_paths = []
    if not hasattr(args, "directory_paths"):
        args.directory_paths = []
    if not hasattr(args, "index_concurrency"):
        args.index_concurrency = DEFAULT_INDEX_CONCURRENCY
    if not hasattr(args, "allow_partial_index"):
        args.allow_partial_index = False
    if not hasattr(args, "wait_index"):
        args.wait_index = False
    if not hasattr(args, "output_style"):
        args.output_style = DEFAULT_OUTPUT_STYLE
    if not hasattr(args, "no_color"):
        args.no_color = False
    if not hasattr(args, "heartbeat_seconds"):
        args.heartbeat_seconds = DEFAULT_HEARTBEAT_SECONDS
    if not hasattr(args, "idle_timeout_seconds"):
        args.idle_timeout_seconds = DEFAULT_IDLE_TIMEOUT_SECONDS
    if not hasattr(args, "request_timeout_seconds"):
        args.request_timeout_seconds = DEFAULT_REQUEST_TIMEOUT_SECONDS
    if not getattr(args, "command", None):
        interactive_mode(
            args.reasoning,
            args.debug,
            args.width,
            args.web_search,
            args.output_style,
            args.no_color,
            args.heartbeat_seconds,
            args.idle_timeout_seconds,
            args.request_timeout_seconds,
        )
    elif args.command == "query":
        try:
            single_query(
                args.prompt,
                reasoning_effort=args.reasoning,
                debug=args.debug,
                model=args.model,
                width=args.width,
                web_search=args.web_search,
                file_paths=args.file_paths,
                image_paths=args.image_paths,
                blob_paths=args.blob_paths,
                directory_paths=args.directory_paths,
                index_concurrency=args.index_concurrency,
                allow_partial_index=args.allow_partial_index,
                wait_index=args.wait_index,
                output_style=args.output_style,
                no_color=args.no_color,
                heartbeat_seconds=args.heartbeat_seconds,
                idle_timeout_seconds=args.idle_timeout_seconds,
                request_timeout_seconds=args.request_timeout_seconds,
            )
        except ValueError as exc:
            print(exc)
    elif args.command == "remember":
        try:
            entry = add_permanent_memory(args.text)
            print(f"Added permanent memory [{entry['id']}] at {entry['timestamp']}.")
        except ValueError as e:
            print(e)
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
    elif args.command == "sync-directory":
        print(
            "Directory sync can take significant time, from minutes to hours or days "
            "for large or complex directories. This feature is experimental and may "
            "use substantial API tokens/storage.",
            flush=True,
        )
        sync_directory_vector_stores(args.directory, index_concurrency=args.index_concurrency)
    elif args.command == "index-status":
        print_directory_status(args.directory)

if __name__ == "__main__":
    main()
