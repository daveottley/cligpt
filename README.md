# cligpt

This program aims to replace or improve upon GitHub Copilot CLI. You simply ask questions to
this program using the bash function gpt.sh included here. Do not use any punctionation!!

cligpt will ALWAYS return a command for you to try out at the shell.

You must set your OPENAI_API_KEY environment variable before this program will execute

## Installation

Register the gpt function found in gpt.sh with your shell. You can source the file at the command line
or preferrably in your shell's .rc file (e.g. .bashrc)

source ./gpt.sh

Once you have access to this function your use of this program will be much easier.

## Usage

gpt how do I use you
