import datetime
import hashlib
import os
import re
import sqlite3
import sys
import tempfile
import time

from vector_store_manager import state_root


LOCAL_INDEX_DB = os.path.join(state_root(), "local_search.sqlite3")
CHUNK_SIZE = 4000
CHUNK_OVERLAP = 400
MAX_FILE_TEXT_CHARS = 200_000
DEFAULT_RESULT_CHUNKS = 18
MAX_CONTEXT_CHARS = 90_000

STOPWORDS = {
    "a", "about", "all", "also", "an", "and", "are", "as", "at", "be",
    "based", "by", "can", "consider", "create", "do", "for", "from",
    "give", "has", "have", "i", "in", "information", "is", "it", "me",
    "my", "of", "on", "or", "return", "that", "the", "this", "to",
    "using", "with", "you",
}

ARCHIVE_PROMPT_TERMS = {
    "all", "archive", "archived", "disposed", "former", "historical",
    "history", "old", "past", "previous", "sold",
}


def sha256_path(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def prompt_allows_archive(prompt):
    tokens = set(query_terms(prompt, limit=100))
    return bool(tokens & ARCHIVE_PROMPT_TERMS)


def query_terms(text, limit=16):
    terms = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_'-]{1,}", text.lower()):
        token = token.strip("'_-")
        if len(token) < 2 or token in STOPWORDS:
            continue
        terms.append(token)
    seen = set()
    unique = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        unique.append(term)
        if len(unique) >= limit:
            break
    return unique


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(length, start + chunk_size)
        if end < length:
            newline = cleaned.rfind("\n", start + chunk_size // 2, end)
            if newline > start:
                end = newline
        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


class LocalSearchIndex:
    def __init__(self, db_path=LOCAL_INDEX_DB):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.ensure_schema()

    def close(self):
        self.conn.close()

    def ensure_schema(self):
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                root_path TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                rel_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                classification TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                text,
                path UNINDEXED,
                rel_path UNINDEXED,
                classification UNINDEXED,
                content=''
            );
            CREATE TABLE IF NOT EXISTS chunk_map (
                fts_rowid INTEGER PRIMARY KEY,
                chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_path);
            CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
            """
        )
        self.conn.commit()

    def file_current(self, upload):
        path = upload["path"]
        try:
            stat = os.stat(path)
        except OSError:
            return False
        row = self.conn.execute(
            "SELECT size_bytes, mtime_ns FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        return bool(row and row["size_bytes"] == stat.st_size and row["mtime_ns"] == stat.st_mtime_ns)

    def upsert_upload(self, upload, text):
        path = upload["path"]
        root_path = upload.get("root_path") or os.path.dirname(path)
        rel_path = os.path.relpath(path, root_path)
        stat = os.stat(path)
        sha256 = sha256_path(path)
        classification = upload.get("classification") or ""
        now = utc_now()
        existing = self.conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        if existing:
            file_id = existing["id"]
            self.conn.execute(
                """
                UPDATE files
                SET root_path = ?, rel_path = ?, kind = ?, size_bytes = ?,
                    mtime_ns = ?, sha256 = ?, classification = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    root_path,
                    rel_path,
                    upload["kind"],
                    stat.st_size,
                    stat.st_mtime_ns,
                    sha256,
                    classification,
                    now,
                    file_id,
                ),
            )
            self._delete_chunks(file_id)
        else:
            cursor = self.conn.execute(
                """
                INSERT INTO files
                    (root_path, path, rel_path, kind, size_bytes, mtime_ns,
                     sha256, classification, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    root_path,
                    path,
                    rel_path,
                    upload["kind"],
                    stat.st_size,
                    stat.st_mtime_ns,
                    sha256,
                    classification,
                    now,
                ),
            )
            file_id = cursor.lastrowid

        for index, chunk in enumerate(chunk_text(text[:MAX_FILE_TEXT_CHARS])):
            chunk_cursor = self.conn.execute(
                "INSERT INTO chunks (file_id, chunk_index, text) VALUES (?, ?, ?)",
                (file_id, index, chunk),
            )
            fts_cursor = self.conn.execute(
                """
                INSERT INTO chunks_fts (text, path, rel_path, classification)
                VALUES (?, ?, ?, ?)
                """,
                (chunk, path, rel_path, classification),
            )
            self.conn.execute(
                "INSERT INTO chunk_map (fts_rowid, chunk_id) VALUES (?, ?)",
                (fts_cursor.lastrowid, chunk_cursor.lastrowid),
            )
        self.conn.commit()

    def _delete_chunks(self, file_id):
        rows = self.conn.execute(
            """
            SELECT chunk_map.fts_rowid
            FROM chunk_map
            JOIN chunks ON chunks.id = chunk_map.chunk_id
            WHERE chunks.file_id = ?
            """,
            (file_id,),
        ).fetchall()
        for row in rows:
            self.conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (row["fts_rowid"],))
        self.conn.execute(
            "DELETE FROM chunk_map WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id = ?)",
            (file_id,),
        )
        self.conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))

    def prune_missing(self, root_paths):
        roots = [os.path.abspath(os.path.expanduser(root)) for root in root_paths]
        pruned = 0
        for root in roots:
            rows = self.conn.execute(
                "SELECT id, path FROM files WHERE root_path = ?",
                (root,),
            ).fetchall()
            for row in rows:
                if os.path.exists(row["path"]):
                    continue
                self._delete_chunks(row["id"])
                self.conn.execute("DELETE FROM files WHERE id = ?", (row["id"],))
                pruned += 1
        self.conn.commit()
        return pruned

    def sync_uploads(self, uploads, prepare_text, progress=None):
        stats = {"indexed": 0, "reused": 0, "failed": 0, "pruned": 0, "total": len(uploads)}
        started = time.monotonic()
        last_eta_at = started
        last_eta_line = 0
        roots = sorted({upload.get("root_path") for upload in uploads if upload.get("root_path")})
        stats["pruned"] = self.prune_missing(roots)
        with tempfile.TemporaryDirectory(prefix="cligpt-local-index-") as temp_directory:
            for index, upload in enumerate(uploads, start=1):
                if progress:
                    progress(f"[Local search index {index}/{len(uploads)}: {upload['path']}]\n")
                try:
                    if self.file_current(upload):
                        stats["reused"] += 1
                    else:
                        text_path = prepare_text(upload, temp_directory)
                        if not text_path:
                            raise ValueError("no local text could be extracted")
                        with open(text_path, "r", encoding="utf-8", errors="replace") as handle:
                            text = handle.read()
                        self.upsert_upload(upload, text)
                        stats["indexed"] += 1
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    stats["failed"] += 1
                    if progress:
                        progress(f"[Local search skipped after failure: {upload['path']}: {exc}]\n")
                completed = stats["indexed"] + stats["reused"] + stats["failed"]
                now = time.monotonic()
                if progress and (
                    completed - last_eta_line >= 100
                    or now - last_eta_at >= 300
                ):
                    elapsed = now - started
                    remaining = len(uploads) - completed
                    rate = completed / elapsed if elapsed > 0 else 0
                    eta = int(remaining / rate) if rate else 0
                    progress(
                        f"[Local search index ETA: {completed}/{len(uploads)} complete, "
                        f"about {format_duration(eta)} remaining]\n"
                    )
                    last_eta_at = now
                    last_eta_line = completed
        return stats

    def search(self, root_paths, prompt, limit=DEFAULT_RESULT_CHUNKS):
        roots = [os.path.abspath(os.path.expanduser(root)) for root in root_paths]
        terms = query_terms(prompt)
        if not terms:
            return []
        match = " OR ".join(terms)
        allow_archive = prompt_allows_archive(prompt)
        params = [match, *roots]
        archive_filter = ""
        if not allow_archive:
            archive_filter = "AND files.classification != 'archive/disposed candidate'"
        query = f"""
            SELECT chunks.text, files.path, files.rel_path, files.kind,
                   files.classification, bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunk_map ON chunk_map.fts_rowid = chunks_fts.rowid
            JOIN chunks ON chunks.id = chunk_map.chunk_id
            JOIN files ON files.id = chunks.file_id
            WHERE chunks_fts MATCH ?
              AND files.root_path IN ({",".join("?" for _ in roots)})
              {archive_filter}
            ORDER BY score
            LIMIT ?
        """
        rows = []
        try:
            rows = self.conn.execute(query, (*params, limit * 2)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            rows = self._fallback_search(roots, terms, limit * 2, allow_archive)
        return [dict(row) for row in rows[:limit]]

    def _fallback_search(self, roots, terms, limit, allow_archive):
        rows = self.conn.execute(
            f"""
            SELECT chunks.text, files.path, files.rel_path, files.kind,
                   files.classification, 0 AS score
            FROM chunks
            JOIN files ON files.id = chunks.file_id
            WHERE files.root_path IN ({",".join("?" for _ in roots)})
            """,
            roots,
        ).fetchall()
        scored = []
        for row in rows:
            if not allow_archive and row["classification"] == "archive/disposed candidate":
                continue
            haystack = (row["rel_path"] + "\n" + row["text"]).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                item = dict(row)
                item["score"] = -score
                scored.append(item)
        scored.sort(key=lambda item: item["score"])
        return scored[:limit]


def format_duration(seconds):
    seconds = max(0, int(seconds))
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minute:02d}m{second:02d}s"
    if minutes:
        return f"{minutes}m{second:02d}s"
    return f"{second}s"


def build_local_context(root_paths, prompt, stats, results):
    lines = [
        "Local directory search results are preselected by cligpt before the model request.",
        "The model does not have direct access to the full directory; use only the snippets below and say when evidence is insufficient.",
        "Archive/disposed candidate paths are excluded from current operating reports unless the user explicitly asks for historical/all/former/sold material.",
        "",
        f"Roots: {', '.join(os.path.abspath(os.path.expanduser(root)) for root in root_paths)}",
        (
            "Local index stats: "
            f"reused {stats['reused']}; indexed {stats['indexed']}; failed {stats['failed']}; "
            f"pruned {stats['pruned']}; selected {len(results)} chunk(s)."
        ),
        "",
    ]
    used_chars = sum(len(line) + 1 for line in lines)
    for index, result in enumerate(results, start=1):
        header = (
            f"--- Local search result {index}: {result['rel_path']} "
            f"({result['classification']}, {result['kind']}) ---"
        )
        body = result["text"].strip()
        remaining = MAX_CONTEXT_CHARS - used_chars - len(header) - 64
        if remaining <= 0:
            break
        if len(body) > remaining:
            body = body[:remaining].rstrip() + "\n[truncated]"
        lines.extend([header, f"Source path: {result['path']}", body, ""])
        used_chars += len(header) + len(result["path"]) + len(body) + 32
    return "\n".join(lines)

