#!/usr/bin/env bash

function gpt() {
  (
    setopt localoptions noglob
    set -f # Turn off filename globbing
    
    cd ~/scripts/Learning-Python/cligpt || {
      echo "Failed to navigate to ~/scripts/Learning-Python/cligpt"
      return 1
    }

    # Activate virtual environment
    source ~/scripts/Learning-Python/cligpt/.venv/bin/activate
    
    # Run the python script
    python3 ~/scripts/Learning-Python/cligpt/cligpt.py "$@"
    
    # Deactivate the virtual environment
    deactivate

    # Re-enable globbing afterward
    set +f 
  )
}

