import hashlib
import os
import sqlite3
import sys
import tempfile
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from config import (
    DEFAULT_INDEX_CONCURRENCY,
    INDEX_ETA_INTERVAL_FILES,
    INDEX_ETA_INTERVAL_SECONDS,
    STATE_DIR,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS corpora (
    id INTEGER PRIMARY KEY,
    root_path TEXT NOT NULL UNIQUE,
    root_hash TEXT NOT NULL,
    vector_store_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_sync_at REAL,
    remote_adopted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    corpus_id INTEGER NOT NULL,
    abs_path TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    kind TEXT NOT NULL,
    upload_path TEXT,
    upload_sha256 TEXT,
    openai_file_id TEXT,
    vector_store_file_id TEXT,
    status TEXT NOT NULL,
    last_error TEXT,
    updated_at REAL NOT NULL,
    UNIQUE(corpus_id, abs_path),
    FOREIGN KEY(corpus_id) REFERENCES corpora(id)
);
"""


def state_root():
    root = os.getenv("GPT_HOME") or os.getcwd()
    path = os.path.join(root, STATE_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def database_path():
    return os.path.join(state_root(), "vector_stores.db")


def connect():
    db = sqlite3.connect(database_path())
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(corpora)").fetchall()}
    if "remote_adopted" not in columns:
        db.execute("ALTER TABLE corpora ADD COLUMN remote_adopted INTEGER NOT NULL DEFAULT 0")
        db.commit()
    return db


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def root_hash(path):
    return hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:12]


def directory_identity_fingerprint(root_path, uploads=None):
    root_path = os.path.abspath(os.path.expanduser(root_path))
    basename = os.path.basename(root_path) or "root"
    rel_paths = []
    for upload in uploads or []:
        try:
            rel_path = os.path.relpath(upload["path"], root_path)
        except ValueError:
            continue
        parts = rel_path.split(os.sep)
        rel_paths.append(os.path.join(*parts[:2]) if len(parts) > 1 else parts[0])
    unique_rel_paths = sorted(set(rel_paths))[:1000]
    payload = {
        "schema": 1,
        "basename": basename,
        "shallow_paths": unique_rel_paths,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def portable_corpus_name(root_path, fingerprint):
    basename = os.path.basename(os.path.abspath(root_path)) or "root"
    return f"cligpt:{basename}:{fingerprint[:12]}"


def corpus_name(root_path):
    basename = os.path.basename(os.path.abspath(root_path)) or "root"
    return f"cligpt:{basename}:{root_hash(root_path)}"


class VectorStoreManager:
    def __init__(self, openai_client=None):
        self.client = openai_client or OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def new_worker_client(self):
        return OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            max_retries=0,
            timeout=120.0,
        )

    def api_key_hash(self):
        api_key = os.getenv("OPENAI_API_KEY") or ""
        if not api_key:
            return "missing"
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]

    def vector_store_metadata(self, vector_store):
        metadata = getattr(vector_store, "metadata", None)
        return metadata or {}

    def discover_remote_corpus(self, root_path, uploads):
        fingerprint = directory_identity_fingerprint(root_path, uploads)
        expected_key_hash = self.api_key_hash()
        try:
            page = self.client.vector_stores.list(limit=100)
        except Exception as exc:
            sys.stderr.write(f"[Remote vector-store discovery unavailable: {exc}]\n")
            sys.stderr.flush()
            return None

        while True:
            for vector_store in getattr(page, "data", []) or []:
                metadata = self.vector_store_metadata(vector_store)
                if (
                    metadata.get("cligpt_schema_version") == "1"
                    and metadata.get("cligpt_root_fingerprint") == fingerprint
                    and metadata.get("cligpt_api_key_hash") == expected_key_hash
                ):
                    return vector_store
            if not getattr(page, "has_more", False):
                break
            after = getattr(getattr(page, "data", [])[-1], "id", None) if getattr(page, "data", None) else None
            if not after:
                break
            try:
                page = self.client.vector_stores.list(limit=100, after=after)
            except Exception:
                break
        return None

    def adopt_remote_corpus(self, db, root_path, uploads):
        root_path = os.path.abspath(os.path.expanduser(root_path))
        remote = self.discover_remote_corpus(root_path, uploads)
        if not remote:
            return None
        fingerprint = directory_identity_fingerprint(root_path, uploads)
        name = getattr(remote, "name", None) or portable_corpus_name(root_path, fingerprint)
        sys.stderr.write(f"[Adopted remote vector store: {name} id:{remote.id}]\n")
        sys.stderr.flush()
        self.insert_corpus_row(
            db,
            root_path,
            remote.id,
            name,
            created_at=time.time(),
            last_sync_at=time.time(),
            remote_adopted=True,
        )
        db.commit()
        return dict(db.execute(
            "SELECT * FROM corpora WHERE root_path = ?",
            (root_path,),
        ).fetchone())

    def insert_corpus_row(
        self,
        db,
        root_path,
        vector_store_id,
        name,
        created_at=None,
        last_sync_at=None,
        remote_adopted=False,
    ):
        now = time.time()
        db.execute(
            """
            INSERT INTO corpora (root_path, root_hash, vector_store_id, name, created_at, last_sync_at, remote_adopted)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                root_path,
                root_hash(root_path),
                vector_store_id,
                name,
                created_at or now,
                last_sync_at,
                1 if remote_adopted else 0,
            ),
        )

    def ensure_corpus(self, root_path, uploads=None):
        root_path = os.path.abspath(os.path.expanduser(root_path))
        now = time.time()
        with connect() as db:
            existing = db.execute(
                "SELECT * FROM corpora WHERE root_path = ?",
                (root_path,),
            ).fetchone()
            if existing:
                return dict(existing)

            fingerprint = directory_identity_fingerprint(root_path, uploads)
            remote = self.discover_remote_corpus(root_path, uploads)
            if remote:
                return self.adopt_remote_corpus(db, root_path, uploads)

            name = portable_corpus_name(root_path, fingerprint)
            vector_store = self.client.vector_stores.create(
                name=name,
                metadata={
                    "cligpt_schema_version": "1",
                    "cligpt_api_key_hash": self.api_key_hash(),
                    "cligpt_root_fingerprint": fingerprint,
                    "cligpt_root_hash": root_hash(root_path),
                    "cligpt_root_basename": os.path.basename(root_path)[:512],
                    "cligpt_created_by": "cligpt",
                },
            )
            self.insert_corpus_row(db, root_path, vector_store.id, name, created_at=now)
            db.commit()
            return dict(db.execute(
                "SELECT * FROM corpora WHERE root_path = ?",
                (root_path,),
            ).fetchone())

    def find_corpus(self, root_path):
        root_path = os.path.abspath(os.path.expanduser(root_path))
        with connect() as db:
            existing = db.execute(
                "SELECT * FROM corpora WHERE root_path = ?",
                (root_path,),
            ).fetchone()
            return dict(existing) if existing else None

    def existing_file(self, corpus_id, abs_path):
        with connect() as db:
            row = db.execute(
                "SELECT * FROM files WHERE corpus_id = ? AND abs_path = ?",
                (corpus_id, abs_path),
            ).fetchone()
            return dict(row) if row else None

    def upsert_file(self, corpus_id, upload, source_path, upload_path, openai_file_id, vector_store_file_id):
        stat = os.stat(source_path)
        rel_path = os.path.relpath(source_path, self.current_root)
        source_sha = sha256_file(source_path)
        upload_sha = sha256_file(upload_path)
        now = time.time()
        with connect() as db:
            db.execute(
                """
                INSERT INTO files (
                    corpus_id, abs_path, rel_path, size_bytes, mtime_ns, sha256, kind,
                    upload_path, upload_sha256, openai_file_id, vector_store_file_id,
                    status, last_error, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', NULL, ?)
                ON CONFLICT(corpus_id, abs_path) DO UPDATE SET
                    rel_path = excluded.rel_path,
                    size_bytes = excluded.size_bytes,
                    mtime_ns = excluded.mtime_ns,
                    sha256 = excluded.sha256,
                    kind = excluded.kind,
                    upload_path = excluded.upload_path,
                    upload_sha256 = excluded.upload_sha256,
                    openai_file_id = excluded.openai_file_id,
                    vector_store_file_id = excluded.vector_store_file_id,
                    status = 'completed',
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    corpus_id,
                    source_path,
                    rel_path,
                    stat.st_size,
                    stat.st_mtime_ns,
                    source_sha,
                    upload["kind"],
                    upload_path,
                    upload_sha,
                    openai_file_id,
                    vector_store_file_id,
                    now,
                ),
            )
            db.commit()

    def mark_failed(self, corpus_id, upload, error):
        path = upload["path"]
        stat = os.stat(path)
        rel_path = os.path.relpath(path, self.current_root)
        now = time.time()
        with connect() as db:
            db.execute(
                """
                INSERT INTO files (
                    corpus_id, abs_path, rel_path, size_bytes, mtime_ns, sha256, kind,
                    status, last_error, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?)
                ON CONFLICT(corpus_id, abs_path) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    mtime_ns = excluded.mtime_ns,
                    sha256 = excluded.sha256,
                    kind = excluded.kind,
                    status = 'failed',
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    corpus_id,
                    path,
                    rel_path,
                    stat.st_size,
                    stat.st_mtime_ns,
                    sha256_file(path),
                    upload["kind"],
                    str(error),
                    now,
                ),
            )
            db.commit()

    def unchanged(self, row, path):
        stat = os.stat(path)
        return (
            row
            and row.get("status") == "completed"
            and row.get("openai_file_id")
            and row.get("vector_store_file_id")
            and row.get("size_bytes") == stat.st_size
            and row.get("mtime_ns") == stat.st_mtime_ns
        )

    def file_sync_state(self, corpus_id, upload):
        existing = self.existing_file(corpus_id, upload["path"])
        if self.unchanged(existing, upload["path"]):
            return "reused", existing
        if existing and existing.get("status") == "failed":
            return "retry", existing
        if existing:
            return "changed", existing
        return "new", None

    def remove_remote_file(self, vector_store_id, row):
        file_id = row.get("openai_file_id")
        if not file_id:
            return
        try:
            self.client.vector_stores.files.delete(
                vector_store_id=vector_store_id,
                file_id=file_id,
            )
        except Exception:
            pass
        try:
            self.client.files.delete(file_id)
        except Exception:
            pass

    def prune_missing_files(self, corpus, current_paths):
        pruned = 0
        with connect() as db:
            rows = db.execute(
                "SELECT * FROM files WHERE corpus_id = ? AND status = 'completed'",
                (corpus["id"],),
            ).fetchall()
            for row in rows:
                row = dict(row)
                if row["abs_path"] in current_paths:
                    continue
                self.remove_remote_file(corpus["vector_store_id"], row)
                db.execute(
                    """
                    UPDATE files
                    SET status = 'deleted', last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (time.time(), row["id"]),
                )
                pruned += 1
            db.commit()
        return pruned

    def status_for_uploads(self, root_path, uploads, create=False, adopt_remote=False):
        corpus = self.ensure_corpus(root_path, uploads) if create else self.find_corpus(root_path)
        self.current_root = os.path.abspath(os.path.expanduser(root_path))
        current_paths = {upload["path"] for upload in uploads}
        status_counts = {
            "total": len(uploads),
            "completed": 0,
            "new": 0,
            "changed": 0,
            "retry": 0,
            "failed": 0,
            "deleted": 0,
        }
        examples = {"new": [], "changed": [], "retry": [], "failed": [], "deleted": []}
        if corpus is None:
            if adopt_remote:
                with connect() as db:
                    corpus = self.adopt_remote_corpus(db, self.current_root, uploads)
            if corpus is not None:
                status_counts["completed"] = len(uploads)
                return {
                    "root_path": self.current_root,
                    "vector_store_id": corpus["vector_store_id"],
                    "name": corpus["name"],
                    "last_sync_at": corpus.get("last_sync_at"),
                    "complete": True,
                    "counts": status_counts,
                    "examples": examples,
                    "remote_adopted": bool(corpus.get("remote_adopted")),
                }
            status_counts["new"] = len(uploads)
            examples["new"] = [upload["path"] for upload in uploads[:5]]
            return {
                "root_path": self.current_root,
                "vector_store_id": None,
                "name": corpus_name(self.current_root),
                "last_sync_at": None,
                "complete": False,
                "counts": status_counts,
                "examples": examples,
            }
        with connect() as db:
            rows = db.execute(
                "SELECT * FROM files WHERE corpus_id = ?",
                (corpus["id"],),
            ).fetchall()
            if corpus.get("remote_adopted") and not rows:
                status_counts["completed"] = len(uploads)
                return {
                    "root_path": self.current_root,
                    "vector_store_id": corpus["vector_store_id"],
                    "name": corpus["name"],
                    "last_sync_at": corpus.get("last_sync_at"),
                    "complete": True,
                    "counts": status_counts,
                    "examples": examples,
                    "remote_adopted": True,
                }
            rows_by_path = {row["abs_path"]: dict(row) for row in rows}
            for upload in uploads:
                row = rows_by_path.get(upload["path"])
                if self.unchanged(row, upload["path"]):
                    status_counts["completed"] += 1
                elif row and row.get("status") == "failed":
                    status_counts["retry"] += 1
                    if len(examples["retry"]) < 5:
                        examples["retry"].append(upload["path"])
                elif row:
                    status_counts["changed"] += 1
                    if len(examples["changed"]) < 5:
                        examples["changed"].append(upload["path"])
                else:
                    status_counts["new"] += 1
                    if len(examples["new"]) < 5:
                        examples["new"].append(upload["path"])
            for row in rows:
                row = dict(row)
                if row["status"] == "completed" and row["abs_path"] not in current_paths:
                    status_counts["deleted"] += 1
                    if len(examples["deleted"]) < 5:
                        examples["deleted"].append(row["abs_path"])
                elif row["status"] == "failed" and row["abs_path"] in current_paths:
                    status_counts["failed"] += 1
                    if len(examples["failed"]) < 5:
                        examples["failed"].append(row["abs_path"])
        incomplete = (
            status_counts["new"]
            or status_counts["changed"]
            or status_counts["retry"]
            or status_counts["deleted"]
        )
        return {
            "root_path": self.current_root,
            "vector_store_id": corpus["vector_store_id"],
            "name": corpus["name"],
            "last_sync_at": corpus.get("last_sync_at"),
            "complete": not bool(incomplete),
            "counts": status_counts,
            "examples": examples,
            "remote_adopted": bool(corpus.get("remote_adopted")),
        }

    def context_for_directory(self, root_path, uploads):
        status = self.status_for_uploads(root_path, uploads, create=True)
        stats = {
            "reused": status["counts"]["completed"],
            "uploaded": 0,
            "failed": status["counts"]["failed"],
            "pruned": 0,
            "total": status["counts"]["total"],
            "partial": not status["complete"],
        }
        return {
            "root_path": status["root_path"],
            "vector_store_id": status["vector_store_id"],
            "name": status["name"],
            "stats": stats,
            "failures": [],
            "status": status,
            "remote_adopted": status.get("remote_adopted", False),
        }

    def write_eta(self, completed, total, started_at, force=False):
        if total <= 0 or completed <= 0:
            return
        elapsed = max(0.001, time.time() - started_at)
        rate = completed / elapsed
        remaining = max(0, total - completed)
        eta_seconds = int(remaining / rate) if rate > 0 else 0
        eta_minutes = eta_seconds // 60
        eta_remainder = eta_seconds % 60
        sys.stderr.write(
            f"[Index progress: {completed}/{total} complete; "
            f"elapsed {int(elapsed // 60)}m {int(elapsed % 60)}s; "
            f"ETA {eta_minutes}m {eta_remainder}s]\n"
        )
        sys.stderr.flush()

    def index_one_file(self, corpus, upload, temp_directory, prepare_upload_path):
        worker_client = self.new_worker_client()
        try:
            upload_path = prepare_upload_path(upload, temp_directory)
            with open(upload_path, "rb") as handle:
                uploaded = worker_client.files.create(file=handle, purpose="user_data")
            attributes = {
                "rel_path": os.path.relpath(upload["path"], self.current_root)[:512],
                "kind": upload["kind"][:512],
                "sha256": sha256_file(upload["path"]),
            }
            vector_file = worker_client.vector_stores.files.create_and_poll(
                vector_store_id=corpus["vector_store_id"],
                file_id=uploaded.id,
                attributes=attributes,
            )
            return {
                "upload": upload,
                "upload_path": upload_path,
                "openai_file_id": uploaded.id,
                "vector_store_file_id": getattr(vector_file, "id", uploaded.id),
            }
        finally:
            try:
                worker_client.close()
            except Exception:
                pass

    def sync_directory(
        self,
        root_path,
        uploads,
        prepare_upload_path,
        index_concurrency=DEFAULT_INDEX_CONCURRENCY,
    ):
        index_concurrency = max(1, int(index_concurrency or DEFAULT_INDEX_CONCURRENCY))
        corpus = self.ensure_corpus(root_path, uploads)
        self.current_root = os.path.abspath(os.path.expanduser(root_path))
        current_paths = {upload["path"] for upload in uploads}
        stats = {
            "reused": 0,
            "uploaded": 0,
            "failed": 0,
            "pruned": self.prune_missing_files(corpus, current_paths),
            "total": len(uploads),
        }
        failures = []
        total = len(uploads)
        completed = 0
        started_at = time.time()
        last_eta_at = started_at
        last_eta_completed = 0

        def maybe_eta(force=False):
            nonlocal last_eta_at, last_eta_completed
            now = time.time()
            if force and completed == last_eta_completed:
                return
            if (
                force
                or completed == total
                or completed - last_eta_completed >= INDEX_ETA_INTERVAL_FILES
                or now - last_eta_at >= INDEX_ETA_INTERVAL_SECONDS
            ):
                self.write_eta(completed, total, started_at)
                last_eta_at = now
                last_eta_completed = completed

        with tempfile.TemporaryDirectory(prefix="cligpt-vector-") as temp_directory:
            futures = {}
            with ThreadPoolExecutor(max_workers=index_concurrency) as executor:
                for index, upload in enumerate(uploads, start=1):
                    existing = self.existing_file(corpus["id"], upload["path"])
                    state = "reused" if self.unchanged(existing, upload["path"]) else "indexing"
                    sys.stderr.write(
                        f"[Indexing file {index}/{total} for search"
                        f"{' (reused)' if state == 'reused' else ''}: {upload['path']}]\n"
                    )
                    sys.stderr.flush()
                    if state == "reused":
                        stats["reused"] += 1
                        completed += 1
                        maybe_eta()
                        continue
                    if existing and existing.get("openai_file_id"):
                        self.remove_remote_file(corpus["vector_store_id"], existing)
                    upload_temp_directory = tempfile.mkdtemp(dir=temp_directory)
                    future = executor.submit(
                        self.index_one_file,
                        corpus,
                        upload,
                        upload_temp_directory,
                        prepare_upload_path,
                    )
                    futures[future] = upload

                for future in as_completed(futures):
                    upload = futures[future]
                    try:
                        result = future.result()
                        self.upsert_file(
                            corpus["id"],
                            upload,
                            upload["path"],
                            result["upload_path"],
                            result["openai_file_id"],
                            result["vector_store_file_id"],
                        )
                        stats["uploaded"] += 1
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        stats["failed"] += 1
                        failures.append({"path": upload["path"], "kind": upload["kind"], "error": str(exc)})
                        self.mark_failed(corpus["id"], upload, exc)
                        sys.stderr.write(f"[Search index skipped after failure: {upload['path']}: {exc}]\n")
                        sys.stderr.flush()
                    finally:
                        completed += 1
                        maybe_eta()
            maybe_eta(force=True)
        with connect() as db:
            db.execute(
                "UPDATE corpora SET last_sync_at = ?, remote_adopted = 0 WHERE id = ?",
                (time.time(), corpus["id"]),
            )
            db.commit()
        return {
            "root_path": self.current_root,
            "vector_store_id": corpus["vector_store_id"],
            "name": corpus["name"],
            "stats": stats,
            "failures": failures,
        }
