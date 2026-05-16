# cligpt - A minimal, smart terminal helper

## Why use cligpt?

cligpt is a minimal, smart terminal helper. It functions on a raw TTY and has
a standard UNIX UI including pipes, arguments, options, and more!

This helper tries to keep the responses digestable and readable within a standard
80x80 terminal window.

Using cligpt can avoid some of the tracking that OpenAI does with its consumer
accounts.

This tool supports both one-off queries and an interactive REPL. It accepts reasoning effort flags to
customize processing, and a debug flag for extra output. If no subcommand is provided,
arguments are treated as a query by default.

Web search is enabled by default through OpenAI's hosted web search tool. This lets
cligpt answer questions about current, fast-changing, or source-sensitive topics
without maintaining a local scraper dependency.

**CAUTION:** This program may consume high rates of REAL-WORLD-MONEY depending on token
usage!

## Requirements

* Python
* Open AI API keys
  - (opt) verification/level to use newest models

## Installation

Execute the following commands once you have cloned the git repo onto your
local machine:

~/repo-home > python3 -m venv .venv
~/repo-home > source .venv/bin/activate
pip install -r requirements.txt

You must set your `OPENAI_API_KEY` environment variable before this program
will execute.

You *may* also need to set your `OPENAI_PROJECT` env var depending on your
org setup.

You will need to export your repo top level directory as `GPT_HOME`.


Register the `gpt` function found in `gpt.sh` with your shell. You can source
the file at the command line or preferably in your shell's `.rc` file (e.g.,
`.bashrc`):

```bash
source ./gpt.sh
```

Once you have access to this function, your use of this program will be much
easier.

After installation, run:

```bash
gpt doctor
```

`doctor` is read-only. It verifies Python packages, `OPENAI_API_KEY`, and
optional third-party document/OCR tools such as LibreOffice, ocrmypdf,
Tesseract, Poppler, Ghostscript, `file`, and binwalk. Missing tools do not stop
basic chat, but they reduce file, directory, OCR, and blob-analysis capability.

Use:

```bash
gpt update
```

to pull the latest git changes, update the Python virtual environment from
`requirements.txt`, and then run the same diagnostic checks. If system tools are
missing, `update` prints the package-manager command to install them. Use:

```bash
gpt update --system
```

to also install missing system tools with the detected package manager
(`pacman`, `apt`, `dnf`, or `brew`). This may prompt for sudo.

Use a dry run to verify update and install commands without changing the system:

```bash
gpt update --system --dry-run
```

## Use - Flags and Options

You invoke the helper by typing
  • python3 cligpt.py --model o3 --high "What is the meaning of life?"
  • python3 cligpt.py --low "Why is the sky blue?"
  • python3 cligpt.py +debug "Why aren't you returning an answer?"
  • python3 cligpt.py --width 100 "Explain UNIX pipes"
  • python3 cligpt.py --no-context --no-web "Explain POSIX pipes from model knowledge only"
  • python3 cligpt.py --raw "What is the smallest request?"
  • python3 cligpt.py --file ./paper.pdf "Summarize this document"
  • python3 cligpt.py --file ./draft.docx "Summarize this document"
  • python3 cligpt.py --file ./main.cpp "Review this source file"
  • python3 cligpt.py --image ./photo.jpg "Describe this image"
  • python3 cligpt.py --blob ./firmware.bin "Identify what this blob may contain"
  • python3 cligpt.py --directory ./evidence "Analyze these files"

### Permanent Memory

Permanent memories are stored locally in `permanent_memory.json` and are sent as
context on normal queries. Use `--no-context` when you do not want recent
history or permanent memories included in a request.

Save memories in `key: value` format:

```bash
gpt remember "name: Dave"
gpt remember "editor: nvim"
```

List memories and note their stable IDs:

```bash
gpt view-memory
gpt memories
```

Replace a memory by ID:

```bash
gpt edit-memory 3 "editor: helix"
gpt update-memory 3 "editor: helix"
```

Delete a memory by ID:

```bash
gpt forget 3
gpt forget-memory 3
```

Memory IDs are stable. Deleting memory `3` does not renumber the remaining
memories, and the next new memory receives a new ID.

In interactive mode, use `--remember <key:value>`, `:view-memory`,
`:edit-memory <id> <key:value>`, and `:forget-memory <id>`.

### Model

You can use a specific model by using the following flag:
  --model your-desired-model-name

Make sure that the model name matches the name published by OpenAI exactly!

### Reasoning Effort

You can use the following flags to tell the model how hard to think about your query
  --high (-h)       Set reasoning effort to high
  --medium (-m)     Set reasoning effort to medium (default)
  --low (-l)        Set reasoning effort to low

### Debug Information

You can tell the program how much debug information you want to see.
  +debug (+d)       Enable debug mode (prints full header & reasoning tokens)
  -debug (-d)       Disable debug mode

### Response Width

By default, responses are formatted to the current terminal width minus one
column. You can override this with:
  --width 100       Format response lines to at most 100 columns

### Web Search

Web search is enabled by default. The model can decide when to search and should
use search for current, fast-changing, or source-sensitive facts. If search is
used, cligpt asks the model to make source URLs visible in the answer and prints
only final-answer citations after the streamed response.

### Prompt Cache

OpenAI prompt caching is automatic for supported models. cligpt optimizes for
cache hits by placing reusable directory/search context before the current user
question, sending a stable `prompt_cache_key`, and using extended cache
retention for GPT-5-family models by default. It also keeps volatile machine
diagnostics such as `neofetch` out of the default system prompt and inserts a
stable cache anchor before changing context history, because OpenAI cache hits
require an exact prefix match of at least 1,024 tokens.

The default prompt includes only a compact stable environment block:

```text
# ENV
- os: CachyOS Linux 6.x
- shell: zsh
- editor: nvim
- package_manager: pacman
- aur_helper: paru
```

When machine-specific detail is actually needed, the model can call the
read-only local `get_system_profile` function tool instead of receiving raw
`neofetch` output in every prompt.

Use these flags when you want explicit control:

```text
--prompt-cache-key KEY                 Override the stable routing key
--prompt-cache-retention auto          Default; use 24h for GPT-5-family models
--prompt-cache-retention in_memory     Use normal short-lived cache retention
--prompt-cache-retention 24h           Request extended cache retention
--prompt-cache-retention off           Omit prompt_cache_retention
```

Environment defaults:

```text
CLIGPT_PROMPT_CACHE_KEY=...
CLIGPT_PROMPT_CACHE_RETENTION=auto
CLIGPT_PROMPT_CACHE_MIN_STABLE_WORDS=1152
CLIGPT_INCLUDE_NEOFETCH=0
```

The usage footer reports `cached_input`, `cache_hit`, `prompt_cache_key`, and
`prompt_cache_retention` so repeated long-context requests can be checked
directly. Official docs: https://platform.openai.com/docs/guides/prompt-caching/prompt-caching

OpenAI may also return broader web-search source metadata that was not cited in
the final answer. cligpt logs those uncited sources to a per-response file under
`sources/`, keyed by the same response ID stored in `context.txt`, and prints an
italic Markdown link to that exact file instead of mixing them into the visible
source list.

Disable web search for a request with:
  --no-web          Use model knowledge and local context only
  --no-context      Do not send recent context.txt history or permanent memories
  --full-context    Send selected context.txt blocks with usage stats and sources
  --raw             Send only the typed prompt, with no system/context/tools/web/files

Examples:
  • python3 cligpt.py "What changed in Python 3.14?"
  • python3 cligpt.py --no-context --no-web "Explain how a pipe works in Unix"
  • python3 cligpt.py --raw "What is the meaning of life?"

In interactive mode, web search and context history start enabled. Prefix a
message with `--no-web` to disable web search for that message and following
messages. Prefix a later message with `--web` to turn it back on. Use
`--no-context` to turn recent-history/permanent-memory context off, and
`--context` to re-enable it. Use `--full-context` when the model should see
usage stats, source footers, and other metadata from selected context blocks.
For minimum-token experiments, use `--raw`; it sends only the typed prompt and
cannot be combined with files or directories.

### File and Image Uploads

Use `--file` to include a supported file for a request:
  --file ./paper.pdf "Summarize this"

LibreOffice-editable documents are converted to temporary PDFs before upload:
  --file ./proposal.docx "Summarize this"
  --file ./spreadsheet.xlsx "Find anomalies"
  --file ./notes.odt "Extract action items"

Raw text and code files are included directly as text:
  --file ./notes.txt "Summarize this"
  --file ./main.cpp "Review this code"

Use `--blob` for arbitrary binary files. cligpt does not upload raw binary bytes
directly to the model. It builds a text report with file metadata, size, hashes,
MIME guess, `file(1)` output when available, first/last bytes as hex, printable
strings, and `binwalk` output when available:
  --blob ./firmware.bin "Analyze this binary"

Use `--image` to upload an image for a request:
  --image ./photo.jpg "What is in this image?"

Direct `--image` vision attachments are capped at 25 images per request. For
larger image sets, use `--directory` so images are indexed once and reused.

Use `--directory` to recursively scan a directory with a local OCR/text cache,
search that cache before the model request, and send only selected snippets:
  --directory ./case-files "Find the important details"

This local preflight search does not upload the whole directory to OpenAI and
does not give the model direct access to your filesystem. It is the default
because it is cheaper and faster for questions that can be answered from OCR,
text layers, filenames, and metadata.

Use `--remote-search` with `--directory` when you want OpenAI file_search vector
stores instead of local preflight search:
  --directory ./case-files --remote-search "Find the important details"

In remote-search mode, if the directory index is incomplete, query mode warns
before contacting the model. The default interactive choice is to proceed using
the files already available in the vector store while a non-blocking background
sync continues. Use `--wait-index` to force cligpt to finish syncing before the
query, or `--allow-partial-index` to proceed without prompting.

You can manage directory indexes directly:
  sync-directory ./case-files --index-concurrency 8
  index-status ./case-files
  index-list
  index-duplicates
  index-expire vs_... --days 7
  index-delete vs_...

`sync-directory` manages the remote OpenAI file_search index and is
resumable/idempotent: unchanged files reuse existing OpenAI
file IDs, changed files are re-indexed, failed files are retried on the next
sync, and deleted local files are pruned from the vector store. Syncs can be
interrupted and started again later.

Accepted file types:
  - Documents: PDF (`.pdf`)
  - LibreOffice-editable documents: common Writer, Calc, Impress, Draw, and
    Microsoft Office formats such as `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`,
    `.pptx`, `.odt`, `.ods`, `.odp`, `.odg`, `.rtf`, and related templates
  - Raw text/code: `.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.xml`, `.cpp`,
    `.py`, `.js`, `.rs`, `.go`, shell scripts, config files, and similar
  - Images: PNG, JPEG/JPG, WEBP, and non-animated GIF
  - Binary blobs: any file passed with `--blob`, plus otherwise unsupported
    files discovered by `--directory`

Directory searches include supported documents, raw text/code files, and blob
reports for otherwise unsupported files. Images found in directories are not
directly re-uploaded on every query. Document-like images are OCR scanned and
locally searchable like PDFs. In local preflight mode, non-document images are
indexed by filename, metadata, and OCR preview only; use `--remote-search` or
direct `--image` uploads for broad visual questions such as finding a specific
object in a photo set. cligpt enforces a hard limit of 5000 included directory
files per request to avoid accidentally indexing a large filesystem tree.

For PDFs in directory searches, cligpt prefers local text extraction over
uploading heavy PDFs. It tries `pdftotext` first, then `ocrmypdf`, then a
Poppler + Tesseract OCR fallback when needed. This reduces network use and gives
cleaner text for scanned leases and other image-heavy documents. In
`--remote-search` mode, if text extraction fails, cligpt falls back to the
compressed-PDF upload path.

Each directory gets a durable local corpus entry in `.cligpt/vector_stores.db`
and a matching OpenAI vector store named like `cligpt:<directory>:<hash>`.
Unchanged files reuse their existing OpenAI file IDs. Changed files are
re-uploaded and re-indexed automatically on the next run based on size, mtime,
and SHA256 metadata. Files deleted locally are removed from the vector store on
the next sync. Files that still fail after retries are skipped and reported to
the model.

Vector stores are also tagged with portable OpenAI metadata derived from a
directory identity fingerprint. When the same OpenAI API key indexes the same
network directory from another machine, cligpt checks existing OpenAI vector
stores before creating a new one. If it finds a matching store, it adopts that
remote index locally and avoids a duplicate vector store. Adopted remote indexes
do not have local per-file state on the new machine until a full sync is run, so
normal queries reuse the existing vector store while explicit `sync-directory`
can rebuild local per-file tracking if needed.

New cligpt vector stores default to a 7-day expiration policy anchored to
`last_active_at`. Use `index-expire vs_... --days N` to adjust retention,
`index-list` to inspect storage usage, and `index-delete vs_...` to remove a
store immediately. Deleting vector stores stops future vector-store storage
charges for those stores.

The `.cligpt/` directory is ignored by Git and should remain local-only. It can
contain local search cache data, vector-store metadata, and sync logs tied to
private business documents. Do not commit it.

Large PDFs over 10 MB are compressed with Ghostscript before upload when
possible, with a stronger second pass if the compressed PDF is still over 5 MB.
This is especially useful for scanned leases and other image-heavy PDFs.

OpenAI's current input docs:
  - PDF files: https://platform.openai.com/docs/guides/pdf-files
  - File search: https://platform.openai.com/docs/guides/tools-file-search/
  - Images: https://platform.openai.com/docs/guides/images-vision

### Model Context

cligpt uses model capability profiles to advertise and enforce basic limits. The
profiles are refreshed from OpenAI's model comparison documentation and cached
for 3 days in `.cligpt/openai_model_limits.json`; checked-in fallback values are
used if the docs cannot be reached. The recent-history budget is derived from
the selected model's safe input budget, not from the output-token cap. cligpt
uses 20% of safe input for recent conversation history, with a 4,000-token floor
and 120,000-token ceiling.

Large directories should use `--directory`, which locally selects relevant file
chunks before the request instead of forcing every document into the prompt
context. Use `--remote-search` when local snippets are not enough and you want
OpenAI file_search to retrieve from a remote vector store.

As a rough guide, direct file attachment is best for a few files. A directory of
hundreds of leases, scans, spreadsheets, or photos should be indexed and searched.
The startup header shows the selected model, context window, safe input budget,
max output tokens, and whether files are being sent directly or through
`file_search`.

Directory indexing defaults to 8 concurrent file preparations/uploads. Increase
or reduce this with `--index-concurrency N` depending on local CPU, network
quality, and OpenAI API reliability. Sync progress prints `file #/#` and periodic
elapsed/ETA messages so long runs can be evaluated or aborted.

For current operating reports such as rent rolls, `--directory` also sends a
compact directory manifest. Paths containing archive/disposed markers such as
`Old`, `Archive`, `Former`, `Sold`, `Disposed`, `Closed`, `Historical`, or
`Inactive` are labeled as historical and should be excluded from current rent
rolls unless the prompt explicitly asks for historical/all/former/sold data.
The same classification metadata is prepended to text, blob, PDF OCR, and
Office-converted text indexed for file search so retrieved chunks carry their
directory status with them.

Images inside `--directory` are indexed instead of re-uploaded on every query.
Document-like images such as photographed leases are OCR scanned with
Tesseract and stored as searchable text. Non-document images are indexed as
reusable vision-caption/metadata reports, which supports broad searches such as
"which filename is most likely to contain Waldo" without directly attaching
every image to the request. Direct OpenAI vision attachments are capped at 25
images; use directory sync for larger image sets.


## Output

When the tool starts, it prints a header in the following format:
  [<model_name> - <reasoning_effort> - web:<on|off> - files:<direct|file_search> - context:<tokens> - safe input:<tokens> - max output:<tokens> - width:<width>]

If the +debug flag is enabled, additional header information and reasoning tokens are printed.

Your response is printed below the debug information and should fit in one terminal window.

Terminal rendering defaults to `--style auto`, which uses a Codex-like Rich
Markdown panel when stdout is a terminal and plain text when stdout is piped.
Use:

```text
--style codex      Render assistant output in Markdown panels
--style compact    Render Markdown without full panels
--style plain      Print plain streamed text
--no-color         Disable ANSI color
```

Long-running streamed responses print stderr heartbeats while the OpenAI stream
is silent, so a heavy directory/file-search request does not look dead:

```text
--heartbeat-seconds N   Waiting message interval, default 30
--idle-timeout N        Warn after N seconds without stream events, default 600
--request-timeout N     OpenAI request timeout in seconds, default 3600
```

With `+debug`, cligpt prints a request-input estimate that breaks down the
assembled payload. Stream event counts are reported later in `Usage Detail`
instead of being printed live, so text delta events do not flood the terminal.
Ctrl-C aborts the local wait cleanly and does not roll back the reusable
directory index.

Every response renders a compact Markdown usage section at the bottom of the
same assistant panel and stores the full visible answer, including usage and
source footers, in `context.txt`. When cligpt later reuses recent history as
model input, it strips that stored log down to conversation text only, so usage
and source metadata remain on disk without being resent as prompt context:

```text
### Token Usage

`usage_cost` input:12,345; output:1,234; reasoning:567; total:13,579; estimated_cost:$0.0967  
`prompt_cache` cached_input:1,000; cache_hit:8.1%; prompt_cache_key:cligp...def456; prompt_cache_retention:24h  
`file_search_direct_uploads` file_search:1 call(s), 50 result(s); web_search:0 call(s); direct_uploads:0 file(s), 0 B; local_tools:get_system_profile:1 call(s)  
`directory` reused:212; uploaded:0; failed:0; pruned:0; remote_adopted:1; background_syncs:0  
`local_search` reused:300; indexed:2; failed:0; selected:18
```

This is API-reported token usage plus an estimated per-response cost, tool
calls, local search counters, and sync/upload counters. Cached input is shown
when the API reports `usage.input_tokens_details.cached_tokens`. Reasoning
tokens are included in output-token billing when reported. The estimate includes
known model token rates plus per-call file_search and web_search rates where
available; it does not include recurring vector-store storage charges, and it is
not a perfect real-time billing statement because storage charges and dashboard
aggregation can lag.
