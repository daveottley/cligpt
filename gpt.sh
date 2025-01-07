function gpt() {
  (
    setopt localoptions noglob
    set -f # Turn off filename globbing
    
    cd /home/daveottley/scripts/Learning-Python/cligpt || {
      echo "Failed to navigate to /home/daveottley/scripts/Learning-Python/cligpt"
      return 1
    }

    # Activate virtual environment
    source /home/daveottley/scripts/Learning-Python/.venv/bin/activate
    
    # Run the python script
    python3 /home/daveottley/scripts/Learning-Python/cligpt/cligpt.py "$@"
    
    # Deactivate the virtual environment
    deactivate

    # Re-enable globbing afterward
    set +f 
  )
}

