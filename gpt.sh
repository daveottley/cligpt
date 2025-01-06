function gpt() {
  setopt localoptions noglob
  set -f # Turn off filename globbing
  source /home/daveottley/scripts/python/cligpt/.venv/bin/activate
  python3 /home/daveottley/scripts/python/cligpt/cligpt.py "$@"
  deactivate
  # Re-enable globbing afterward
  set +f 
}

