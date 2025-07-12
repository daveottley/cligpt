# cligpt
GPT CLI Tool Help & Usage
-------------------------
This tool supports both one-off queries and an interactive REPL. It accepts reasoning effort flags to
customize processing, and a debug flag for extra output. If no subcommand is provided,
arguments are treated as a query by default.

When the tool starts, it prints a header in the following format:
  [mode: <model_name> - reasoning effort: <reasoning_effort>]

If the +debug flag is enabled, additional header information and reasoning tokens are printed.

Flags:
  --high (-h)       Set reasoning effort to high
  --medium (-m)     Set reasoning effort to medium (default)
  --low (-l)        Set reasoning effort to low
  +debug (+d)       Enable debug mode (prints full header & reasoning tokens)
  -debug (-d)       Disable debug mode

Please refer to this file for setup and usage instructions.

This program aims to replace or improve upon the GitHub Copilot CLI. You can
ask questions to this program using the bash function `gpt.sh` included here.
Make sure to use proper bash quoting and syntax.

This program uses the latest reasoning model of OpenAI to provide the best
programming and CLI guidance possible.

**CAUTION:** This program may consume high rates of currency depending on token
usage!

## Installation

You must set your `OPENAI_API_KEY` environment variable before this program
will execute.

Register the `gpt` function found in `gpt.sh` with your shell. You can source
the file at the command line or preferably in your shell's `.rc` file (e.g.,
`.bashrc`):

```bash
source ./gpt.sh
```

Once you have access to this function, your use of this program will be much
easier.

## Usage

Usage Examples:
  • gpt_cli.py --high "inquiry text"
  • gpt_cli.py --low
  • gpt_cli.py +debug
  • gpt_cli.py +debug --medium
  • gpt_cli.py --model gpt-4 "use a specific model"
