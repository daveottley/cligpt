# ai_client.py

import sys
import os
import platform
import json
import shutil
import datetime
import uuid
import mimetypes
import subprocess
import tempfile
import hashlib
import string
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import pathname2url
from openai import OpenAI
from config import (
    MAX_CONTEXT_TOKENS,
    MODEL,
    FAST_MODEL,
    SYSTEM_MESSAGE_FILE,
    SOURCES_DIR,
    MAX_UPLOAD_FILES,
)
from memory_manager import (
        get_neofetch_output,
        prune_context,
        add_to_context,
)

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TOPIC_TAG_SCHEMA = {
    "type": "json_schema",
    "name": "topic_tags",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 5 concise topic tags, broad to specific."
            }
        },
        "required": ["topics"],
        "additionalProperties": False
    }
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PDF_EXTENSIONS = {".pdf"}
TEXT_EXTENSIONS = {
    ".txt", ".text", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json",
    ".jsonl", ".yaml", ".yml", ".toml", ".xml", ".html", ".htm", ".css",
    ".js", ".jsx", ".ts", ".tsx", ".py", ".rb", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".cs", ".php", ".swift",
    ".kt", ".kts", ".scala", ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".bat", ".cmd", ".sql", ".ini", ".cfg", ".conf", ".log", ".dockerfile",
    ".makefile", ".mk", ".lua", ".vim", ".el", ".lisp", ".clj", ".ex",
    ".exs", ".erl", ".hrl", ".fs", ".fsx", ".r", ".m", ".pl", ".pm",
}
LIBREOFFICE_EXTENSIONS = {
    ".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm",
    ".ppt", ".pptx", ".pptm", ".pot", ".potx", ".potm", ".pps", ".ppsx", ".ppsm",
    ".odt", ".ott", ".fodt", ".ods", ".ots", ".fods", ".odp", ".otp", ".fodp",
    ".odg", ".otg", ".fodg", ".odf", ".rtf", ".wpd", ".wps",
    ".sxw", ".stw", ".sxc", ".stc", ".sxi", ".sti", ".sxd", ".std",
}
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
PDF_MIME_TYPES = {"application/pdf"}
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {
    "application/json",
    "application/x-ndjson",
    "application/xml",
    "application/x-yaml",
    "application/toml",
    "application/javascript",
    "application/x-sh",
}
LIBREOFFICE_COMMAND = shutil.which("libreoffice") or shutil.which("soffice")
FILE_COMMAND = shutil.which("file")
BINWALK_COMMAND = shutil.which("binwalk")
HEX_PREVIEW_BYTES = 512
STRING_PREVIEW_LIMIT = 200
STRING_MIN_LENGTH = 4

def supports_reasoning_effort(model):
    """Return whether the model family accepts the reasoning_effort parameter."""
    return model.startswith(("gpt-5", "o1", "o3", "o4"))

def normalize_paths(paths):
    return [path for path in (paths or []) if path]

def file_magic_kind(path):
    try:
        with open(path, "rb") as f:
            header = f.read(16)
    except OSError:
        return None

    if header.startswith(b"%PDF"):
        return "file"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if header.startswith(b"\xff\xd8\xff"):
        return "image"
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return "image"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image"
    return None

def classify_upload_path(path, requested_kind=None):
    suffix = os.path.splitext(path)[1].lower()
    mime_type, _ = mimetypes.guess_type(path)
    magic_kind = file_magic_kind(path)
    is_text = (
        suffix in TEXT_EXTENSIONS
        or mime_type in TEXT_MIME_TYPES
        or (mime_type or "").startswith(TEXT_MIME_PREFIXES)
    )
    is_office = suffix in LIBREOFFICE_EXTENSIONS

    if requested_kind == "image":
        if suffix in IMAGE_EXTENSIONS or mime_type in IMAGE_MIME_TYPES or magic_kind == "image":
            return "image"
        raise ValueError(f"--image only accepts PNG, JPEG, WEBP, or non-animated GIF files: {path}")

    if requested_kind == "file":
        if suffix in PDF_EXTENSIONS or mime_type in PDF_MIME_TYPES or magic_kind == "file":
            return "file"
        if is_office:
            return "office"
        if is_text:
            return "text"
        raise ValueError(
            "--file accepts PDFs, LibreOffice-convertible documents, and raw text/code files: "
            f"{path}"
        )

    if requested_kind == "blob":
        return "blob"

    if suffix in IMAGE_EXTENSIONS or mime_type in IMAGE_MIME_TYPES or magic_kind == "image":
        return "image"
    if suffix in PDF_EXTENSIONS or mime_type in PDF_MIME_TYPES or magic_kind == "file":
        return "file"
    if is_office:
        return "office"
    if is_text:
        return "text"
    return None

def iter_directory_files(directory):
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(
            dirname for dirname in dirs
            if not dirname.startswith(".") and dirname != "__pycache__"
        )
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            yield os.path.join(root, filename)

def collect_uploads(file_paths=None, image_paths=None, directory_paths=None, blob_paths=None):
    uploads = []
    seen_paths = set()

    def add_path(path, requested_kind=None, skip_unsupported=False, blob_fallback=False):
        full_path = os.path.abspath(os.path.expanduser(path))
        if full_path in seen_paths:
            return
        if not os.path.isfile(full_path):
            raise ValueError(f"Upload path is not a file: {path}")
        kind = classify_upload_path(full_path, requested_kind=requested_kind)
        if kind is None:
            if blob_fallback:
                kind = "blob"
            elif skip_unsupported:
                return
            else:
                raise ValueError(f"Unsupported upload file type: {path}")
        uploads.append({"path": full_path, "kind": kind})
        seen_paths.add(full_path)
        if len(uploads) > MAX_UPLOAD_FILES:
            raise ValueError(f"Too many upload files. The hard limit is {MAX_UPLOAD_FILES}.")

    for path in normalize_paths(file_paths):
        add_path(path, requested_kind="file")
    for path in normalize_paths(image_paths):
        add_path(path, requested_kind="image")
    for path in normalize_paths(blob_paths):
        add_path(path, requested_kind="blob")
    for directory in normalize_paths(directory_paths):
        full_directory = os.path.abspath(os.path.expanduser(directory))
        if not os.path.isdir(full_directory):
            raise ValueError(f"--directory path is not a directory: {directory}")
        for path in iter_directory_files(full_directory):
            add_path(path, blob_fallback=True)

    return uploads

def upload_file_for_response(upload):
    purpose = "vision" if upload["kind"] == "image" else "user_data"
    with open(upload["path"], "rb") as f:
        uploaded = client.files.create(file=f, purpose=purpose)
    return {
        "path": upload["path"],
        "kind": upload["kind"],
        "file_id": uploaded.id,
    }

def convert_office_to_pdf(path, output_directory):
    if not LIBREOFFICE_COMMAND:
        raise ValueError(
            "LibreOffice is required to convert this file to PDF, but no libreoffice/soffice "
            f"command was found: {path}"
        )

    profile_directory = os.path.join(output_directory, "lo-profile")
    os.makedirs(profile_directory, exist_ok=True)
    profile_uri = "file://" + pathname2url(os.path.abspath(profile_directory))

    result = subprocess.run(
        [
            LIBREOFFICE_COMMAND,
            "--headless",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "pdf",
            "--outdir",
            output_directory,
            path,
        ],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ValueError(f"LibreOffice failed to convert {path} to PDF: {detail}")

    expected = os.path.join(
        output_directory,
        os.path.splitext(os.path.basename(path))[0] + ".pdf",
    )
    if os.path.exists(expected):
        return expected

    converted = [
        os.path.join(output_directory, filename)
        for filename in os.listdir(output_directory)
        if filename.lower().endswith(".pdf")
    ]
    if converted:
        return max(converted, key=os.path.getmtime)
    raise ValueError(f"LibreOffice did not produce a PDF for {path}")

def read_text_attachment(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return {
        "path": path,
        "kind": "text",
        "text": text,
    }

def bytes_to_hex_lines(data, start_offset=0, width=16):
    lines = []
    for index in range(0, len(data), width):
        chunk = data[index:index + width]
        hex_bytes = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_text = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in chunk)
        lines.append(f"{start_offset + index:08x}  {hex_bytes:<47}  {ascii_text}")
    return "\n".join(lines)

def printable_strings(data, min_length=STRING_MIN_LENGTH):
    allowed = set(bytes(string.printable, "ascii")) - {0x0b, 0x0c}
    results = []
    current = bytearray()
    for byte in data:
        if byte in allowed and byte not in {0x0a, 0x0d, 0x09}:
            current.append(byte)
        else:
            if len(current) >= min_length:
                results.append(current.decode("ascii", errors="replace"))
            current = bytearray()
    if len(current) >= min_length:
        results.append(current.decode("ascii", errors="replace"))
    return results

def run_optional_command(command):
    if not command[0]:
        return "unavailable"
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return f"unavailable: {exc}"
    output = (result.stdout or result.stderr or "").strip()
    return output if output else f"exit code {result.returncode}, no output"

def build_blob_report(path):
    stat = os.stat(path)
    with open(path, "rb") as f:
        data = f.read()

    sha256 = hashlib.sha256(data).hexdigest()
    sha1 = hashlib.sha1(data).hexdigest()
    md5 = hashlib.md5(data).hexdigest()
    first_bytes = data[:HEX_PREVIEW_BYTES]
    last_bytes = data[-HEX_PREVIEW_BYTES:] if len(data) > HEX_PREVIEW_BYTES else b""
    strings_preview = printable_strings(data)[:STRING_PREVIEW_LIMIT]
    mime_type, encoding = mimetypes.guess_type(path)
    file_output = run_optional_command([FILE_COMMAND, "--brief", path]) if FILE_COMMAND else "unavailable"
    binwalk_output = run_optional_command([BINWALK_COMMAND, path]) if BINWALK_COMMAND else "unavailable"

    lines = [
        f"Binary blob analysis report for: {path}",
        f"Size: {stat.st_size} bytes",
        f"MIME guess: {mime_type or 'unknown'}",
        f"Encoding guess: {encoding or 'unknown'}",
        f"file(1): {file_output}",
        f"SHA256: {sha256}",
        f"SHA1: {sha1}",
        f"MD5: {md5}",
        "",
        f"First {len(first_bytes)} bytes:",
        bytes_to_hex_lines(first_bytes),
    ]
    if last_bytes:
        lines.extend([
            "",
            f"Last {len(last_bytes)} bytes:",
            bytes_to_hex_lines(last_bytes, start_offset=max(0, len(data) - len(last_bytes))),
        ])
    lines.extend([
        "",
        f"Printable strings, first {len(strings_preview)} of up to {STRING_PREVIEW_LIMIT}:",
    ])
    lines.extend(strings_preview or ["None found."])
    lines.extend([
        "",
        "binwalk:",
        binwalk_output,
    ])
    return "\n".join(lines)

def read_blob_attachment(path):
    return {
        "path": path,
        "kind": "blob",
        "text": build_blob_report(path),
    }

def upload_attachments(file_paths=None, image_paths=None, directory_paths=None, blob_paths=None):
    uploads = collect_uploads(file_paths, image_paths, directory_paths, blob_paths)
    attachments = []
    with tempfile.TemporaryDirectory(prefix="cligpt-upload-") as temp_directory:
        for upload in uploads:
            if upload["kind"] == "text":
                attachments.append(read_text_attachment(upload["path"]))
            elif upload["kind"] == "blob":
                attachments.append(read_blob_attachment(upload["path"]))
            elif upload["kind"] == "office":
                conversion_directory = tempfile.mkdtemp(dir=temp_directory)
                pdf_path = convert_office_to_pdf(upload["path"], conversion_directory)
                uploaded = upload_file_for_response({"path": pdf_path, "kind": "file"})
                uploaded["source_path"] = upload["path"]
                uploaded["converted_path"] = pdf_path
                attachments.append(uploaded)
            else:
                attachments.append(upload_file_for_response(upload))
    return attachments

def build_user_content(user_prompt, uploaded_attachments):
    content = [{"type": "input_text", "text": user_prompt}]
    for attachment in uploaded_attachments:
        if attachment["kind"] == "image":
            content.append({"type": "input_image", "file_id": attachment["file_id"]})
        elif attachment["kind"] in {"text", "blob"}:
            label = "binary blob report" if attachment["kind"] == "blob" else "text file"
            content.append({
                "type": "input_text",
                "text": (
                    f"\n\n--- Begin attached {label}: {attachment['path']} ---\n"
                    f"{attachment['text']}\n"
                    f"--- End attached {label}: {attachment['path']} ---"
                ),
            })
        else:
            content.append({"type": "input_file", "file_id": attachment["file_id"]})
    return content

def resolve_output_width(width=None):
    if width:
        return max(1, width), "flag"
    return max(1, shutil.get_terminal_size((80, 20)).columns - 1), "terminal"

def get_nested_attr(obj, *names, default=None):
    current = obj
    for name in names:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return current if current is not None else default

def extract_response_text(response):
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    chunks = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
    return "".join(chunks)

def source_to_text(source):
    if isinstance(source, dict):
        return json.dumps(source, ensure_ascii=False, sort_keys=True)
    if hasattr(source, "model_dump"):
        return json.dumps(source.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    if hasattr(source, "__dict__"):
        return json.dumps(vars(source), ensure_ascii=False, sort_keys=True, default=str)
    return str(source)

def response_output_items(response):
    return get_nested_attr(response, "output", default=[]) or []

def content_items(item):
    return get_nested_attr(item, "content", default=[]) or []

def annotation_items(content):
    return get_nested_attr(content, "annotations", default=[]) or []

def comparable_url(url):
    if not url:
        return url
    parts = urlsplit(url)
    filtered_query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
        ],
        doseq=True,
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, filtered_query, ""))

def collect_final_answer_citations(response):
    citations = []
    seen_urls = set()

    for item in response_output_items(response):
        for content in content_items(item):
            for annotation in annotation_items(content):
                url = get_nested_attr(annotation, "url")
                comparable = comparable_url(url)
                if not url or comparable in seen_urls:
                    continue
                title = get_nested_attr(annotation, "title", default=url)
                citations.append({"title": title, "url": url, "text": source_to_text(annotation)})
                seen_urls.add(comparable)

    return citations

def collect_uncited_web_sources(response, cited_urls):
    sources = []
    seen_urls = {comparable_url(url) for url in cited_urls}

    for item in response_output_items(response):
        action_sources = get_nested_attr(item, "action", "sources", default=[]) or []
        for source in action_sources:
            url = get_nested_attr(source, "url")
            comparable = comparable_url(url)
            if not url or comparable in seen_urls:
                continue
            title = get_nested_attr(source, "title", default=url)
            sources.append({"title": title, "url": url, "text": source_to_text(source)})
            seen_urls.add(comparable)

    return sources

def source_file_path(response_id):
    return os.path.join(SOURCES_DIR, f"{response_id}.txt")

def source_file_link(response_id):
    return f"sources/{response_id}.txt"

def save_source_block(response_id, block):
    os.makedirs(SOURCES_DIR, exist_ok=True)
    path = source_file_path(response_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(block + "\n")
    return path

def log_uncited_web_sources(response_id, user_prompt, sources):
    if not sources:
        return None

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"Response ID: {response_id}",
        f"Timestamp: {timestamp}",
        "Uncited web-search sources returned by the OpenAI web search tool.",
        "These sources were not final-answer citations.",
        f">>> {user_prompt}",
    ]
    for index, source in enumerate(sources, start=1):
        lines.extend([
            f"[{index}]",
            f"Title: {source['title']}",
            f"URL: {source['url']}",
            f"Complete source text: {source['text']}",
        ])
    return save_source_block(response_id, "\n".join(lines))

class StreamingLineWrapper:
    def __init__(self, width):
        self.width = max(1, width)
        self.line = ""
        self.word = ""

    def feed(self, text):
        output = []
        for char in text:
            if char == "\n":
                self._flush_word(output)
                output.append(self.line.rstrip() + "\n" if self.line else "\n")
                self.line = ""
            elif char.isspace():
                self._flush_word(output)
            else:
                self.word += char
        return "".join(output)

    def finish(self):
        output = []
        self._flush_word(output)
        if self.line:
            output.append(self.line.rstrip())
            self.line = ""
        return "".join(output)

    def _flush_word(self, output):
        if not self.word:
            return

        word = self.word
        self.word = ""

        while len(word) > self.width:
            if self.line:
                output.append(self.line.rstrip() + "\n")
                self.line = ""
            output.append(word[:self.width] + "\n")
            word = word[self.width:]

        if not word:
            return
        if not self.line:
            self.line = word
        elif len(self.line) + 1 + len(word) <= self.width:
            self.line += " " + word
        else:
            output.append(self.line.rstrip() + "\n")
            self.line = word

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

def extract_topic_tags(user_prompt, answer_text, model=FAST_MODEL):
    response = client.responses.create(
        model=model,
        instructions=(
            "Extract up to 5 concise topic tags from the user's prompt and "
            "assistant answer. Use broad-to-specific tags. Return only the "
            "structured data requested."
        ),
        input=[
            {
                "role": "user",
                "content": (
                    f"User prompt:\n{user_prompt}\n\n"
                    f"Assistant answer:\n{answer_text}"
                ),
            }
        ],
        max_output_tokens=120,
        text={"format": TOPIC_TAG_SCHEMA},
        store=False,
    )

    raw_content = extract_response_text(response)
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        return []

    topics = parsed.get("topics", [])
    if not isinstance(topics, list):
        return []
    return [str(topic).strip() for topic in topics if str(topic).strip()][:5]

def single_query(
    user_prompt,
    reasoning_effort="medium",
    debug=False,
    model=None,
    width=None,
    web_search=True,
    file_paths=None,
    image_paths=None,
    directory_paths=None,
    blob_paths=None,
):
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
    response_id = str(uuid.uuid4())
    output_width, width_source = resolve_output_width(width)
    uploaded_attachments = upload_attachments(file_paths, image_paths, directory_paths, blob_paths)

    system_message = load_system_message()
    pruned_context, chat_blocks, topic_tags, oldest_block = prune_context(user_prompt)
    
    def estimate_tokens_local(text):
        return len(text.split())
    system_tokens = estimate_tokens_local(system_message)
    context_tokens = estimate_tokens_local(pruned_context)
    user_tokens = estimate_tokens_local(user_prompt)
    total_context_tokens = system_tokens + context_tokens + user_tokens
    attachment_count = len(uploaded_attachments)

    # Build header
    web_label = "web:on" if web_search else "web:off"
    header_basic = f"[{model} - {reasoning_effort} - {web_label} - width: {output_width} ({width_source})]"
    debug_header = (f"[Context Tokens: {total_context_tokens}]\n"
                    f"  [System Message: {system_tokens}]\n"
                    f"  [Pruned Context: {context_tokens}]\n"
                    f"    [Chat Blocks: {chat_blocks}]\n"
                    f"    [Topic Tags: {topic_tags}]\n"
                    f"    [Oldest Block: {oldest_block}]\n"
                    f"  [User Prompt: {user_tokens}]\n"
                    f"  [Web Search: {'enabled' if web_search else 'disabled'}]\n"
                    f"  [Attachments: {attachment_count}]\n"
                    f"  [Output Width: {output_width} ({width_source})]\n")
    
    if debug:
        sys.stdout.write(header_basic + "\n" + debug_header)
    else:
        sys.stdout.write(header_basic + "\n")
    sys.stdout.flush()
        
    combined_system = (
        system_message
        + "\n\n"
        + pruned_context
        + "\n\n"
        + (
            f"Format the visible answer for a terminal with a hard maximum "
            f"line length of {output_width} characters. Prefer lines as close "
            f"to {output_width} characters as natural wording allows. Do not "
            f"use lines longer than {output_width} characters."
            f"\n\nWeb search is {'enabled' if web_search else 'disabled'} for "
            f"this request. When web search is enabled, use it for current, "
            f"fast-changing, or source-sensitive facts. If web search is used, "
            f"make source URLs visible in the answer."
        )
    )
    
    request_args = {
        "model": model,
        "instructions": combined_system,
        "input": [{"role": "user", "content": build_user_content(user_prompt, uploaded_attachments)}],
        "max_output_tokens": MAX_CONTEXT_TOKENS,
        "stream": True,
        "text": {"format": {"type": "text"}},
        "store": True
    }
    if web_search:
        request_args["tools"] = [{"type": "web_search"}]
        request_args["tool_choice"] = "auto"
        request_args["include"] = ["web_search_call.action.sources"]
    if supports_reasoning_effort(model):
        request_args["reasoning"] = {"effort": reasoning_effort}

    stream = client.responses.create(**request_args)

    visible_chunks = []
    reasoning_tokens_used = 0
    completed_response = None
    wrapper = StreamingLineWrapper(output_width)

    if debug:
        sys.stdout.write("[streaming response]\n\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()

    for event in stream:
        event_type = getattr(event, "type", "")
        if event_type == "response.output_text.delta":
            content = getattr(event, "delta", "")
            visible_text = wrapper.feed(content)
            if visible_text:
                visible_chunks.append(visible_text)
                sys.stdout.write(visible_text)
                sys.stdout.flush()
        elif event_type == "response.refusal.delta":
            content = getattr(event, "delta", "")
            visible_text = wrapper.feed(content)
            if visible_text:
                visible_chunks.append(visible_text)
                sys.stdout.write(visible_text)
                sys.stdout.flush()
        elif event_type == "response.completed":
            completed_response = getattr(event, "response", None)
            reasoning_tokens_used = (
                get_nested_attr(
                    event,
                    "response",
                    "usage",
                    "output_tokens_details",
                    "reasoning_tokens",
                    default=0,
                )
                or 0
            )
        elif event_type in {"response.failed", "error", "response.error"}:
            error = get_nested_attr(event, "response", "error") or getattr(event, "error", None)
            raise RuntimeError(f"OpenAI response stream failed: {error}")

    final_text = wrapper.finish()
    if final_text:
        visible_chunks.append(final_text)
        sys.stdout.write(final_text)

    answer_text = "".join(visible_chunks)

    final_citations = collect_final_answer_citations(completed_response) if completed_response else []
    if final_citations:
        sys.stdout.write("\n\nSources:\n")
        for index, citation in enumerate(final_citations, start=1):
            sys.stdout.write(f"[{index}] {citation['title']}: {citation['url']}\n")

    cited_urls = {citation["url"] for citation in final_citations}
    uncited_sources = (
        collect_uncited_web_sources(completed_response, cited_urls)
        if completed_response else []
    )
    if log_uncited_web_sources(response_id, user_prompt, uncited_sources):
        note = (
            "\n*Additional web-search sources were returned but not cited in "
            f"the final answer, logged in your [context]({source_file_link(response_id)}).*"
        )
        sys.stdout.write(note + "\n")
        answer_text += note

    if debug:
        sys.stdout.write(f"\n\n[Reasoning Tokens: {reasoning_tokens_used}]\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()
    
    try:
        topics = extract_topic_tags(user_prompt, answer_text)
    except Exception as exc:
        if debug:
            sys.stderr.write(f"[Topic tag extraction failed: {exc}]\n")
        topics = []
    add_to_context(user_prompt, answer_text, topics, reasoning_effort, response_id=response_id)
    return answer_text
