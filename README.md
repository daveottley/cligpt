# cligpt

This program aims to replace or improve upon GitHub Copilot CLI. You simply ask questions to
this program using the bash function gpt.sh included here. Do not use any punctionation!!

This program uses the latest reasoning model of OpenAI to provide the best programming and
CLI guidance possible. 

CAUTION: THIS PROGRAM MAY CONSUME HIGH RATES OF CURRENCY DEPENDING ON TOKEN USAGE!!!

## Installation

You must set your OPENAI_API_KEY environment variable before this program will execute

Register the gpt function found in gpt.sh with your shell. You can source the file at the command line
or preferrably in your shell's .rc file (e.g. .bashrc)

source ./gpt.sh

Once you have access to this function your use of this program will be much easier.

## Usage

This program has the following commands. This list may not be complete and you can ask gpt itself for
its configuration.

gpt expalin # Designed to improve upon gh copilot explain
gpt suggest # Designed to improve upon gh copilot suggest
gpt search  # Search the internet for a topic. Ensures latest information
gpt rewrite ($language) goal: code
            # Rewrite code according to a specified goal
            # This will include couching foratting and explanations
            # For pure code output include the $language name
            # goal: must end in a colon
            # code "$(cat code.txt)" recommended

example: gpt rewrite python to dynamically detect user environment "$(cat cligpt.py)" > response.txt

explanation: This will take your input file "cligpt.py" and rewrite it in valid python
to dynamically detect the user environment. This output will have no formatting or explanation
so it is suggested first to querry gpt without an explicit language to read any comments 
about edits that it may have. Once you are satisfied with the coding style of the agent,
you can ask it for code by specifying the language. Since code can get rather lengthy for the
terminal, it is suggested that you pipe your response into an output file which you can
inspect in your terminal.
