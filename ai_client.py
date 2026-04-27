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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import pathname2url
from openai import OpenAI
from config import (
    MAX_OUTPUT_TOKENS,
    MODEL,
    FAST_MODEL,
    SYSTEM_MESSAGE_FILE,
    SOURCES_DIR,
    MAX_UPLOAD_FILES,
    MAX_DIRECTORY_FILES,
    MAX_DIRECT_VISION_UPLOADS,
    MAX_UPLOAD_RETRIES,
    DEFAULT_INDEX_CONCURRENCY,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_OUTPUT_STYLE,
    MAX_DIRECT_PDF_UPLOAD_BYTES,
    MAX_COMPRESSED_PDF_UPLOAD_BYTES,
)
from memory_manager import (
        get_neofetch_output,
        prune_context,
        add_to_context,
)
from model_capabilities import get_model_capabilities
from render import RenderConfig, TerminalRenderer
from vector_store_manager import VectorStoreManager, root_hash, state_root

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

def split_uploads_for_directory_search(directory_paths):
    searchable_uploads = []
    direct_uploads = []
    directory_uploads = collect_uploads(directory_paths=directory_paths)
    for upload in directory_uploads:
        searchable_uploads.append(upload)
    return searchable_uploads, direct_uploads

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

def build_user_content(user_prompt, uploaded_attachments, vector_contexts=None):
    content = [{"type": "input_text", "text": user_prompt}]
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
            if debug and (
                event_type in {
                    "response.output_text.delta",
                    "response.refusal.delta",
                    "response.completed",
                    "response.failed",
                    "error",
                    "response.error",
                }
                or "file_search" in event_type
                or "web_search" in event_type
            ):
                sys.stderr.write(f"[OpenAI stream event: {event_type}]\n")
                sys.stderr.flush()
            if not first_text_seen and event_type in {"response.output_text.delta", "response.refusal.delta"}:
                first_text_seen = True
                if debug:
                    sys.stderr.write(
                        f"[First visible output after {format_duration(last_event_at - started_at)}]\n"
                    )
                    sys.stderr.flush()
            yield event
        elif kind == "error":
            raise payload
        elif kind == "done":
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

def response_usage_dict(response, reasoning_tokens=0):
    usage = getattr(response, "usage", None) if response else None
    return {
        "input_tokens": get_nested_attr(usage, "input_tokens", default=0) or 0,
        "output_tokens": get_nested_attr(usage, "output_tokens", default=0) or 0,
        "total_tokens": get_nested_attr(usage, "total_tokens", default=0) or 0,
        "reasoning_tokens": reasoning_tokens or get_nested_attr(
            usage,
            "output_tokens_details",
            "reasoning_tokens",
            default=0,
        ) or 0,
    }

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

def build_usage_summary(
    completed_response,
    reasoning_tokens_used,
    uploaded_attachments,
    vector_contexts,
    stream_event_counts,
    background_syncs,
):
    tokens = response_usage_dict(completed_response, reasoning_tokens_used)
    tool_counts = count_response_tool_items(completed_response)
    direct = direct_attachment_usage(uploaded_attachments)
    directory = directory_usage_summary(vector_contexts)
    # Stream counts are a fallback when the completed response omits tool details.
    file_search_calls = tool_counts["file_search_calls"] or (1 if stream_event_counts.get("file_search", 0) else 0)
    web_search_calls = tool_counts["web_search_calls"] or (1 if stream_event_counts.get("web_search", 0) else 0)
    line = (
        "[Usage: "
        f"input:{tokens['input_tokens']:,} output:{tokens['output_tokens']:,} "
        f"reasoning:{tokens['reasoning_tokens']:,} total:{tokens['total_tokens']:,} | "
        f"file_search:{file_search_calls} call(s), {tool_counts['file_search_results']} result(s) | "
        f"web_search:{web_search_calls} call(s) | "
        f"direct_uploads:{direct['files']} file(s), {format_bytes(direct['bytes'])} | "
        f"directory:reused {directory['reused']}, uploaded {directory['uploaded']}, "
        f"failed {directory['failed']}, pruned {directory['pruned']}, "
        f"remote_adopted {directory['remote_adopted']}, background_syncs {len(background_syncs)}"
        "]"
    )
    detail = {
        "tokens": tokens,
        "tools": {
            "file_search_calls": file_search_calls,
            "file_search_results": tool_counts["file_search_results"],
            "web_search_calls": web_search_calls,
        },
        "direct_uploads": direct,
        "directory": directory,
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
    index_concurrency=DEFAULT_INDEX_CONCURRENCY,
    allow_partial_index=False,
    wait_index=False,
    output_style=DEFAULT_OUTPUT_STYLE,
    no_color=False,
    heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
    idle_timeout_seconds=DEFAULT_IDLE_TIMEOUT_SECONDS,
    request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
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
    background_syncs = []
    if directory_paths:
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
    vector_store_count = len(vector_contexts)
    max_output_tokens = min(MAX_OUTPUT_TOKENS, capabilities.max_output_tokens)

    # Build header
    web_label = "web:on" if web_search else "web:off"
    file_mode = "file_search" if vector_contexts else "direct"
    header_basic = (
        f"[{model} - {reasoning_effort} - {web_label} - files:{file_mode} - "
        f"context:{capabilities.max_context_tokens:,} - safe input:{capabilities.safe_input_tokens:,} - "
        f"max output:{max_output_tokens:,} - width: {output_width} ({width_source})]"
    )
    debug_header = (f"[Context Tokens: {total_context_tokens}]\n"
                    f"  [System Message: {system_tokens}]\n"
                    f"  [Pruned Context: {context_tokens}]\n"
                    f"    [Chat Blocks: {chat_blocks}]\n"
                    f"    [Topic Tags: {topic_tags}]\n"
                    f"    [Oldest Block: {oldest_block}]\n"
                    f"  [User Prompt: {user_tokens}]\n"
                    f"  [Web Search: {'enabled' if web_search else 'disabled'}]\n"
                    f"  [Attachments: {attachment_count}]\n"
                    f"  [Vector Stores: {vector_store_count}]\n"
                    f"  [Background Directory Syncs: {len(background_syncs)}]\n"
                    f"  [Model Capability Confidence: {capabilities.confidence}]\n"
                    f"  [Output Width: {output_width} ({width_source})]\n")
    
    if debug:
        renderer.meta(header_basic)
        sys.stdout.write(debug_header)
    else:
        renderer.meta(header_basic)
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
            f"\n\nThe selected model has a configured context window of "
            f"{capabilities.max_context_tokens:,} tokens and a conservative "
            f"safe input budget of {capabilities.safe_input_tokens:,} tokens. "
            f"When file_search is available, prefer targeted retrieval over "
            f"asking for entire directories to be loaded into context."
        )
    )
    
    request_args = {
        "model": model,
        "instructions": combined_system,
        "input": [{"role": "user", "content": build_user_content(user_prompt, uploaded_attachments, vector_contexts)}],
        "max_output_tokens": max_output_tokens,
        "stream": True,
        "text": {"format": {"type": "text"}},
        "store": True
    }
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
    if tools:
        request_args["tools"] = tools
        request_args["tool_choice"] = "auto"
    if include:
        request_args["include"] = include
    if capabilities.supports_reasoning_effort(reasoning_effort):
        request_args["reasoning"] = {"effort": reasoning_effort}

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

    def visible_text_chunks():
        nonlocal completed_response, reasoning_tokens_used
        for event in iter_stream_with_heartbeat(
            stream,
            debug=debug,
            heartbeat_seconds=heartbeat_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
        ):
            event_type = getattr(event, "type", "")
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

    try:
        renderer.render_stream(visible_text_chunks())
    except KeyboardInterrupt:
        try:
            stream.close()
        except Exception:
            pass
        sys.stderr.write("\n[Interrupted by user. Local index state was not rolled back.]\n")
        sys.stderr.flush()
        raise

    if renderer.style == "plain" or not renderer.enabled:
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

    usage_line, usage_detail = build_usage_summary(
        completed_response,
        reasoning_tokens_used,
        uploaded_attachments,
        vector_contexts,
        stream_event_counts,
        background_syncs,
    )
    sys.stdout.write("\n" + usage_line + "\n")
    answer_text += "\n" + usage_line

    if debug:
        sys.stdout.write(
            "\n[Usage Detail]\n"
            + json.dumps(usage_detail, indent=2, sort_keys=True)
            + "\n"
        )
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
