#!/usr/bin/env bash

function gpt() {
  (
    # Ensure local shell options so that filename globbing is disabled
    setopt localoptions noglob
    set -f  # Disable globbing
    
    # Change directory to the project's location (adjust path as needed)
    cd ~/scripts/Learning-Python/cligpt || {
      echo "Failed to navigate to ~/scripts/Learning-Python/cligpt"
      return 1
    }

    # Activate the virtual environment
    source ~/scripts/Learning-Python/cligpt/.venv/bin/activate
    
    # Execute the Python CLI tool with any passed arguments
    python3 ~/scripts/Learning-Python/cligpt/cligpt.py "$@"
    
    # Deactivate the virtual environment
    deactivate

    # Re-enable globbing after execution
    set +f 
  )
}

