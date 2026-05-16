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
import time
import threading
import queue
from collections import Counter
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import pathname2url
from openai import OpenAI
from config import (
    MAX_OUTPUT_TOKENS,
    MODEL,
    FAST_MODEL,
    SYSTEM_MESSAGE_FILE,
    CONTEXT_FILE,
    SOURCES_DIR,
    MAX_UPLOAD_FILES,
    MAX_DIRECTORY_FILES,
    MAX_DIRECT_VISION_UPLOADS,
    MAX_UPLOAD_RETRIES,
    DEFAULT_VECTOR_STORE_EXPIRATION_DAYS,
    DEFAULT_INDEX_CONCURRENCY,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_PROMPT_CACHE_KEY,
    DEFAULT_PROMPT_CACHE_MIN_STABLE_WORDS,
    DEFAULT_PROMPT_CACHE_RETENTION,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_OUTPUT_STYLE,
    CLIGPT_INCLUDE_NEOFETCH,
    MAX_DIRECT_PDF_UPLOAD_BYTES,
    MAX_COMPRESSED_PDF_UPLOAD_BYTES,
)
from memory_manager import (
        get_neofetch_output,
        prune_context,
        add_to_context,
)
from model_capabilities import get_model_capabilities, get_recent_history_token_budget
from render import RenderConfig, TerminalRenderer
from vector_store_manager import VectorStoreManager, root_hash, state_root
from local_search import LocalSearchIndex, build_local_context

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
ARCHIVE_DIRECTORY_MARKERS = {
    "old",
    "archive",
    "archives",
    "archived",
    "former",
    "sold",
    "disposed",
    "closed",
    "historical",
    "inactive",
}

FILE_SEARCH_CALL_PRICE_PER_1000 = 2.50
WEB_SEARCH_CALL_PRICE_PER_1000 = 10.00
PROMPT_CACHE_ANCHOR_SENTENCE = (
    "Prompt cache stability anchor: this fixed text is not user content, "
    "not evidence, and must not be quoted or used to answer. "
)
LOCAL_TOOL_GET_SYSTEM_PROFILE = "get_system_profile"
MAX_LOCAL_TOOL_ROUNDS = 3
CURRENT_DIRECTORY_MARKERS = {
    "current",
    "active",
    "landlord signed",
    "executed",
}
LIBREOFFICE_COMMAND = shutil.which("libreoffice") or shutil.which("soffice")
GHOSTSCRIPT_COMMAND = shutil.which("gs")
OCRMYPDF_COMMAND = shutil.which("ocrmypdf")
PDFINFO_COMMAND = shutil.which("pdfinfo")
PDFTOTEXT_COMMAND = shutil.which("pdftotext")
PDFTOPPM_COMMAND = shutil.which("pdftoppm")
TESSERACT_COMMAND = shutil.which("tesseract")
FILE_COMMAND = shutil.which("file")
BINWALK_COMMAND = shutil.which("binwalk")
HEX_PREVIEW_BYTES = 512
STRING_PREVIEW_LIMIT = 200
STRING_MIN_LENGTH = 4
PDF_TEXT_MIN_CHARS = 500
PDF_OCR_MAX_PAGES = 50
IMAGE_DOCUMENT_TEXT_MIN_CHARS = 80

def supports_reasoning_effort(model):
    """Return whether the selected model accepts reasoning_effort."""
    return bool(get_model_capabilities(model).reasoning_efforts)

def new_upload_client():
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        max_retries=0,
        timeout=120.0,
    )

def sha256_path(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

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

def path_components(path):
    normalized = os.path.normpath(path)
    components = []
    while True:
        head, tail = os.path.split(normalized)
        if tail:
            components.append(tail)
            normalized = head
            continue
        if head and head != os.path.sep:
            components.append(head)
        break
    return list(reversed(components))

def classify_directory_path(path, root_path=None):
    root_path = os.path.abspath(os.path.expanduser(root_path)) if root_path else None
    abs_path = os.path.abspath(os.path.expanduser(path))
    rel_path = os.path.relpath(abs_path, root_path) if root_path else abs_path
    components = [component.lower() for component in path_components(rel_path)]
    archive_hits = [
        component for component in components
        if component in ARCHIVE_DIRECTORY_MARKERS
    ]
    current_hits = [
        component for component in components
        if component in CURRENT_DIRECTORY_MARKERS
    ]
    if archive_hits:
        return {
            "classification": "archive/disposed candidate",
            "reason": f"path contains archive marker: {archive_hits[0]}",
            "current_report_policy": (
                "exclude from current operating reports unless the user explicitly "
                "asks for historical, former, sold, or all properties"
            ),
        }
    if current_hits:
        return {
            "classification": "current/active candidate",
            "reason": f"path contains current marker: {current_hits[0]}",
            "current_report_policy": "eligible for current operating reports",
        }
    return {
        "classification": "active candidate",
        "reason": "no archive/disposed marker in path",
        "current_report_policy": "eligible for current operating reports unless contradicted by document content",
    }

def directory_manifest(root_path, uploads):
    root = os.path.abspath(os.path.expanduser(root_path))
    top_level = {}
    for upload in uploads:
        rel_path = os.path.relpath(upload["path"], root)
        parts = rel_path.split(os.sep)
        top = parts[0] if parts else "."
        role = classify_directory_path(upload["path"], root)
        entry = top_level.setdefault(
            top,
            {
                "file_count": 0,
                "classification": role["classification"],
                "reason": role["reason"],
            },
        )
        entry["file_count"] += 1
        if role["classification"] == "archive/disposed candidate":
            entry["classification"] = role["classification"]
            entry["reason"] = role["reason"]
    archive_roots = []
    active_roots = []
    for name, entry in sorted(top_level.items()):
        line = (
            f"- {name}: {entry['classification']} "
            f"({entry['file_count']} file(s); {entry['reason']})"
        )
        if entry["classification"] == "archive/disposed candidate":
            archive_roots.append(line)
        else:
            active_roots.append(line)
    lines = [
        f"Directory manifest for: {root}",
        "Current-report policy:",
        "- Treat archive/disposed candidate paths as historical and exclude them from current rent rolls, active tenant lists, owned-property summaries, and current operating reports unless the user explicitly asks for historical/all/former/sold properties.",
        "- Prefer direct active/current candidate property roots for current rent rolls.",
        "",
        "Active/current candidate top-level paths:",
    ]
    lines.extend(active_roots or ["- None detected."])
    lines.extend(["", "Archive/disposed candidate top-level paths:"])
    lines.extend(archive_roots or ["- None detected."])
    return "\n".join(lines)

def upload_metadata_header(upload):
    root_path = upload.get("root_path")
    role = classify_directory_path(upload["path"], root_path)
    rel_path = os.path.relpath(upload["path"], root_path) if root_path else upload["path"]
    lines = [
        "cligpt source metadata:",
        f"Source path: {upload['path']}",
        f"Relative path: {rel_path}",
        f"Directory classification: {role['classification']}",
        f"Classification reason: {role['reason']}",
        f"Current-report handling: {role['current_report_policy']}",
        "",
    ]
    return "\n".join(lines)

def collect_uploads(file_paths=None, image_paths=None, directory_paths=None, blob_paths=None):
    uploads = []
    seen_paths = set()
    upload_limit = MAX_DIRECTORY_FILES if directory_paths else MAX_UPLOAD_FILES

    def add_path(path, requested_kind=None, skip_unsupported=False, blob_fallback=False, root_path=None):
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
        upload = {"path": full_path, "kind": kind}
        if root_path:
            upload["root_path"] = os.path.abspath(os.path.expanduser(root_path))
        uploads.append(upload)
        seen_paths.add(full_path)
        if len(uploads) > upload_limit:
            raise ValueError(f"Too many upload files. The hard limit is {upload_limit}.")

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
            add_path(path, blob_fallback=True, root_path=full_directory)

    return uploads

def print_upload_plan(uploads):
    if not uploads:
        return
    counts = Counter(upload["kind"] for upload in uploads)
    total_bytes = sum(os.path.getsize(upload["path"]) for upload in uploads)
    parts = ", ".join(f"{kind}:{counts[kind]}" for kind in sorted(counts))
    sys.stderr.write(
        f"[Preparing {len(uploads)} attachment(s), {total_bytes / (1024 * 1024):.1f} MB total: "
        f"{parts}]\n"
    )
    sys.stderr.flush()

def format_upload_progress(completed, total, succeeded, failed):
    if total <= 0:
        return ""
    width = 24
    filled = int(width * completed / total)
    bar = "#" * filled + "-" * (width - filled)
    return f"[Uploads {completed}/{total} [{bar}] ok:{succeeded} skipped:{failed}]"

def write_upload_progress(completed, total, succeeded, failed, end=False):
    if total <= 0:
        return
    line = format_upload_progress(completed, total, succeeded, failed)
    suffix = "\n" if end else ""
    sys.stderr.write("\r" + line + suffix)
    sys.stderr.flush()

def upload_status(upload, message):
    sys.stderr.write(f"\n[{message}: {upload['path']}]\n")
    sys.stderr.flush()

def describe_exception(exc):
    parts = [f"{type(exc).__name__}: {exc}"]
    cause = getattr(exc, "__cause__", None)
    while cause is not None:
        parts.append(f"caused by {type(cause).__name__}: {cause}")
        cause = getattr(cause, "__cause__", None)
    return "; ".join(parts)

def upload_file_for_response(upload):
    purpose = "vision" if upload["kind"] == "image" else "user_data"
    last_error = None
    for attempt in range(1, MAX_UPLOAD_RETRIES + 1):
        upload_client = new_upload_client()
        try:
            with open(upload["path"], "rb") as f:
                uploaded = upload_client.files.create(file=f, purpose=purpose)
            return {
                "path": upload["path"],
                "kind": upload["kind"],
                "file_id": uploaded.id,
            }
        except Exception as exc:
            last_error = describe_exception(exc)
            if attempt >= MAX_UPLOAD_RETRIES:
                break
            delay = min(2 ** (attempt - 1), 8)
            sys.stderr.write(
                f"[Upload failed for {upload['path']} on attempt {attempt}/{MAX_UPLOAD_RETRIES}: "
                f"{last_error}. Retrying in {delay}s.]\n"
            )
            sys.stderr.flush()
            time.sleep(delay)
        finally:
            try:
                upload_client.close()
            except Exception:
                pass
    raise ValueError(
        f"Failed to upload {upload['path']} after {MAX_UPLOAD_RETRIES} attempts: {last_error}"
    )

def run_pdf_compression(path, output_directory, quality):
    output_path = os.path.join(
        output_directory,
        os.path.splitext(os.path.basename(path))[0] + f".{quality.lstrip('/')}.pdf",
    )
    result = subprocess.run(
        [
            GHOSTSCRIPT_COMMAND,
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.7",
            f"-dPDFSETTINGS={quality}",
            f"-sOutputFile={output_path}",
            path,
        ],
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0 or not os.path.exists(output_path):
        detail = (result.stderr or result.stdout or "").strip()
        raise ValueError(detail or f"Ghostscript failed with exit code {result.returncode}")
    return output_path

def build_upload_failure_attachment(failures):
    lines = [
        "The following requested files failed to prepare or upload and were omitted.",
        "The answer may be incomplete if these files were relevant.",
        "",
    ]
    for failure in failures:
        lines.append(f"- {failure['path']} ({failure['kind']}): {failure['error']}")
    return {
        "path": "cligpt upload failures",
        "kind": "text",
        "text": "\n".join(lines),
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

def compress_pdf_for_upload(path, output_directory):
    if os.path.getsize(path) <= MAX_DIRECT_PDF_UPLOAD_BYTES:
        return path
    if not GHOSTSCRIPT_COMMAND:
        sys.stderr.write(
            f"[Large PDF is {os.path.getsize(path)} bytes but Ghostscript is unavailable; "
            f"uploading original: {path}]\n"
        )
        sys.stderr.flush()
        return path

    os.makedirs(output_directory, exist_ok=True)
    sys.stderr.write(
        f"[Compressing large PDF before upload: {path} "
        f"({os.path.getsize(path) // (1024 * 1024)} MB)]\n"
    )
    sys.stderr.flush()
    try:
        output_path = run_pdf_compression(path, output_directory, "/ebook")
    except ValueError as exc:
        sys.stderr.write(
            f"[PDF compression failed; uploading original: {path}. {exc}]\n"
        )
        sys.stderr.flush()
        return path

    original_size = os.path.getsize(path)
    compressed_size = os.path.getsize(output_path)
    if compressed_size > MAX_COMPRESSED_PDF_UPLOAD_BYTES:
        sys.stderr.write(
            f"[PDF remains {compressed_size // (1024 * 1024)} MB after standard compression; "
            f"trying stronger compression: {path}]\n"
        )
        sys.stderr.flush()
        try:
            stronger_path = run_pdf_compression(path, output_directory, "/screen")
            stronger_size = os.path.getsize(stronger_path)
            if stronger_size < compressed_size:
                output_path = stronger_path
                compressed_size = stronger_size
        except ValueError as exc:
            sys.stderr.write(
                f"[Stronger PDF compression failed; using standard compression: {path}. {exc}]\n"
            )
            sys.stderr.flush()

    if compressed_size >= original_size:
        sys.stderr.write(
            f"[PDF compression did not reduce size; uploading original: {path}]\n"
        )
        sys.stderr.flush()
        return path

    sys.stderr.write(
        f"[Compressed PDF for upload: {original_size // (1024 * 1024)} MB -> "
        f"{max(1, compressed_size // (1024 * 1024))} MB]\n"
    )
    sys.stderr.flush()
    return output_path

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

def write_text_upload_file(text, source_path, output_directory, suffix=".txt"):
    safe_name = os.path.basename(source_path) or "attachment"
    output_path = os.path.join(output_directory, safe_name + suffix)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return output_path

def meaningful_text_chars(text):
    return sum(1 for char in text if char.isalnum())

def get_pdf_page_count(path):
    if not PDFINFO_COMMAND:
        return None
    try:
        result = subprocess.run(
            [PDFINFO_COMMAND, path],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None

def extract_pdf_text_layer(path):
    if not PDFTOTEXT_COMMAND:
        return ""
    try:
        result = subprocess.run(
            [PDFTOTEXT_COMMAND, "-layout", path, "-"],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except Exception:
        return ""
    return result.stdout or ""

def extract_pdf_text_with_ocrmypdf(path, output_directory):
    if not OCRMYPDF_COMMAND:
        return ""
    sidecar_path = os.path.join(output_directory, "ocr-sidecar.txt")
    output_pdf = os.path.join(output_directory, "ocr-output.pdf")
    try:
        result = subprocess.run(
            [
                OCRMYPDF_COMMAND,
                "--sidecar",
                sidecar_path,
                "--skip-text",
                path,
                output_pdf,
            ],
            text=True,
            capture_output=True,
            timeout=900,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0 or not os.path.exists(sidecar_path):
        return ""
    with open(sidecar_path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()

def extract_pdf_text_with_tesseract(path, output_directory):
    if not PDFTOPPM_COMMAND or not TESSERACT_COMMAND:
        return ""
    page_count = get_pdf_page_count(path) or PDF_OCR_MAX_PAGES
    page_limit = min(page_count, PDF_OCR_MAX_PAGES)
    image_prefix = os.path.join(output_directory, "page")
    try:
        subprocess.run(
            [
                PDFTOPPM_COMMAND,
                "-f",
                "1",
                "-l",
                str(page_limit),
                "-r",
                "180",
                "-png",
                path,
                image_prefix,
            ],
            text=True,
            capture_output=True,
            timeout=max(120, page_limit * 30),
            check=False,
        )
    except Exception:
        return ""
    texts = []
    images = sorted(
        os.path.join(output_directory, filename)
        for filename in os.listdir(output_directory)
        if filename.startswith("page-") and filename.endswith(".png")
    )
    for image in images:
        try:
            result = subprocess.run(
                [TESSERACT_COMMAND, image, "stdout", "-l", "eng", "--psm", "6"],
                text=True,
                capture_output=True,
                timeout=90,
                check=False,
            )
        except Exception:
            continue
        if result.stdout:
            texts.append(result.stdout)
    return "\n\n".join(texts)

def extract_image_text_with_tesseract(path):
    if not TESSERACT_COMMAND:
        return ""
    try:
        result = subprocess.run(
            [TESSERACT_COMMAND, path, "stdout", "-l", "eng", "--psm", "6"],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except Exception:
        return ""
    return result.stdout or ""

def image_metadata_report(path):
    stat = os.stat(path)
    mime_type, encoding = mimetypes.guess_type(path)
    file_output = run_optional_command([FILE_COMMAND, "--brief", path]) if FILE_COMMAND else "unavailable"
    return "\n".join([
        f"Image metadata report for: {path}",
        f"Size: {stat.st_size} bytes",
        f"MIME guess: {mime_type or 'unknown'}",
        f"Encoding guess: {encoding or 'unknown'}",
        f"file(1): {file_output}",
        f"SHA256: {sha256_path(path)}",
        "",
    ])

def describe_image_for_search(path):
    vision_client = new_upload_client()
    uploaded = None
    try:
        uploaded = upload_file_for_response({"path": path, "kind": "image"})
        response = vision_client.responses.create(
            model=FAST_MODEL,
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Describe this image for later semantic file search. "
                            "Mention visible text, document-like content, people, clothing, "
                            "objects, colors, scene type, and any distinctive features. "
                            "If this is a scanned document or lease photo, say that clearly."
                        ),
                    },
                    {"type": "input_image", "file_id": uploaded["file_id"]},
                ],
            }],
            max_output_tokens=700,
            store=True,
        )
        return extract_response_text(response).strip()
    except Exception as exc:
        return f"Vision caption unavailable: {exc}"
    finally:
        if uploaded and uploaded.get("file_id"):
            try:
                vision_client.files.delete(uploaded["file_id"])
            except Exception:
                pass

def prepare_image_text_for_search(upload, output_directory, metadata_header):
    path = upload["path"]
    ocr_text = extract_image_text_with_tesseract(path)
    if meaningful_text_chars(ocr_text) >= IMAGE_DOCUMENT_TEXT_MIN_CHARS:
        text = "\n".join([
            metadata_header,
            f"OCR text for document-like image: {path}",
            "Extraction method: tesseract image OCR",
            "",
            ocr_text,
        ])
        return write_text_upload_file(text, path, output_directory, ".image-ocr.txt")

    caption = describe_image_for_search(path)
    text = "\n".join([
        metadata_header,
        f"Searchable image description for: {path}",
        "Image handling: non-document image indexed by reusable vision caption and metadata.",
        "Note: broad image search uses this caption first. Direct OpenAI vision uploads are capped separately.",
        "",
        image_metadata_report(path),
        "Vision caption:",
        caption or "No caption generated.",
        "",
        "OCR preview:",
        ocr_text or "No meaningful OCR text detected.",
    ])
    return write_text_upload_file(text, path, output_directory, ".image-description.txt")

def prepare_pdf_text_for_search(path, output_directory, metadata_header=""):
    text = extract_pdf_text_layer(path)
    source = "pdftotext text layer"
    if meaningful_text_chars(text) < PDF_TEXT_MIN_CHARS:
        ocr_text = extract_pdf_text_with_ocrmypdf(path, output_directory)
        if meaningful_text_chars(ocr_text) > meaningful_text_chars(text):
            text = ocr_text
            source = "ocrmypdf sidecar"
    if meaningful_text_chars(text) < PDF_TEXT_MIN_CHARS:
        fallback_text = extract_pdf_text_with_tesseract(path, output_directory)
        if meaningful_text_chars(fallback_text) > meaningful_text_chars(text):
            text = fallback_text
            source = "pdftoppm+tesseract"
    if meaningful_text_chars(text) < 50:
        return None
    page_count = get_pdf_page_count(path)
    header = [
        f"Extracted text for searchable PDF: {path}",
        f"Extraction method: {source}",
        f"Pages: {page_count if page_count is not None else 'unknown'}",
        "",
    ]
    if page_count and page_count > PDF_OCR_MAX_PAGES and source == "pdftoppm+tesseract":
        header.append(
            f"Note: OCR fallback was limited to the first {PDF_OCR_MAX_PAGES} pages.\n"
        )
    return write_text_upload_file(metadata_header + "\n".join(header) + text, path, output_directory, ".pdf-text.txt")

def prepare_upload_path_for_search(upload, temp_directory):
    metadata = upload_metadata_header(upload)
    if upload["kind"] == "image":
        image_directory = tempfile.mkdtemp(dir=temp_directory)
        return prepare_image_text_for_search(upload, image_directory, metadata)
    if upload["kind"] == "text":
        text_attachment = read_text_attachment(upload["path"])
        return write_text_upload_file(metadata + text_attachment["text"], upload["path"], temp_directory)
    if upload["kind"] == "blob":
        blob_attachment = read_blob_attachment(upload["path"])
        return write_text_upload_file(metadata + blob_attachment["text"], upload["path"], temp_directory, ".blob-report.txt")
    if upload["kind"] == "office":
        conversion_directory = tempfile.mkdtemp(dir=temp_directory)
        pdf_path = convert_office_to_pdf(upload["path"], conversion_directory)
        text_path = prepare_pdf_text_for_search(pdf_path, conversion_directory, metadata)
        if text_path:
            return text_path
        return compress_pdf_for_upload(pdf_path, conversion_directory)
    if upload["kind"] == "file":
        pdf_directory = tempfile.mkdtemp(dir=temp_directory)
        text_path = prepare_pdf_text_for_search(upload["path"], pdf_directory, metadata)
        if text_path:
            return text_path
        return compress_pdf_for_upload(upload["path"], pdf_directory)
    return upload["path"]

def prepare_image_text_for_local_search(upload, output_directory, metadata_header=""):
    path = upload["path"]
    ocr_text = extract_image_text_with_tesseract(path)
    if meaningful_text_chars(ocr_text) >= IMAGE_DOCUMENT_TEXT_MIN_CHARS:
        text = "\n".join([
            metadata_header,
            f"Extracted text for searchable document image: {path}",
            "Image handling: document-like image OCR indexed locally.",
            "",
            ocr_text,
        ])
        return write_text_upload_file(text, path, output_directory, ".image-ocr.txt")
    text = "\n".join([
        metadata_header,
        f"Local metadata for non-document image: {path}",
        "Image handling: non-document image was not sent to OpenAI for local preflight search.",
        "Note: broad visual search needs --remote-search or direct --image uploads; local search can only use filename, metadata, and OCR text.",
        "",
        image_metadata_report(path),
        "",
        "OCR preview:",
        ocr_text or "No meaningful OCR text detected.",
    ])
    return write_text_upload_file(text, path, output_directory, ".image-local.txt")

def prepare_upload_path_for_local_search(upload, temp_directory):
    metadata = upload_metadata_header(upload)
    if upload["kind"] == "image":
        image_directory = tempfile.mkdtemp(dir=temp_directory)
        return prepare_image_text_for_local_search(upload, image_directory, metadata)
    if upload["kind"] == "text":
        text_attachment = read_text_attachment(upload["path"])
        return write_text_upload_file(metadata + text_attachment["text"], upload["path"], temp_directory)
    if upload["kind"] == "blob":
        blob_attachment = read_blob_attachment(upload["path"])
        return write_text_upload_file(metadata + blob_attachment["text"], upload["path"], temp_directory, ".blob-report.txt")
    if upload["kind"] == "office":
        conversion_directory = tempfile.mkdtemp(dir=temp_directory)
        pdf_path = convert_office_to_pdf(upload["path"], conversion_directory)
        text_path = prepare_pdf_text_for_search(pdf_path, conversion_directory, metadata)
        if text_path:
            return text_path
        return write_text_upload_file(
            metadata + f"No OCR text could be extracted from converted office file: {upload['path']}",
            upload["path"],
            temp_directory,
            ".office-local.txt",
        )
    if upload["kind"] == "file":
        pdf_directory = tempfile.mkdtemp(dir=temp_directory)
        text_path = prepare_pdf_text_for_search(upload["path"], pdf_directory, metadata)
        if text_path:
            return text_path
        return write_text_upload_file(
            metadata + f"No OCR text could be extracted from PDF: {upload['path']}",
            upload["path"],
            temp_directory,
            ".pdf-local.txt",
        )
    return upload["path"]

def split_uploads_for_directory_search(directory_paths):
    searchable_uploads = []
    direct_uploads = []
    directory_uploads = collect_uploads(directory_paths=directory_paths)
    for upload in directory_uploads:
        searchable_uploads.append(upload)
    return searchable_uploads, direct_uploads

def sync_and_search_local_directories(directory_paths, user_prompt):
    root_paths = [os.path.abspath(os.path.expanduser(path)) for path in normalize_paths(directory_paths)]
    uploads = collect_uploads(directory_paths=root_paths)
    for upload in uploads:
        role = classify_directory_path(upload["path"], upload.get("root_path"))
        upload["classification"] = role["classification"]
    sys.stderr.write(
        f"[Local directory search: indexing/reusing {len(uploads)} file(s) before model request]\n"
    )
    sys.stderr.flush()
    index = LocalSearchIndex()
    try:
        stats = index.sync_uploads(
            uploads,
            prepare_upload_path_for_local_search,
            progress=lambda message: (sys.stderr.write(message), sys.stderr.flush()),
        )
        results = index.search(root_paths, user_prompt)
    finally:
        index.close()
    stats["selected"] = len(results)
    context = {
        "mode": "local",
        "root_paths": root_paths,
        "stats": stats,
        "results": results,
        "text": build_local_context(root_paths, user_prompt, stats, results),
    }
    sys.stderr.write(
        f"[Local directory search ready: reused:{stats['reused']} indexed:{stats['indexed']} "
        f"failed:{stats['failed']} selected:{len(results)}]\n"
    )
    sys.stderr.flush()
    return context

def directory_sync_status(directory_paths, create=False, adopt_remote=False):
    statuses = []
    for directory in normalize_paths(directory_paths):
        root = os.path.abspath(os.path.expanduser(directory))
        searchable_uploads, _ = split_uploads_for_directory_search([root])
        manager = VectorStoreManager(client)
        statuses.append(
            manager.status_for_uploads(
                root,
                searchable_uploads,
                create=create,
                adopt_remote=adopt_remote,
            )
        )
    return statuses

def directory_sync_log_path(root):
    safe_hash = root_hash(root)
    return os.path.join(state_root(), f"sync-{safe_hash}.log")

def start_background_directory_sync(directory_paths, index_concurrency=DEFAULT_INDEX_CONCURRENCY):
    processes = []
    script = os.path.abspath(sys.argv[0] or "cligpt.py")
    for directory in normalize_paths(directory_paths):
        root = os.path.abspath(os.path.expanduser(directory))
        log_path = directory_sync_log_path(root)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        command = [
            sys.executable,
            script,
            "sync-directory",
            root,
            "--index-concurrency",
            str(index_concurrency),
        ]
        with open(log_path, "ab") as log_handle:
            log_handle.write(
                f"\n--- background sync started {datetime.datetime.now().isoformat()} "
                f"for {root} ---\n".encode("utf-8")
            )
            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=log_handle,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        processes.append({"root_path": root, "pid": process.pid, "log_path": log_path})
    return processes

def print_sync_warning(statuses, index_concurrency):
    sys.stderr.write(
        "[Directory search index is incomplete. A sync-directory task is required "
        "to fully answer against the requested directory.]\n"
    )
    sys.stderr.write(
        "[Directory sync is non-blocking when you proceed with a query; the model "
        "will answer using only files already available in the search index.]\n"
    )
    sys.stderr.write(
        "[This feature is experimental. Syncing large directories can take minutes, "
        "hours, or days and may use significant API tokens/storage.]\n"
    )
    for status in statuses:
        counts = status["counts"]
        sys.stderr.write(
            f"[{status['name']}: completed:{counts['completed']} "
            f"new:{counts['new']} changed:{counts['changed']} retry:{counts['retry']} "
            f"deleted:{counts['deleted']} failed:{counts['failed']}]\n"
        )
    sys.stderr.write(
        "[Choose: Enter/a = proceed now and continue sync in background; "
        "s = sync first, then query; q = cancel]\n"
    )
    sys.stderr.flush()

def choose_directory_query_mode(statuses, index_concurrency, allow_partial=False, wait_index=False):
    incomplete = [status for status in statuses if not status["complete"]]
    if wait_index:
        return "sync_first"
    if not incomplete:
        return "ready"
    if allow_partial:
        return "partial"
    print_sync_warning(incomplete, index_concurrency)
    if not sys.stdin.isatty():
        sys.stderr.write("[Non-interactive shell detected; proceeding with partial index and background sync.]\n")
        sys.stderr.flush()
        return "partial"
    choice = input("Directory index incomplete. Proceed with partial index? [a]/s/q: ").strip().lower()
    if choice in {"q", "quit", "cancel", "n", "no"}:
        return "cancel"
    if choice in {"s", "sync", "sync-first"}:
        return "sync_first"
    return "partial"

def context_for_existing_directory_vector_stores(directory_paths):
    vector_contexts = []
    for directory in normalize_paths(directory_paths):
        root = os.path.abspath(os.path.expanduser(directory))
        searchable_uploads, _ = split_uploads_for_directory_search([root])
        manager = VectorStoreManager(client)
        context = manager.context_for_directory(root, searchable_uploads)
        context["manifest"] = directory_manifest(root, searchable_uploads)
        vector_contexts.append(context)
    return vector_contexts

def sync_directory_vector_stores(directory_paths, index_concurrency=DEFAULT_INDEX_CONCURRENCY):
    vector_contexts = []
    for directory in normalize_paths(directory_paths):
        root = os.path.abspath(os.path.expanduser(directory))
        searchable_uploads, _ = split_uploads_for_directory_search([root])
        print_upload_plan(searchable_uploads)
        manager = VectorStoreManager(client)
        context = manager.sync_directory(
            root,
            searchable_uploads,
            prepare_upload_path_for_search,
            index_concurrency=index_concurrency,
        )
        context["manifest"] = directory_manifest(root, searchable_uploads)
        vector_contexts.append(context)
        stats = context["stats"]
        sys.stderr.write(
            f"[Search index ready: {context['name']} "
            f"reused:{stats['reused']} uploaded:{stats['uploaded']} "
            f"pruned:{stats['pruned']} failed:{stats['failed']}]\n"
        )
        sys.stderr.flush()
    return vector_contexts

def print_directory_status(directory_paths):
    statuses = directory_sync_status(directory_paths, adopt_remote=True)
    for status in statuses:
        counts = status["counts"]
        sync_time = (
            datetime.datetime.fromtimestamp(status["last_sync_at"]).isoformat(sep=" ", timespec="seconds")
            if status.get("last_sync_at") else "never"
        )
        print(f"{status['name']}")
        print(f"  Root: {status['root_path']}")
        print(f"  Vector store: {status['vector_store_id']}")
        print(f"  Complete: {'yes' if status['complete'] else 'no'}")
        print(f"  Remote adopted: {'yes' if status.get('remote_adopted') else 'no'}")
        print(f"  Last sync: {sync_time}")
        print(
            "  Files: "
            f"completed={counts['completed']} new={counts['new']} changed={counts['changed']} "
            f"retry={counts['retry']} deleted={counts['deleted']} failed={counts['failed']} "
            f"total={counts['total']}"
        )
        for key in ("new", "changed", "retry", "deleted", "failed"):
            examples = status["examples"].get(key) or []
            if examples:
                print(f"  Example {key}:")
                for path in examples:
                    print(f"    {path}")

def upload_attachments(file_paths=None, image_paths=None, directory_paths=None, blob_paths=None):
    uploads = collect_uploads(file_paths, image_paths, None, blob_paths)
    for _, direct_uploads in [split_uploads_for_directory_search(directory_paths)] if directory_paths else []:
        uploads.extend(direct_uploads)
    if uploads:
        capped_uploads = []
        skipped_vision = []
        vision_count = 0
        for upload in uploads:
            if upload["kind"] == "image":
                if vision_count >= MAX_DIRECT_VISION_UPLOADS:
                    skipped_vision.append({
                        "path": upload["path"],
                        "kind": upload["kind"],
                        "error": (
                            f"Direct OpenAI vision upload cap is {MAX_DIRECT_VISION_UPLOADS}; "
                            "directory images should be synced into file_search captions/OCR instead."
                        ),
                    })
                    continue
                vision_count += 1
            capped_uploads.append(upload)
        if skipped_vision:
            sys.stderr.write(
                f"[Skipped {len(skipped_vision)} direct image upload(s): "
                f"vision upload cap is {MAX_DIRECT_VISION_UPLOADS}.]\n"
            )
            sys.stderr.flush()
        uploads = capped_uploads
    else:
        skipped_vision = []
    print_upload_plan(uploads)
    attachments = []
    failures = list(skipped_vision)
    total = len(uploads)
    completed = 0
    succeeded = 0
    failed = 0
    best_effort = bool(normalize_paths(directory_paths)) or total > 1
    write_upload_progress(completed, total, succeeded, failed)
    with tempfile.TemporaryDirectory(prefix="cligpt-upload-") as temp_directory:
        for upload in uploads:
            try:
                upload_status(upload, "Preparing upload")
                if upload["kind"] == "text":
                    attachments.append(read_text_attachment(upload["path"]))
                elif upload["kind"] == "blob":
                    attachments.append(read_blob_attachment(upload["path"]))
                elif upload["kind"] == "office":
                    conversion_directory = tempfile.mkdtemp(dir=temp_directory)
                    pdf_path = convert_office_to_pdf(upload["path"], conversion_directory)
                    pdf_path = compress_pdf_for_upload(pdf_path, conversion_directory)
                    upload_status(upload, "Uploading converted PDF")
                    uploaded = upload_file_for_response({"path": pdf_path, "kind": "file"})
                    uploaded["source_path"] = upload["path"]
                    uploaded["converted_path"] = pdf_path
                    attachments.append(uploaded)
                elif upload["kind"] == "file":
                    pdf_directory = tempfile.mkdtemp(dir=temp_directory)
                    pdf_path = compress_pdf_for_upload(upload["path"], pdf_directory)
                    upload_status(upload, "Uploading file")
                    uploaded = upload_file_for_response({"path": pdf_path, "kind": "file"})
                    uploaded["source_path"] = upload["path"]
                    if pdf_path != upload["path"]:
                        uploaded["compressed_path"] = pdf_path
                    attachments.append(uploaded)
                else:
                    upload_status(upload, "Uploading image")
                    attachments.append(upload_file_for_response(upload))
                succeeded += 1
                upload_status(upload, "Upload complete")
            except Exception as exc:
                failed += 1
                failures.append({
                    "path": upload["path"],
                    "kind": upload["kind"],
                    "error": str(exc),
                })
                if best_effort:
                    upload_status(upload, f"Upload skipped after failure: {exc}")
                else:
                    upload_status(upload, f"Upload failed: {exc}")
                if not best_effort:
                    raise
            finally:
                completed += 1
                write_upload_progress(completed, total, succeeded, failed, end=(completed == total))
    if failures:
        attachments.append(build_upload_failure_attachment(failures))
    if uploads and not any(attachment["kind"] != "text" or attachment["path"] != "cligpt upload failures" for attachment in attachments):
        raise ValueError(
            f"No requested attachments could be prepared or uploaded; {len(failures)} file(s) failed."
        )
    return attachments

def build_user_content(user_prompt, uploaded_attachments, vector_contexts=None, local_context=None):
    content = []
    if local_context:
        content.append({"type": "input_text", "text": local_context["text"]})
    if vector_contexts:
        lines = [
            "Searchable directory corpora are available through the file_search tool.",
            "Use file_search to inspect relevant files instead of assuming all files are already in context.",
        ]
        for context in vector_contexts:
            stats = context["stats"]
            lines.append(
                f"- {context['name']} ({context['root_path']}): "
                f"{stats['reused']} reused, {stats['uploaded']} uploaded, "
                f"{stats['pruned']} pruned, {stats['failed']} failed"
                f"{' (partial index)' if stats.get('partial') else ''}"
                f"{' (adopted remote index; local per-file state unavailable)' if context.get('remote_adopted') else ''}"
            )
            manifest = context.get("manifest")
            if manifest:
                lines.append("")
                lines.append(manifest)
                lines.append("")
            status = context.get("status")
            if status and not status.get("complete"):
                counts = status["counts"]
                lines.append(
                    "  Index status at query time: "
                    f"completed {counts['completed']}/{counts['total']}; "
                    f"new {counts['new']}; changed {counts['changed']}; "
                    f"retry {counts['retry']}; deleted {counts['deleted']}."
                )
            for failure in context.get("failures", [])[:25]:
                lines.append(f"  omitted: {failure['path']} ({failure['error']})")
        content.append({"type": "input_text", "text": "\n".join(lines)})
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
    content.append({
        "type": "input_text",
        "text": f"CURRENT USER QUESTION:\n{user_prompt}",
    })
    return content

def format_duration(seconds):
    seconds = max(0, int(seconds))
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minute:02d}m{second:02d}s"
    if minutes:
        return f"{minutes}m{second:02d}s"
    return f"{second}s"

def format_stream_event_debug_line(count):
    return f"[OpenAI Stream Events: {max(0, int(count))}]"

def iter_stream_with_heartbeat(
    stream,
    *,
    debug=False,
    heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
    idle_timeout_seconds=DEFAULT_IDLE_TIMEOUT_SECONDS,
):
    events = queue.Queue()

    def consume_stream():
        try:
            for event in stream:
                events.put(("event", event))
        except BaseException as exc:
            events.put(("error", exc))
        finally:
            events.put(("done", None))

    thread = threading.Thread(target=consume_stream, name="cligpt-openai-stream", daemon=True)
    thread.start()
    started_at = time.monotonic()
    last_event_at = started_at
    last_heartbeat_at = started_at
    last_idle_warning_at = started_at
    first_text_seen = False
    first_text_delay = None
    if debug:
        sys.stderr.write("[OpenAI stream opened; waiting for events]\n")
        sys.stderr.flush()

    while True:
        now = time.monotonic()
        timeout = max(1, min(heartbeat_seconds or 5, 5))
        try:
            kind, payload = events.get(timeout=timeout)
        except queue.Empty:
            now = time.monotonic()
            if heartbeat_seconds and now - last_heartbeat_at >= heartbeat_seconds:
                sys.stderr.write(
                    f"[Waiting for model stream... elapsed:{format_duration(now - started_at)} "
                    f"idle:{format_duration(now - last_event_at)}]\n"
                )
                sys.stderr.flush()
                last_heartbeat_at = now
            if (
                idle_timeout_seconds
                and now - last_event_at >= idle_timeout_seconds
                and now - last_idle_warning_at >= idle_timeout_seconds
            ):
                sys.stderr.write(
                    f"[No response events for {format_duration(now - last_event_at)}. "
                    "The request may still be running remotely. Press Ctrl-C to abort locally.]\n"
                )
                sys.stderr.flush()
                last_idle_warning_at = now
            continue

        if kind == "event":
            event = payload
            last_event_at = time.monotonic()
            event_type = getattr(event, "type", "")
            if not first_text_seen and event_type in {"response.output_text.delta", "response.refusal.delta"}:
                first_text_seen = True
                first_text_delay = format_duration(last_event_at - started_at)
            yield event
        elif kind == "error":
            raise payload
        elif kind == "done":
            if debug and first_text_delay:
                sys.stderr.write(f"[First visible output after {first_text_delay}]\n")
                sys.stderr.flush()
            break

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

def estimate_tokens_local(text):
    """Rough local token estimate for debug output, not billing."""
    if not text:
        return 0
    text = str(text)
    word_estimate = len(text.split())
    char_estimate = (len(text) + 3) // 4
    return max(word_estimate, char_estimate)

def estimate_json_tokens(value):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        text = str(value)
    return estimate_tokens_local(text)

def input_content_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "input_text":
            chunks.append(str(item.get("text", "")))
    return "\n".join(chunks)

def count_non_text_input_items(content):
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for item in content
        if isinstance(item, dict) and item.get("type") != "input_text"
    )

def request_input_debug_breakdown(
    request_args,
    *,
    system_message,
    runtime_instructions,
    pruned_context,
    user_prompt,
    local_context=None,
):
    instructions = request_args.get("instructions", "")
    input_items = request_args.get("input", []) or []
    user_content_text = ""
    non_text_items = 0
    if isinstance(input_items, str):
        user_content_text = input_items
    else:
        for item in input_items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            user_content_text += input_content_text(content)
            non_text_items += count_non_text_input_items(content)

    tools = request_args.get("tools", []) or []
    include = request_args.get("include", []) or []
    reasoning = request_args.get("reasoning") or {}
    text_format = request_args.get("text") or {}
    local_text = (local_context or {}).get("text", "")
    known_instruction_parts = (
        estimate_tokens_local(system_message)
        + estimate_tokens_local(runtime_instructions)
        + estimate_tokens_local(pruned_context)
    )
    instruction_total = estimate_tokens_local(instructions)
    hidden_instruction_overhead = max(instruction_total - known_instruction_parts, 0)

    return {
        "estimate_method": "max(words, chars/4); approximate, not API tokenizer",
        "estimated_request_input_tokens": (
            instruction_total
            + estimate_tokens_local(user_content_text)
            + estimate_json_tokens(tools)
            + estimate_json_tokens(include)
            + estimate_json_tokens(reasoning)
            + estimate_json_tokens(text_format)
        ),
        "instructions": instruction_total,
        "system_message": estimate_tokens_local(system_message),
        "runtime_instructions": estimate_tokens_local(runtime_instructions),
        "pruned_context": estimate_tokens_local(pruned_context),
        "prompt_cache_anchor_and_headings": hidden_instruction_overhead,
        "user_content_text": estimate_tokens_local(user_content_text),
        "current_user_prompt": estimate_tokens_local(user_prompt),
        "local_search_text": estimate_tokens_local(local_text),
        "tools_schema": estimate_json_tokens(tools),
        "include_selectors": estimate_json_tokens(include),
        "reasoning_settings": estimate_json_tokens(reasoning),
        "text_format": estimate_json_tokens(text_format),
        "non_text_input_items": non_text_items,
    }

def format_request_input_debug_breakdown(breakdown):
    return (
        f"[Request Input Estimate: {breakdown['estimated_request_input_tokens']:,} approx tokens]\n"
        f"  [Instructions Total: {breakdown['instructions']:,}]\n"
        f"    [System Message: {breakdown['system_message']:,}]\n"
        f"    [Runtime Instructions: {breakdown['runtime_instructions']:,}]\n"
        f"    [Pruned Context: {breakdown['pruned_context']:,}]\n"
        f"    [Prompt Cache Anchor/Headings: {breakdown['prompt_cache_anchor_and_headings']:,}]\n"
        f"  [User Content Text: {breakdown['user_content_text']:,}]\n"
        f"    [Current User Prompt: {breakdown['current_user_prompt']:,}]\n"
        f"    [Local Search Text: {breakdown['local_search_text']:,}]\n"
        f"  [Tool Schemas: {breakdown['tools_schema']:,}]\n"
        f"  [Include Selectors: {breakdown['include_selectors']:,}]\n"
        f"  [Reasoning Settings: {breakdown['reasoning_settings']:,}]\n"
        f"  [Text Format: {breakdown['text_format']:,}]\n"
        f"  [Non-text Input Items: {breakdown['non_text_input_items']}]\n"
        f"  [Estimate Method: {breakdown['estimate_method']}]\n"
    )

def read_linux_meminfo():
    data = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                parts = value.strip().split()
                if not parts:
                    continue
                try:
                    data[key] = int(parts[0]) * 1024
                except ValueError:
                    continue
    except OSError:
        return {}
    return data

def read_linux_cpu_model():
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name") and ":" in line:
                    return line.split(":", 1)[1].strip()
    except OSError:
        return ""
    return ""

def linux_distribution_info():
    try:
        import distro
        return {
            "name": distro.name() or "",
            "version": distro.version() or "",
            "id": distro.id() or "",
        }
    except Exception:
        pass

    data = {}
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as handle:
            for line in handle:
                if "=" not in line:
                    continue
                key, value = line.rstrip("\n").split("=", 1)
                data[key.lower()] = value.strip().strip('"')
    except OSError:
        return {}
    return {
        "name": data.get("pretty_name") or data.get("name") or "",
        "version": data.get("version_id") or "",
        "id": data.get("id") or "",
    }

def command_output(command, timeout=2, limit=3000):
    if not command or not shutil.which(command[0]):
        return ""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    text = (result.stdout or "").strip()
    if len(text) > limit:
        return text[:limit] + "\n...[truncated]"
    return text

def bytes_to_gib(value):
    if not value:
        return None
    return round(float(value) / (1024 ** 3), 2)

def disk_usage_for(path):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {
        "path": path,
        "total_gib": bytes_to_gib(usage.total),
        "used_gib": bytes_to_gib(usage.used),
        "free_gib": bytes_to_gib(usage.free),
    }

def first_available_command(names, default="unknown"):
    for name in names:
        if shutil.which(name):
            return name
    return default

def display_profile():
    monitors = command_output(["xrandr", "--listmonitors"], timeout=2, limit=1500)
    if monitors:
        return {"source": "xrandr --listmonitors", "monitors": monitors.splitlines()}
    return {
        "source": "environment",
        "display": os.getenv("DISPLAY") or "",
        "wayland_display": os.getenv("WAYLAND_DISPLAY") or "",
    }

def graphics_profile():
    output = command_output(["lspci"], timeout=2, limit=4000)
    if not output:
        return []
    wanted = ("vga compatible controller", "3d controller", "display controller")
    return [
        line
        for line in output.splitlines()
        if any(marker in line.lower() for marker in wanted)
    ][:10]

def executable_map(names):
    return {name: bool(shutil.which(name)) for name in names}

def get_system_profile():
    """Return a compact, read-only local system profile for the model."""
    meminfo = read_linux_meminfo()
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    cpu_model = read_linux_cpu_model() or platform.processor()
    return {
        "schema_version": 1,
        "source": "cligpt local get_system_profile tool",
        "privacy_note": "Generated locally and sent only because the model called this read-only tool.",
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "distribution": linux_distribution_info(),
        },
        "runtime": {
            "cwd": cwd,
            "home": home,
            "shell": os.path.basename(os.getenv("SHELL", "")) or "unknown",
            "editor": os.getenv("EDITOR", "unknown"),
            "python": platform.python_version(),
        },
        "hardware": {
            "cpu_model": cpu_model,
            "logical_cpu_count": os.cpu_count(),
            "memory_total_gib": bytes_to_gib(meminfo.get("MemTotal")),
            "memory_available_gib": bytes_to_gib(meminfo.get("MemAvailable")),
            "graphics": graphics_profile(),
        },
        "storage": {
            "cwd": disk_usage_for(cwd),
            "home": disk_usage_for(home),
            "root": disk_usage_for("/"),
        },
        "display": display_profile(),
        "tools_available": executable_map([
            "neofetch",
            "fastfetch",
            "git",
            "rg",
            "python",
            "python3",
            "tesseract",
            "ocrmypdf",
            "pdftotext",
            "pdfinfo",
            "pdftoppm",
            "gs",
            "libreoffice",
            "soffice",
            "docker",
            "podman",
        ]),
    }

def local_tool_schemas():
    return [
        {
            "type": "function",
            "name": LOCAL_TOOL_GET_SYSTEM_PROFILE,
            "description": (
                "Return a compact read-only profile of the user's local machine, "
                "including OS, CPU, memory, storage, display, and selected tool availability. "
                "Use this only when the answer depends on local machine capabilities or installed tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]

def collect_local_tool_calls(response):
    calls = []
    if not response:
        return calls
    for item in response_output_items(response):
        if get_nested_attr(item, "type") != "function_call":
            continue
        name = get_nested_attr(item, "name")
        if name != LOCAL_TOOL_GET_SYSTEM_PROFILE:
            continue
        arguments_text = get_nested_attr(item, "arguments", default="{}") or "{}"
        try:
            arguments = json.loads(arguments_text)
        except json.JSONDecodeError:
            arguments = {}
        calls.append({
            "name": name,
            "call_id": get_nested_attr(item, "call_id"),
            "arguments": arguments,
        })
    return calls

def execute_local_tool_call(call):
    name = call.get("name")
    if name == LOCAL_TOOL_GET_SYSTEM_PROFILE:
        payload = {"ok": True, "tool": name, "result": get_system_profile()}
    else:
        payload = {"ok": False, "tool": name or "unknown", "error": "Unknown local tool."}
    return {
        "type": "function_call_output",
        "call_id": call.get("call_id"),
        "output": json.dumps(payload, ensure_ascii=True, sort_keys=True),
    }

def aggregate_completed_responses(responses, fallback_reasoning_tokens=0):
    responses = [response for response in responses if response]
    if not responses:
        return None
    if len(responses) == 1:
        return responses[0]

    input_tokens = 0
    cached_tokens = 0
    output_tokens = 0
    total_tokens = 0
    reasoning_tokens = 0
    output_items = []
    for response in responses:
        usage = getattr(response, "usage", None)
        input_tokens += get_nested_attr(usage, "input_tokens", default=0) or 0
        cached_tokens += get_nested_attr(usage, "input_tokens_details", "cached_tokens", default=0) or 0
        output_tokens += get_nested_attr(usage, "output_tokens", default=0) or 0
        total_tokens += get_nested_attr(usage, "total_tokens", default=0) or 0
        reasoning_tokens += get_nested_attr(usage, "output_tokens_details", "reasoning_tokens", default=0) or 0
        output_items.extend(response_output_items(response))
    if not reasoning_tokens:
        reasoning_tokens = fallback_reasoning_tokens
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
            output_tokens=output_tokens,
            output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
            total_tokens=total_tokens,
        ),
        output=output_items,
    )

def response_usage_dict(response, reasoning_tokens=0):
    usage = getattr(response, "usage", None) if response else None
    return {
        "input_tokens": get_nested_attr(usage, "input_tokens", default=0) or 0,
        "cached_input_tokens": get_nested_attr(
            usage,
            "input_tokens_details",
            "cached_tokens",
            default=0,
        ) or 0,
        "output_tokens": get_nested_attr(usage, "output_tokens", default=0) or 0,
        "total_tokens": get_nested_attr(usage, "total_tokens", default=0) or 0,
        "reasoning_tokens": reasoning_tokens or get_nested_attr(
            usage,
            "output_tokens_details",
            "reasoning_tokens",
            default=0,
        ) or 0,
    }

def format_cache_hit_ratio(tokens):
    input_tokens = tokens.get("input_tokens", 0) or 0
    if input_tokens <= 0:
        return "0.0%"
    cached_tokens = tokens.get("cached_input_tokens", 0) or 0
    return f"{(cached_tokens / input_tokens) * 100:.1f}%"

def estimate_response_cost(tokens, model, file_search_calls=0, web_search_calls=0):
    capabilities = get_model_capabilities(model)
    input_rate = capabilities.input_price_per_million
    cached_input_rate = capabilities.cached_input_price_per_million
    output_rate = capabilities.output_price_per_million
    if input_rate is None or output_rate is None:
        return None

    input_tokens = tokens.get("input_tokens", 0) or 0
    cached_input_tokens = min(tokens.get("cached_input_tokens", 0) or 0, input_tokens)
    uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
    if cached_input_rate is None:
        cached_input_rate = input_rate

    input_cost = (uncached_input_tokens * input_rate) / 1_000_000
    cached_input_cost = (cached_input_tokens * cached_input_rate) / 1_000_000
    output_cost = ((tokens.get("output_tokens", 0) or 0) * output_rate) / 1_000_000
    file_search_cost = (file_search_calls * FILE_SEARCH_CALL_PRICE_PER_1000) / 1_000
    web_search_cost = (web_search_calls * WEB_SEARCH_CALL_PRICE_PER_1000) / 1_000
    total = input_cost + cached_input_cost + output_cost + file_search_cost + web_search_cost
    return {
        "total": total,
        "currency": "USD",
        "input": input_cost,
        "cached_input": cached_input_cost,
        "output": output_cost,
        "file_search": file_search_cost,
        "web_search": web_search_cost,
        "model": capabilities.model_id,
        "note": "reasoning tokens are included in billed output tokens when reported",
    }

def format_estimated_cost(cost):
    if cost is None:
        return "unknown"
    value = cost.get("total", 0) or 0
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:,.4f}"
    return f"${value:,.6f}"

def abbreviate_table_value(value, max_chars=14):
    text = str(value or "none")
    if len(text) <= max_chars:
        return text
    if max_chars <= 8:
        return text[:max_chars]
    head = (max_chars - 3) // 2
    tail = max_chars - 3 - head
    return f"{text[:head]}...{text[-tail:]}"

def normalize_prompt_cache_retention(retention, model):
    value = (retention or "").strip().lower()
    if value in {"", "off", "none", "disabled", "false", "0"}:
        return None
    if value in {"in_memory", "24h"}:
        return value
    if value == "auto":
        if model.startswith("gpt-5") or model.startswith("gpt-4.1"):
            return "24h"
        return None
    return value

def build_prompt_cache_key(
    model,
    system_message,
    web_search,
    vector_contexts=None,
    local_context=None,
    uploaded_attachments=None,
    explicit_key=None,
):
    explicit_key = (explicit_key or "").strip()
    if explicit_key:
        return explicit_key
    if vector_contexts:
        file_mode = "file_search"
    elif local_context:
        file_mode = "local_search"
    elif uploaded_attachments:
        file_mode = "direct"
    else:
        file_mode = "none"
    web_mode = "web" if web_search else "noweb"
    system_hash = hashlib.sha256(system_message.encode("utf-8")).hexdigest()[:12]
    return f"cligpt:v2:{model}:{web_mode}:{file_mode}:{system_hash}"

def build_prompt_cache_anchor(current_word_count, target_word_count=DEFAULT_PROMPT_CACHE_MIN_STABLE_WORDS):
    if target_word_count <= 0 or current_word_count >= target_word_count:
        return ""
    words_needed = target_word_count - current_word_count
    sentence_words = len(PROMPT_CACHE_ANCHOR_SENTENCE.split())
    repetitions = max(1, (words_needed + sentence_words - 1) // sentence_words)
    return (PROMPT_CACHE_ANCHOR_SENTENCE * repetitions).strip()

def compose_instructions(
    system_message,
    runtime_instructions,
    pruned_context,
    min_stable_words=DEFAULT_PROMPT_CACHE_MIN_STABLE_WORDS,
):
    context_heading = "# Pruned Context History"
    stable_system = system_message.rstrip()
    if stable_system.endswith(context_heading):
        stable_system = stable_system[:-len(context_heading)].rstrip()

    runtime_block = "# Runtime Request Settings\n" + runtime_instructions.strip()
    anchor = build_prompt_cache_anchor(
        len((stable_system + "\n\n" + runtime_block).split()),
        target_word_count=min_stable_words,
    )
    parts = [
        stable_system,
        runtime_block,
    ]
    if anchor:
        parts.append("# Prompt Cache Stability Anchor\n" + anchor)
    parts.append(context_heading)
    if pruned_context.strip():
        parts.append(pruned_context.strip())
    return "\n\n".join(parts)

def count_response_tool_items(response):
    counts = {
        "file_search_calls": 0,
        "file_search_results": 0,
        "web_search_calls": 0,
    }
    if not response:
        return counts
    for item in getattr(response, "output", []) or []:
        item_type = getattr(item, "type", "")
        if item_type == "file_search_call":
            counts["file_search_calls"] += 1
            results = getattr(item, "results", None) or []
            counts["file_search_results"] += len(results)
        elif item_type == "web_search_call":
            counts["web_search_calls"] += 1
    return counts

def direct_attachment_usage(uploaded_attachments):
    files = 0
    bytes_total = 0
    for attachment in uploaded_attachments:
        if attachment.get("path") == "cligpt upload failures":
            continue
        if attachment.get("kind") in {"text", "blob"}:
            bytes_total += len((attachment.get("text") or "").encode("utf-8"))
            files += 1
            continue
        path = attachment.get("source_path") or attachment.get("path")
        if path and os.path.exists(path):
            try:
                bytes_total += os.path.getsize(path)
            except OSError:
                pass
        files += 1
    return {"files": files, "bytes": bytes_total}

def format_bytes(value):
    value = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024

def directory_usage_summary(vector_contexts):
    totals = {
        "reused": 0,
        "uploaded": 0,
        "failed": 0,
        "pruned": 0,
        "remote_adopted": 0,
        "stores": [],
    }
    for context in vector_contexts:
        stats = context.get("stats", {})
        totals["reused"] += stats.get("reused", 0) or 0
        totals["uploaded"] += stats.get("uploaded", 0) or 0
        totals["failed"] += stats.get("failed", 0) or 0
        totals["pruned"] += stats.get("pruned", 0) or 0
        if context.get("remote_adopted"):
            totals["remote_adopted"] += 1
        if context.get("vector_store_id"):
            totals["stores"].append(context["vector_store_id"])
    return totals

def format_timestamp(timestamp):
    if not timestamp:
        return "never"
    try:
        return datetime.datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds")
    except Exception:
        return str(timestamp)

def file_counts_text(file_counts):
    if not file_counts:
        return "unknown"
    if hasattr(file_counts, "model_dump"):
        data = file_counts.model_dump()
    elif isinstance(file_counts, dict):
        data = file_counts
    else:
        data = {
            name: getattr(file_counts, name)
            for name in ("in_progress", "completed", "failed", "cancelled", "total")
            if hasattr(file_counts, name)
        }
    return ", ".join(f"{key}:{value}" for key, value in sorted(data.items()))

def expires_after_text(expires_after):
    if not expires_after:
        return "none"
    if hasattr(expires_after, "model_dump"):
        data = expires_after.model_dump()
    elif isinstance(expires_after, dict):
        data = expires_after
    else:
        data = {
            name: getattr(expires_after, name)
            for name in ("anchor", "days")
            if hasattr(expires_after, name)
        }
    anchor = data.get("anchor", "unknown")
    days = data.get("days", "unknown")
    return f"{days} day(s) after {anchor}"

def print_index_list():
    manager = VectorStoreManager(client)
    stores = manager.list_indexes()
    if not stores:
        print("No vector stores found.")
        return
    total_bytes = sum(store["usage_bytes"] for store in stores)
    print(f"Vector stores: {len(stores)} total usage: {format_bytes(total_bytes)}")
    for store in sorted(stores, key=lambda item: item.get("created_at") or 0, reverse=True):
        metadata = store.get("metadata") or {}
        print(f"{store['id']}  {store['name']}")
        print(f"  usage: {format_bytes(store['usage_bytes'])}")
        print(f"  files: {file_counts_text(store['file_counts'])}")
        print(f"  created: {format_timestamp(store['created_at'])}")
        print(f"  last active: {format_timestamp(store['last_active_at'])}")
        print(f"  expires: {expires_after_text(store['expires_after'])}")
        if metadata.get("cligpt_root_fingerprint"):
            print(f"  cligpt fingerprint: {metadata.get('cligpt_root_fingerprint')[:16]}")

def delete_index(vector_store_id):
    manager = VectorStoreManager(client)
    manager.delete_index(vector_store_id)
    print(f"Deleted vector store: {vector_store_id}")

def expire_index(vector_store_id, days=DEFAULT_VECTOR_STORE_EXPIRATION_DAYS):
    manager = VectorStoreManager(client)
    manager.expire_index(vector_store_id, days=days)
    print(f"Set expiration for {vector_store_id}: {days} day(s) after last_active_at")

def print_index_duplicates():
    manager = VectorStoreManager(client)
    duplicates = manager.duplicate_indexes()
    if not duplicates:
        print("No cligpt vector-store duplicates found.")
        return
    for index, group in enumerate(duplicates, start=1):
        print(f"Duplicate group {index}:")
        for store in sorted(group, key=lambda item: item.get("created_at") or 0, reverse=True):
            print(
                f"  {store['id']} {store['name']} "
                f"usage:{format_bytes(store['usage_bytes'])} "
                f"created:{format_timestamp(store['created_at'])}"
            )

def build_usage_summary(
    completed_response,
    reasoning_tokens_used,
    uploaded_attachments,
    vector_contexts,
    local_context,
    stream_event_counts,
    background_syncs,
    local_tool_stats=None,
    model=MODEL,
    prompt_cache_key=None,
    prompt_cache_retention=None,
):
    tokens = response_usage_dict(completed_response, reasoning_tokens_used)
    tool_counts = count_response_tool_items(completed_response)
    direct = direct_attachment_usage(uploaded_attachments)
    directory = directory_usage_summary(vector_contexts)
    local_stats = (local_context or {}).get("stats", {})
    # Stream counts are a fallback when the completed response omits tool details.
    file_search_calls = tool_counts["file_search_calls"] or (1 if stream_event_counts.get("file_search", 0) else 0)
    web_search_calls = tool_counts["web_search_calls"] or (1 if stream_event_counts.get("web_search", 0) else 0)
    cost = estimate_response_cost(tokens, model, file_search_calls, web_search_calls)
    local_tool_stats = local_tool_stats or {}
    local_tool_text = ", ".join(
        f"{name}:{count} call(s)"
        for name, count in sorted(local_tool_stats.items())
        if count
    ) or "none"
    line = (
        "`usage_cost` "
        f"input:{tokens['input_tokens']:,}; "
        f"output:{tokens['output_tokens']:,}; reasoning:{tokens['reasoning_tokens']:,}; "
        f"total:{tokens['total_tokens']:,}; estimated_cost:{format_estimated_cost(cost)}  \n"
        "`prompt_cache` "
        f"cached_input:{tokens['cached_input_tokens']:,}; "
        f"cache_hit:{format_cache_hit_ratio(tokens)}; "
        f"prompt_cache_key:{abbreviate_table_value(prompt_cache_key)}; "
        f"prompt_cache_retention:{prompt_cache_retention or 'default'}  \n"
        "`file_search_direct_uploads` "
        f"file_search:{file_search_calls} call(s), "
        f"{tool_counts['file_search_results']} result(s); web_search:{web_search_calls} call(s); "
        f"direct_uploads:{direct['files']} file(s), {format_bytes(direct['bytes'])}; "
        f"local_tools:{local_tool_text}  \n"
        "`directory` "
        f"reused:{directory['reused']}; uploaded:{directory['uploaded']}; "
        f"failed:{directory['failed']}; pruned:{directory['pruned']}; "
        f"remote_adopted:{directory['remote_adopted']}; background_syncs:{len(background_syncs)}  \n"
        "`local_search` "
        f"reused:{local_stats.get('reused', 0)}; "
        f"indexed:{local_stats.get('indexed', 0)}; failed:{local_stats.get('failed', 0)}; "
        f"selected:{local_stats.get('selected', 0)}"
    )
    detail = {
        "tokens": tokens,
        "tools": {
            "file_search_calls": file_search_calls,
            "file_search_results": tool_counts["file_search_results"],
            "web_search_calls": web_search_calls,
            "local_tool_calls": dict(local_tool_stats),
        },
        "estimated_cost": cost,
        "prompt_cache": {
            "key": prompt_cache_key,
            "retention": prompt_cache_retention,
            "cached_input_tokens": tokens["cached_input_tokens"],
            "cache_hit_ratio": format_cache_hit_ratio(tokens),
        },
        "direct_uploads": direct,
        "directory": directory,
        "local_search": local_stats,
        "stream_events": {
            "total": stream_event_counts.get("total", 0),
            "by_type": {
                key.removeprefix("type:"): count
                for key, count in sorted(stream_event_counts.items())
                if key.startswith("type:")
            },
        },
        "background_syncs": background_syncs,
    }
    return line, detail

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
    return os.path.abspath(source_file_path(response_id))

def context_file_link():
    return os.path.abspath(CONTEXT_FILE)

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
    if operating_system == "Linux":
        env_os = f"{distribution}, {version}" if version else distribution
    else:
        env_os = f"{operating_system} {version}".strip()
    shell_path = os.getenv("SHELL", "")
    shell = os.path.basename(shell_path) if shell_path else "unknown"
    editor = os.getenv("EDITOR", "unknown")
    package_manager = first_available_command([
        "pacman",
        "apt",
        "dnf",
        "yum",
        "zypper",
        "brew",
        "nix",
    ])
    aur_helper = first_available_command(["paru", "yay"], default="none")
    with open(SYSTEM_MESSAGE_FILE, "r", encoding="utf-8") as f:
        template = f.read().strip()
    
    # Keep the default system prefix deterministic so OpenAI prompt caching can match it.
    formatted_message = template.format(
        distribution=distribution,
        operating_system=operating_system,
        version=version,
        env_os=env_os,
        shell=shell,
        editor=editor,
        package_manager=package_manager,
        aur_helper=aur_helper,
    ).strip()

    if CLIGPT_INCLUDE_NEOFETCH:
        return formatted_message + "\n\n" + get_neofetch_output()
    return formatted_message

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
    index_concurrency=DEFAULT_INDEX_CONCURRENCY,
    remote_search=False,
    allow_partial_index=False,
    wait_index=False,
    output_style=DEFAULT_OUTPUT_STYLE,
    no_color=False,
    heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
    idle_timeout_seconds=DEFAULT_IDLE_TIMEOUT_SECONDS,
    request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    prompt_cache_key=DEFAULT_PROMPT_CACHE_KEY,
    prompt_cache_retention=DEFAULT_PROMPT_CACHE_RETENTION,
    include_context=True,
    full_context=False,
    raw_prompt=False,
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
    capabilities = get_model_capabilities(model)
    if capabilities.reasoning_efforts and reasoning_effort not in capabilities.reasoning_efforts:
        reasoning_effort = capabilities.default_reasoning_effort
    if raw_prompt:
        if (
            normalize_paths(file_paths)
            or normalize_paths(image_paths)
            or normalize_paths(blob_paths)
            or normalize_paths(directory_paths)
        ):
            raise ValueError("--raw cannot be combined with --file, --image, --blob, or --directory.")
        web_search = False
        include_context = False
        full_context = False
    response_id = str(uuid.uuid4())
    output_width, width_source = resolve_output_width(width)
    renderer = TerminalRenderer(
        RenderConfig(width=output_width, style=output_style, no_color=no_color)
    )
    direct_requested = (
        len(normalize_paths(file_paths))
        + len(normalize_paths(image_paths))
        + len(normalize_paths(blob_paths))
    )
    if not directory_paths and direct_requested > capabilities.recommended_max_direct_files:
        sys.stderr.write(
            f"[Warning: {direct_requested} direct attachments requested. "
            f"{model} is configured for about {capabilities.recommended_max_direct_files} "
            f"direct files before vector-store directory mode is recommended.]\n"
        )
        sys.stderr.flush()
    vector_contexts = []
    local_context = None
    background_syncs = []
    if directory_paths:
        if remote_search:
            statuses = directory_sync_status(directory_paths, create=True)
            directory_mode = choose_directory_query_mode(
                statuses,
                index_concurrency,
                allow_partial=allow_partial_index,
                wait_index=wait_index,
            )
            if directory_mode == "cancel":
                raise ValueError("Directory query cancelled before contacting the model.")
            if directory_mode == "sync_first":
                vector_contexts = sync_directory_vector_stores(
                    directory_paths,
                    index_concurrency=index_concurrency,
                )
            elif directory_mode == "partial":
                background_syncs = start_background_directory_sync(
                    directory_paths,
                    index_concurrency=index_concurrency,
                )
                for sync in background_syncs:
                    sys.stderr.write(
                        f"[Background sync started: pid:{sync['pid']} "
                        f"log:{sync['log_path']} root:{sync['root_path']}]\n"
                    )
                sys.stderr.flush()
                vector_contexts = context_for_existing_directory_vector_stores(directory_paths)
            else:
                vector_contexts = context_for_existing_directory_vector_stores(directory_paths)
        else:
            local_context = sync_and_search_local_directories(directory_paths, user_prompt)
    direct_directory_paths = directory_paths if remote_search else None
    uploaded_attachments = upload_attachments(file_paths, image_paths, direct_directory_paths, blob_paths)

    system_message = "" if raw_prompt else load_system_message()
    if include_context:
        pruned_context, chat_blocks, topic_tags, oldest_block = prune_context(
            user_prompt,
            model=model,
            include_metadata=full_context,
        )
    else:
        pruned_context, chat_blocks, topic_tags, oldest_block = "", 0, "disabled", "None"
    
    system_tokens = estimate_tokens_local(system_message)
    context_tokens = estimate_tokens_local(pruned_context)
    user_tokens = estimate_tokens_local(user_prompt)
    total_context_tokens = system_tokens + context_tokens + user_tokens
    recent_history_budget = get_recent_history_token_budget(model)
    attachment_count = len(uploaded_attachments)
    vector_store_count = len(vector_contexts)
    max_output_tokens = min(MAX_OUTPUT_TOKENS, capabilities.max_output_tokens)

    # Build header
    web_label = "web:on" if web_search else "web:off"
    if raw_prompt:
        file_mode = "raw"
    elif vector_contexts:
        file_mode = "file_search"
    elif local_context:
        file_mode = "local_search"
    else:
        file_mode = "direct"
    header_basic = (
        f"[{model} - {reasoning_effort} - {web_label} - files:{file_mode} - "
        f"context:{capabilities.max_context_tokens:,} - safe input:{capabilities.safe_input_tokens:,} - "
        f"max output:{max_output_tokens:,} - width: {output_width} ({width_source})]"
    )
    debug_header = (f"[Preflight Context Estimate: {total_context_tokens} approx tokens]\n"
                    f"  [System Message Estimate: {system_tokens}]\n"
                    f"  [Pruned Context Estimate: {context_tokens}]\n"
                    f"  [Recent History Budget: {recent_history_budget}]\n"
                    f"    [Chat Blocks: {chat_blocks}]\n"
                    f"    [Topic Tags: {topic_tags}]\n"
                    f"    [Oldest Block: {oldest_block}]\n"
                    f"  [User Prompt Estimate: {user_tokens}]\n"
                    f"  [Raw Prompt Mode: {'enabled' if raw_prompt else 'disabled'}]\n"
                    f"  [Context History: {'enabled' if include_context else 'disabled'}]\n"
                    f"  [Full Context Metadata: {'enabled' if include_context and full_context else 'disabled'}]\n"
                    f"  [Web Search: {'enabled' if web_search else 'disabled'}]\n"
                    f"  [Attachments: {attachment_count}]\n"
                    f"  [Vector Stores: {vector_store_count}]\n"
                    f"  [Local Search Chunks: {local_context['stats'].get('selected', 0) if local_context else 0}]\n"
                    f"  [Background Directory Syncs: {len(background_syncs)}]\n"
                    f"  [Model Capability Confidence: {capabilities.confidence}]\n"
                    f"  [Output Width: {output_width} ({width_source})]\n")
    
    runtime_instructions = "" if raw_prompt else (
        f"Format the visible answer for a terminal with a hard maximum "
        f"line length of {output_width} characters. Prefer lines as close "
        f"to {output_width} characters as natural wording allows. Do not "
        f"use lines longer than {output_width} characters."
        f"\n\nWeb search is {'enabled' if web_search else 'disabled'} for "
        f"this request. When web search is enabled, use it for current, "
        f"fast-changing, or source-sensitive facts. If web search is used, "
        f"make source URLs visible in the answer."
        f"\n\nThe selected model has a configured context window of "
        f"{capabilities.max_context_tokens:,} tokens and a conservative "
        f"safe input budget of {capabilities.safe_input_tokens:,} tokens. "
        f"When file_search or local directory search is available, prefer "
        f"targeted retrieval evidence over assuming entire directories are "
        f"already loaded into context."
        f"\n\nA read-only local function tool named {LOCAL_TOOL_GET_SYSTEM_PROFILE} "
        f"is available. Call it only when the user's task depends on this "
        f"machine's local OS, hardware, display, storage, package manager, "
        f"or installed command-line tools. Do not call it for ordinary questions."
    )
    combined_system = "" if raw_prompt else compose_instructions(
        system_message,
        runtime_instructions,
        pruned_context,
        min_stable_words=DEFAULT_PROMPT_CACHE_MIN_STABLE_WORDS if include_context else 0,
    )
    effective_prompt_cache_key = None if raw_prompt else build_prompt_cache_key(
        model,
        system_message,
        web_search,
        vector_contexts=vector_contexts,
        local_context=local_context,
        uploaded_attachments=uploaded_attachments,
        explicit_key=prompt_cache_key,
    )
    effective_prompt_cache_retention = None if raw_prompt else normalize_prompt_cache_retention(prompt_cache_retention, model)

    debug_header += (
        f"  [Prompt Cache Key: {effective_prompt_cache_key}]\n"
        f"  [Prompt Cache Retention: {effective_prompt_cache_retention or 'default'}]\n"
    )

    if debug:
        renderer.meta(header_basic)
        sys.stdout.write(debug_header)
    else:
        renderer.meta(header_basic)
    sys.stdout.flush()
    
    if raw_prompt:
        request_args = {
            "model": model,
            "input": user_prompt,
            "max_output_tokens": max_output_tokens,
            "stream": True,
            "store": True,
        }
    else:
        request_args = {
            "model": model,
            "instructions": combined_system,
            "input": [{"role": "user", "content": build_user_content(user_prompt, uploaded_attachments, vector_contexts, local_context)}],
            "max_output_tokens": max_output_tokens,
            "stream": True,
            "text": {"format": {"type": "text"}},
            "store": True,
            "prompt_cache_key": effective_prompt_cache_key,
        }
    if effective_prompt_cache_retention:
        request_args["prompt_cache_retention"] = effective_prompt_cache_retention
    tools = []
    include = []
    if web_search:
        tools.append({"type": "web_search"})
        include.append("web_search_call.action.sources")
    if vector_contexts:
        if not capabilities.supports_file_search:
            raise ValueError(f"Model {model} is not configured for file_search directory mode.")
        tools.append({
            "type": "file_search",
            "vector_store_ids": [context["vector_store_id"] for context in vector_contexts],
            "max_num_results": 50,
        })
        include.append("file_search_call.results")
    if not raw_prompt and LOCAL_TOOL_GET_SYSTEM_PROFILE in capabilities.tools:
        tools.extend(local_tool_schemas())
    if tools:
        request_args["tools"] = tools
        request_args["tool_choice"] = "auto"
    if include:
        request_args["include"] = include
    if not raw_prompt and capabilities.supports_reasoning_effort(reasoning_effort):
        request_args["reasoning"] = {"effort": reasoning_effort}

    if debug:
        request_breakdown = request_input_debug_breakdown(
            request_args,
            system_message=system_message,
            runtime_instructions=runtime_instructions,
            pruned_context=pruned_context,
            user_prompt=user_prompt,
            local_context=local_context,
        )
        sys.stdout.write(format_request_input_debug_breakdown(request_breakdown))
        sys.stdout.flush()

    stream = client.responses.create(**request_args, timeout=request_timeout_seconds)

    visible_chunks = []
    reasoning_tokens_used = 0
    completed_response = None
    stream_event_counts = Counter()
    wrapper = StreamingLineWrapper(output_width)

    if debug:
        sys.stderr.write("[streaming response]\n")
        sys.stderr.flush()
    else:
        if renderer.style == "plain" or not renderer.enabled:
            sys.stdout.write("\n")
    sys.stdout.flush()

    completed_responses = []
    local_tool_stats = Counter()

    def stream_visible_response(active_stream):
        nonlocal completed_response, reasoning_tokens_used
        for event in iter_stream_with_heartbeat(
            active_stream,
            debug=debug,
            heartbeat_seconds=heartbeat_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
        ):
            event_type = getattr(event, "type", "")
            stream_event_counts["total"] += 1
            if event_type:
                stream_event_counts[f"type:{event_type}"] += 1
            if "file_search" in event_type:
                stream_event_counts["file_search"] += 1
            if "web_search" in event_type:
                stream_event_counts["web_search"] += 1
            if event_type in {"response.output_text.delta", "response.refusal.delta"}:
                content = getattr(event, "delta", "")
                if renderer.style == "plain" or not renderer.enabled:
                    visible_text = wrapper.feed(content)
                    if visible_text:
                        visible_chunks.append(visible_text)
                        yield visible_text
                else:
                    visible_chunks.append(content)
                    yield content
            elif event_type == "response.completed":
                completed_response = getattr(event, "response", None)
                completed_responses.append(completed_response)
                reasoning_tokens_used += (
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

    def visible_text_chunks():
        nonlocal stream, completed_response
        active_stream = stream
        for _round in range(MAX_LOCAL_TOOL_ROUNDS + 1):
            yield from stream_visible_response(active_stream)
            tool_calls = collect_local_tool_calls(completed_response)
            if not tool_calls:
                break
            if _round >= MAX_LOCAL_TOOL_ROUNDS:
                raise RuntimeError("Model requested too many local tool rounds.")
            tool_outputs = []
            for call in tool_calls:
                if not call.get("call_id"):
                    continue
                local_tool_stats[call["name"]] += 1
                if debug:
                    sys.stderr.write(f"[Local tool call: {call['name']}]\n")
                    sys.stderr.flush()
                tool_outputs.append(execute_local_tool_call(call))
            if not tool_outputs:
                break
            active_stream = client.responses.create(
                **{
                    **request_args,
                    "input": tool_outputs,
                    "previous_response_id": get_nested_attr(completed_response, "id"),
                },
                timeout=request_timeout_seconds,
            )
            stream = active_stream

    usage_detail = None

    def build_final_response(streamed_answer, *, markdown=True):
        nonlocal completed_response, usage_detail
        completed_response = aggregate_completed_responses(completed_responses, reasoning_tokens_used)

        sections = []
        usage_line, usage_detail = build_usage_summary(
            completed_response,
            reasoning_tokens_used,
            uploaded_attachments,
            vector_contexts,
            local_context,
            stream_event_counts,
            background_syncs,
            local_tool_stats,
            model=model,
            prompt_cache_key=effective_prompt_cache_key,
            prompt_cache_retention=effective_prompt_cache_retention,
        )
        sections.append(("### Token Usage\n\n" if markdown else "Token Usage:\n") + usage_line)

        final_answer = streamed_answer.rstrip()
        tail = "\n\n".join(sections)
        if tail:
            final_answer = final_answer + ("\n\n" if final_answer else "") + tail
        return final_answer, tail

    try:
        if renderer.enabled:
            answer_text = renderer.render_stream_with_final(
                visible_text_chunks(),
                lambda streamed: build_final_response(streamed, markdown=True)[0],
            )
        else:
            renderer.render_stream(visible_text_chunks())
            if renderer.style == "plain" or not renderer.enabled:
                final_text = wrapper.finish()
                if final_text:
                    visible_chunks.append(final_text)
                    sys.stdout.write(final_text)
            streamed_answer = "".join(visible_chunks)
            answer_text, tail = build_final_response(streamed_answer, markdown=False)
            if tail:
                sys.stdout.write("\n\n" + tail + "\n")
    except KeyboardInterrupt:
        try:
            stream.close()
        except Exception:
            pass
        sys.stderr.write("\n[Interrupted by user. Local index state was not rolled back.]\n")
        sys.stderr.flush()
        raise

    context_answer_text = "".join(visible_chunks).strip()
    if not context_answer_text:
        context_answer_text = answer_text.strip()

    persisted_answer_text = answer_text.strip()

    final_citations = collect_final_answer_citations(completed_response) if completed_response else []
    if final_citations:
        source_lines = ["Sources:"]
        sys.stdout.write("\n\nSources:\n")
        for index, citation in enumerate(final_citations, start=1):
            source_line = f"[{index}] {citation['title']}: {citation['url']}"
            source_lines.append(source_line)
            sys.stdout.write(source_line + "\n")
        persisted_answer_text += "\n\n" + "\n".join(source_lines)

    cited_urls = {citation["url"] for citation in final_citations}
    uncited_sources = (
        collect_uncited_web_sources(completed_response, cited_urls)
        if completed_response else []
    )
    if log_uncited_web_sources(response_id, user_prompt, uncited_sources):
        note = (
            "\n*Additional web-search sources were returned but not cited in "
            f"the final answer, logged in [source details]({source_file_link(response_id)}). "
            f"Conversation history is in [context]({context_file_link()}).*"
        )
        sys.stdout.write(note + "\n")
        persisted_answer_text += note

    if debug:
        total_stream_events = (usage_detail or {}).get("stream_events", {}).get("total", 0)
        sys.stdout.write(
            "\n[Usage Detail]\n"
            + format_stream_event_debug_line(total_stream_events)
            + "\n"
            + json.dumps(usage_detail, indent=2, sort_keys=True)
            + "\n"
        )
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()
    
    if raw_prompt:
        topics = []
    else:
        try:
            topics = extract_topic_tags(user_prompt, context_answer_text)
        except Exception as exc:
            if debug:
                sys.stderr.write(f"[Topic tag extraction failed: {exc}]\n")
            topics = []
    add_to_context(user_prompt, persisted_answer_text, topics, reasoning_effort, response_id=response_id)
    return answer_text
