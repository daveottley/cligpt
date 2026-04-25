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

## Requireemnts

* Python
* Open AI API keys
  - (opt) verification/level to use newest models

## Installation

Execute the following commands once you have cloned the git repo onto your
local machine:

~/repo-home > pyhon3 -m venv .venv
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

## Use - Flags and Options

You invoke the helper by typing
  • python3 cligpt.py --model o3 --high "What is the meaning of life?"
  • python3 cligpt.py --low "Why is the sky blue?"
  • python3 cligpt.py +debug "Why aren't you returning an answer?"
  • python3 cligpt.py --width 100 "Explain UNIX pipes"
  • python3 cligpt.py --no-web "Explain POSIX pipes from model knowledge only"
  • python3 cligpt.py --file ./paper.pdf "Summarize this document"
  • python3 cligpt.py --file ./draft.docx "Summarize this document"
  • python3 cligpt.py --file ./main.cpp "Review this source file"
  • python3 cligpt.py --image ./photo.jpg "Describe this image"
  • python3 cligpt.py --blob ./firmware.bin "Identify what this blob may contain"
  • python3 cligpt.py --directory ./evidence "Analyze these files"

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

OpenAI may also return broader web-search source metadata that was not cited in
the final answer. cligpt logs those uncited sources to a per-response file under
`sources/`, keyed by the same response ID stored in `context.txt`, and prints an
italic Markdown link to that exact file instead of mixing them into the visible
source list.

Disable web search for a request with:
  --no-web          Use model knowledge and local context only

Examples:
  • python3 cligpt.py "What changed in Python 3.14?"
  • python3 cligpt.py --no-web "Explain how a pipe works in Unix"

In interactive mode, web search starts enabled. Prefix a message with `--no-web`
to disable it for that message and following messages. Prefix a later message
with `--web` to turn it back on.

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

Use `--directory` to recursively scan a directory and upload supported files:
  --directory ./case-files "Find the important details"

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

Directory uploads include supported documents, images, raw text/code files, and
blob reports for otherwise unsupported files. cligpt enforces a hard limit of
500 included files per request to avoid accidentally uploading a large filesystem
tree.

OpenAI's current input docs:
  - PDF files: https://platform.openai.com/docs/guides/pdf-files
  - Images: https://platform.openai.com/docs/guides/images-vision


## Output

When the tool starts, it prints a header in the following format:
  [mode: <model_name> - reasoning effort: <reasoning_effort> - web: <on|off> - width: <width>]

If the +debug flag is enabled, additional header information and reasoning tokens are printed.

Your response is printed below the debug information and should fit in one terminal window.
