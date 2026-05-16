import sys
import os
import re
import subprocess
import argparse
import shlex
from config import DEFAULT_INDEX_CONCURRENCY, MODEL
from config import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_OUTPUT_STYLE,
    DEFAULT_PROMPT_CACHE_KEY,
    DEFAULT_PROMPT_CACHE_RETENTION,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_VECTOR_STORE_EXPIRATION_DAYS,
)
from model_capabilities import get_model_capabilities
from memory_manager import (
        ensure_required_permanent_memories, 
        add_permanent_memory, 
        view_permanent_memory, 
        forget_permanent_memory, 
        update_permanent_memory,
        export_permanent_memory,
)
from maintenance import doctor, update


def ai_functions():
    from ai_client import (
        delete_index,
        expire_index,
        print_index_duplicates,
        print_index_list,
        print_directory_status,
        single_query,
        sync_directory_vector_stores,
    )
    return {
        "delete_index": delete_index,
        "expire_index": expire_index,
        "print_index_duplicates": print_index_duplicates,
        "print_index_list": print_index_list,
        "print_directory_status": print_directory_status,
        "single_query": single_query,
        "sync_directory_vector_stores": sync_directory_vector_stores,
    }

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

class TopLevelHelpParser(argparse.ArgumentParser):
    ANSI = {
        "bold": "\033[1m",
        "cyan": "\033[36m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "dim": "\033[2m",
        "reset": "\033[0m",
    }

    def should_color_help(self):
        if os.getenv("CLIGPT_FORCE_COLOR") is not None:
            return True
        if os.getenv("NO_COLOR") is not None:
            return False
        return sys.stdout.isatty()

    def style_help(self, text, *styles):
        if not self.should_color_help():
            return text
        return "".join(self.ANSI[style] for style in styles) + text + self.ANSI["reset"]

    def format_help(self):
        command_groups = getattr(self, "command_groups", [])
        lines = [
            f"{self.style_help('usage:', 'bold')} gpt {self.style_help('command', 'green')} [args]",
            "",
            self.style_help(
                "CLI Help Agent with context/memory management, web search, and tool use",
                "bold",
            ),
        ]
        for heading, command_help in command_groups:
            lines.extend(["", self.style_help(heading, "bold", "cyan")])
            for command, help_text in command_help:
                lines.append(f"  {self.style_help(command.ljust(18), 'green')} {help_text}")
        lines.extend([
            "",
            self.style_help("options:", "bold", "cyan"),
            (
                '  Options vary per command. Run "'
                f"{self.style_help('gpt command --help', 'yellow')}"
                '" for detailed options.'
            ),
            "",
        ])
        return "\n".join(lines)

def has_default_query_prompt(argv):
    options_with_values = {
        "--width", "--model", "-m", "--file", "--image", "--directory",
        "--blob", "--index-concurrency", "--style", "--heartbeat-seconds",
        "--idle-timeout", "--request-timeout", "--prompt-cache-key",
        "--prompt-cache-retention",
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
    initial_prompt_cache_key=DEFAULT_PROMPT_CACHE_KEY,
    initial_prompt_cache_retention=DEFAULT_PROMPT_CACHE_RETENTION,
    initial_include_context=True,
    initial_full_context=False,
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
    current_prompt_cache_key = initial_prompt_cache_key
    current_prompt_cache_retention = initial_prompt_cache_retention
    current_include_context = initial_include_context
    current_full_context = initial_full_context

    # Print initial REPL header.
    print(format_mode_header(current_reasoning_effort, current_debug_mode, current_width, current_web_search))
    print("Entering interactive mode. Type 'exit' or 'quit' to leave.")
    print("You may use special commands in chat:")
    print("  --remember <key:value>   : Permanently save a memory")
    print("  :view-memory            : Display all permanent memories")
    print("  :forget-memory <id>     : Remove a permanent memory by its stable ID")
    print("  :edit-memory <id> <text>: Replace a permanent memory with key:value text")
    print("  :export-memory <file>   : Export permanent memories to the specified file")
    print("You can adjust flags on the fly by prepending your input with them.")
    print("  Recognized flags: +debug (+d), -debug (-d), --high (-h), --medium (-m), --low (-l), --web, --no-web, --context, --no-context, --full-context, --raw, --width <num>, --style <plain|codex|compact>, --no-color, --file <file>, --image <image>, --blob <file>, --directory <dir>, --remote-search, --index-concurrency <num>, --allow-partial-index, --wait-index, --prompt-cache-key <key>, --prompt-cache-retention <auto|in_memory|24h|off>")
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
                print("  :forget-memory <id>    : Remove a long-term memory by its stable ID")
                print("  :edit-memory <id> <key:value> : Replace a long-term memory by stable ID")
                print("  :export-memory <file>  : Export long-term memories to a file")
                print("  Flags: +debug (+d), -debug (-d), --high (-h), --medium (-m), --low (-l), --web, --no-web, --context, --no-context, --full-context, --raw, --width <num>, --style <plain|codex|compact>, --no-color")
                print("  Stream safety: --heartbeat-seconds <num>, --idle-timeout <num>, --request-timeout <num>")
                print("  Prompt cache: --prompt-cache-key <key>, --prompt-cache-retention <auto|in_memory|24h|off>")
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
                if len(parts) != 2:
                    print("Usage: :forget-memory <id>")
                    continue
                try:
                    entry_id = int(parts[1])
                except ValueError:
                    print("Invalid ID. Must be an integer.")
                    continue
                try:
                    forget_permanent_memory(entry_id)
                    print(f"Permanent memory with id {entry_id} has been removed.")
                except ValueError as e:
                    print(e)
                continue
            elif user_input.startswith(":edit-memory"):
                parts = user_input.split(maxsplit=2)
                if len(parts) != 3:
                    print("Usage: :edit-memory <id> <key:value>")
                    continue
                try:
                    entry_id = int(parts[1])
                except ValueError:
                    print("Invalid ID. Must be an integer.")
                    continue
                try:
                    entry = update_permanent_memory(entry_id, parts[2].strip())
                    print(f"Updated permanent memory [{entry['id']}] at {entry['timestamp']}.")
                except ValueError as e:
                    print(e)
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
                               "--web", "--no-web", "--context", "--no-context", "--full-context", "--raw", "--remote-search", "--allow-partial-index", "--wait-index",
                               "--no-color"}
            flag_tokens = []
            query_tokens = []
            file_paths = []
            image_paths = []
            blob_paths = []
            directory_paths = []
            index_concurrency = DEFAULT_INDEX_CONCURRENCY
            remote_search = False
            allow_partial_index = False
            wait_index = False
            raw_prompt = False
            index = 0
            malformed_flag = False
            while index < len(tokens):
                token = tokens[index]
                if token in {
                    "--width", "--file", "--image", "--blob", "--directory",
                    "--index-concurrency", "--style", "--heartbeat-seconds",
                    "--idle-timeout", "--request-timeout", "--prompt-cache-key",
                    "--prompt-cache-retention",
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
                    elif token == "--prompt-cache-key":
                        flag_tokens.append((token, value))
                    elif token == "--prompt-cache-retention":
                        if value not in {"auto", "in_memory", "24h", "off"}:
                            print("Prompt cache retention must be auto, in_memory, 24h, or off.")
                            malformed_flag = True
                            break
                        flag_tokens.append((token, value))
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
                elif flag == "--context":
                    current_include_context = True
                    current_full_context = False
                    print("Context history turned ON.")
                elif flag == "--no-context":
                    current_include_context = False
                    current_full_context = False
                    print("Context history turned OFF.")
                elif flag == "--full-context":
                    current_include_context = True
                    current_full_context = True
                    print("Full context history turned ON.")
                elif flag == "--raw":
                    raw_prompt = True
                    print("Raw prompt mode enabled for this query.")
                elif flag == "--remote-search":
                    remote_search = True
                    print("Directory queries will use OpenAI file_search vector stores.")
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
                elif flag == "--prompt-cache-key":
                    current_prompt_cache_key = value
                    print(f"Prompt cache key set to {current_prompt_cache_key}.")
                elif flag == "--prompt-cache-retention":
                    current_prompt_cache_retention = value
                    print(f"Prompt cache retention set to {current_prompt_cache_retention}.")
            # If only flags were provided, reprint the header with updated settings.
            if not query_tokens:
                if file_paths or image_paths or blob_paths or directory_paths:
                    print("Upload flags apply to a query. Add a prompt after the upload paths.")
                if raw_prompt:
                    print("Raw prompt mode applies to a query. Add a prompt after --raw.")
                print(format_mode_header(current_reasoning_effort, current_debug_mode, current_width, current_web_search))
            else:
                # Otherwise, join query tokens into a query string and process it.
                query = " ".join(query_tokens)
                try:
                    funcs = ai_functions()
                    funcs["single_query"](
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
                        remote_search=remote_search,
                        allow_partial_index=allow_partial_index,
                        wait_index=wait_index,
                        output_style=current_style,
                        no_color=current_no_color,
                        heartbeat_seconds=current_heartbeat_seconds,
                        idle_timeout_seconds=current_idle_timeout_seconds,
                        request_timeout_seconds=current_request_timeout_seconds,
                        prompt_cache_key=current_prompt_cache_key,
                        prompt_cache_retention=current_prompt_cache_retention,
                        include_context=current_include_context,
                        full_context=current_full_context,
                        raw_prompt=raw_prompt,
                    )
                except ValueError as exc:
                    print(exc)
    except (KeyboardInterrupt, EOFError):
        print("\nExiting interactive mode.")

def parse_args():
    argv = sys.argv[1:]
    subcmds = {
        "query", "remember", "view-memory", "memories", "forget-memory", "forget",
        "edit-memory", "update-memory", "export-memory",
        "sync-directory", "index-status", "index-list", "index-delete",
        "index-expire", "index-duplicates", "doctor", "update",
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
    global_parser.add_argument("--context", dest="include_context", action="store_true",
                               default=True,
                               help="Send recent sanitized context.txt history and permanent memory context")
    global_parser.add_argument("--no-context", dest="include_context", action="store_false",
                               default=argparse.SUPPRESS,
                               help="Do not send recent context.txt history or permanent memory context")
    global_parser.add_argument("--full-context", dest="full_context", action="store_true",
                               default=False,
                               help="Send selected context.txt blocks with usage stats, sources, and metadata")
    global_parser.add_argument("--raw", dest="raw_prompt", action="store_true",
                               default=False,
                               help="Send only the typed prompt: no system message, context, tools, web search, prompt cache, files, or directories")
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
                               help="Recursively search DIR with a local OCR/text cache, up to 5000 files, and send selected snippets. Docs: https://platform.openai.com/docs/guides/tools-file-search/")
    global_parser.add_argument("--remote-search", dest="remote_search", action="store_true",
                               default=argparse.SUPPRESS,
                               help="Use OpenAI file_search vector stores for --directory instead of local preflight search")
    global_parser.add_argument("--index-concurrency", dest="index_concurrency", type=positive_int,
                               default=argparse.SUPPRESS, metavar="N",
                               help=f"Concurrent file indexing uploads for directory syncs (default: {DEFAULT_INDEX_CONCURRENCY})")
    global_parser.add_argument("--allow-partial-index", dest="allow_partial_index", action="store_true",
                               default=argparse.SUPPRESS,
                               help="With --remote-search, proceed without prompting when the vector index is incomplete and start a background sync")
    global_parser.add_argument("--wait-index", dest="wait_index", action="store_true",
                               default=argparse.SUPPRESS,
                               help="With --remote-search, sync the directory vector index before asking the model")
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
    global_parser.add_argument("--prompt-cache-key", dest="prompt_cache_key",
                               default=argparse.SUPPRESS, metavar="KEY",
                               help="Override the stable OpenAI prompt_cache_key used to improve prompt cache routing")
    global_parser.add_argument("--prompt-cache-retention", dest="prompt_cache_retention",
                               choices=["auto", "in_memory", "24h", "off"],
                               default=argparse.SUPPRESS,
                               help=f"Prompt cache retention policy (default: {DEFAULT_PROMPT_CACHE_RETENTION}; auto uses 24h for GPT-5-family models)")
    
    # Create the main parser.
    parser = TopLevelHelpParser(
        prog="gpt",
        description=(
            "CLI Help Agent with context/memory management, web search, and tool use"
        ),
        parents=[global_parser],
        prefix_chars='-+'
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="command",
        parser_class=argparse.ArgumentParser,
    )
    parser.command_groups = [
        ("general commands:", [
            ("query", "Run a one-off query"),
            ("doctor", "Check Python packages, API key, and local document/OCR tools"),
            ("update", "Update cligpt, Python deps, and optionally system tools"),
        ]),
        ("memory commands:", [
            ("remember", "Save a permanent memory"),
            ("view-memory", "View permanent memories"),
            ("memories", "View permanent memories"),
            ("forget-memory", "Forget a permanent memory by its stable ID"),
            ("forget", "Alias for forget-memory"),
            ("edit-memory", "Replace a permanent memory by its stable ID"),
            ("update-memory", "Alias for edit-memory"),
            ("export-memory", "Export permanent memories to a file"),
        ]),
        ("file/directory commands:", [
            ("sync-directory", "Synchronize a directory into its reusable file-search index"),
            ("index-status", "Show local sync status for a directory search index"),
            ("index-list", "List OpenAI vector stores and storage usage"),
            ("index-delete", "Delete an OpenAI vector store by id"),
            ("index-expire", "Set vector store expiration by id"),
            ("index-duplicates", "List likely duplicate cligpt vector stores"),
        ]),
    ]
    
    # Define subcommands.
    parser_query = subparsers.add_parser("query", parents=[global_parser],
                                         help="Run a one-off query", prefix_chars='-+')
    parser_query.add_argument("prompt", type=str, help="User prompt to query the AI")
    parser_query.add_argument("-m", "--model", dest="model", default=MODEL,
                              help="Select model to use")
    
    parser_remember = subparsers.add_parser(
        "remember",
        help="Save a permanent memory",
        description=(
            "Save a permanent memory. Use 'key: value' format, for example: "
            "gpt remember 'name: Dave'"
        ),
        prefix_chars='-+',
    )
    parser_remember.add_argument("text", type=str, help="Memory in 'key: value' format")
    
    for command_name in ("view-memory", "memories"):
        subparsers.add_parser(
            command_name,
            help="View permanent memories",
            description="List permanent memories with their stable IDs.",
            prefix_chars='-+',
        )
    
    for command_name in ("forget-memory", "forget"):
        parser_forget = subparsers.add_parser(
            command_name,
            help="Forget a permanent memory by its stable ID",
            description=(
                "Delete one permanent memory by ID. Run 'gpt view-memory' first "
                "to find the ID."
            ),
            prefix_chars='-+',
        )
        parser_forget.add_argument("id", type=int, help="Stable ID of the permanent memory to forget")

    for command_name in ("edit-memory", "update-memory"):
        parser_edit = subparsers.add_parser(
            command_name,
            help="Replace a permanent memory by its stable ID",
            description=(
                "Replace one permanent memory by ID. Run 'gpt view-memory' first "
                "to find the ID, then pass the corrected memory in 'key: value' format."
            ),
            prefix_chars='-+',
        )
        parser_edit.add_argument("id", type=int, help="Stable ID of the permanent memory to update")
        parser_edit.add_argument("text", type=str, help="Replacement memory in 'key: value' format")
    
    parser_export = subparsers.add_parser(
        "export-memory",
        help="Export permanent memories to a file",
        description="Write permanent memories to a JSON file.",
        prefix_chars='-+',
    )
    parser_export.add_argument("output", type=str, help="Output file path")

    parser_sync = subparsers.add_parser("sync-directory",
                                        help="Synchronize a directory into its reusable file-search index",
                                        prefix_chars='-+')
    parser_sync.add_argument("directory", nargs="+", help="Directory path(s) to sync")
    parser_sync.add_argument("--index-concurrency", dest="index_concurrency", type=positive_int,
                             default=DEFAULT_INDEX_CONCURRENCY, metavar="N",
                             help=f"Concurrent file indexing uploads (default: {DEFAULT_INDEX_CONCURRENCY})")

    parser_status = subparsers.add_parser("index-status",
                                          help="Show local sync status for a directory search index",
                                          prefix_chars='-+')
    parser_status.add_argument("directory", nargs="+", help="Directory path(s) to inspect")

    subparsers.add_parser("index-list",
                          help="List OpenAI vector stores and storage usage",
                          prefix_chars='-+')

    parser_delete = subparsers.add_parser("index-delete",
                                          help="Delete an OpenAI vector store by id",
                                          prefix_chars='-+')
    parser_delete.add_argument("vector_store_id", help="Vector store id, e.g. vs_...")

    parser_expire = subparsers.add_parser("index-expire",
                                          help="Set vector store expiration by id",
                                          prefix_chars='-+')
    parser_expire.add_argument("vector_store_id", help="Vector store id, e.g. vs_...")
    parser_expire.add_argument("--days", type=positive_int,
                               default=DEFAULT_VECTOR_STORE_EXPIRATION_DAYS,
                               help=f"Days after last_active_at before expiration (default: {DEFAULT_VECTOR_STORE_EXPIRATION_DAYS})")

    subparsers.add_parser("index-duplicates",
                          help="List likely duplicate cligpt vector stores",
                          prefix_chars='-+')

    subparsers.add_parser(
        "doctor",
        help="Check Python packages, API key, and local document/OCR tools",
        description=(
            "Run a read-only environment check. Doctor reports required Python "
            "packages, OPENAI_API_KEY, and optional document/OCR/blob tools, then "
            "prints suggested install commands for missing system tools."
        ),
        prefix_chars='-+',
    )

    parser_update = subparsers.add_parser(
        "update",
        help="Update cligpt, Python deps, and optionally system tools",
        description=(
            "Update the local cligpt checkout and Python virtual environment. "
            "With --system, update also installs missing optional system tools "
            "through the detected package manager, and asks before installing "
            "AUR packages."
        ),
        prefix_chars='-+',
    )
    parser_update.add_argument("--system", action="store_true",
                               help="Install missing system tools with the detected package manager")
    parser_update.add_argument("--skip-git", action="store_true",
                               help="Do not run git pull")
    parser_update.add_argument("--skip-pip", action="store_true",
                               help="Do not update Python requirements")
    parser_update.add_argument("--dry-run", action="store_true",
                               help="Print update/install commands without executing them")
    
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
    if not hasattr(args, "include_context"):
        args.include_context = True
    if not hasattr(args, "full_context"):
        args.full_context = False
    if not hasattr(args, "raw_prompt"):
        args.raw_prompt = False
    if args.full_context:
        args.include_context = True
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
    if not hasattr(args, "remote_search"):
        args.remote_search = False
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
    if not hasattr(args, "prompt_cache_key"):
        args.prompt_cache_key = DEFAULT_PROMPT_CACHE_KEY
    if not hasattr(args, "prompt_cache_retention"):
        args.prompt_cache_retention = DEFAULT_PROMPT_CACHE_RETENTION
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
            args.prompt_cache_key,
            args.prompt_cache_retention,
            args.include_context,
            args.full_context,
        )
    elif args.command == "query":
        try:
            funcs = ai_functions()
            funcs["single_query"](
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
                remote_search=args.remote_search,
                allow_partial_index=args.allow_partial_index,
                wait_index=args.wait_index,
                output_style=args.output_style,
                no_color=args.no_color,
                heartbeat_seconds=args.heartbeat_seconds,
                idle_timeout_seconds=args.idle_timeout_seconds,
                request_timeout_seconds=args.request_timeout_seconds,
                prompt_cache_key=args.prompt_cache_key,
                prompt_cache_retention=args.prompt_cache_retention,
                include_context=args.include_context,
                full_context=args.full_context,
                raw_prompt=args.raw_prompt,
            )
        except ValueError as exc:
            print(exc)
    elif args.command == "remember":
        try:
            entry = add_permanent_memory(args.text)
            print(f"Added permanent memory [{entry['id']}] at {entry['timestamp']}.")
        except ValueError as e:
            print(e)
    elif args.command in {"view-memory", "memories"}:
        memories = view_permanent_memory()
        if memories:
            for mem in memories:
                print(mem)
        else:
            print("No permanent memories found.")
    elif args.command in {"forget-memory", "forget"}:
        try:
            forget_permanent_memory(args.id)
            print(f"Permanent memory with id {args.id} has been removed.")
        except ValueError as e:
            print(e)
    elif args.command in {"edit-memory", "update-memory"}:
        try:
            entry = update_permanent_memory(args.id, args.text)
            print(f"Updated permanent memory [{entry['id']}] at {entry['timestamp']}.")
        except ValueError as e:
            print(e)
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
        funcs = ai_functions()
        funcs["sync_directory_vector_stores"](args.directory, index_concurrency=args.index_concurrency)
    elif args.command == "index-status":
        funcs = ai_functions()
        funcs["print_directory_status"](args.directory)
    elif args.command == "index-list":
        funcs = ai_functions()
        funcs["print_index_list"]()
    elif args.command == "index-delete":
        funcs = ai_functions()
        funcs["delete_index"](args.vector_store_id)
    elif args.command == "index-expire":
        funcs = ai_functions()
        funcs["expire_index"](args.vector_store_id, days=args.days)
    elif args.command == "index-duplicates":
        funcs = ai_functions()
        funcs["print_index_duplicates"]()
    elif args.command == "doctor":
        raise SystemExit(doctor())
    elif args.command == "update":
        raise SystemExit(update(
            skip_git=args.skip_git,
            skip_pip=args.skip_pip,
            install_system=args.system,
            dry_run=args.dry_run,
        ))

if __name__ == "__main__":
    main()
