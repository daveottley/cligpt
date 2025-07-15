#!/usr/bin/env bash

function gpt() {
  (
    # Ensure local shell options so that filename globbing is disabled
    setopt localoptions noglob
    set -f  # Disable globbing
    
    # Change directory to the project's location (adjust path as needed)
    cd $GPT_HOME || {
      echo "Failed to navigate to $GPT_HOME"
      return 1
    }

    # Activate the virtual environment
    source $GPT_HOME/.venv/bin/activate
    
    # Execute the Python CLI tool with any passed arguments
    python3 $GPT_HOME/cligpt.py "$@"
    
    # Deactivate the virtual environment
    deactivate

    # Re-enable globbing after execution
    set +f 
  )
}

