# config.py

import os

# AI configuration
MODEL = "gpt-5"
FAST_MODEL = "gpt-4o-realtime-preview"
STREAM = True
N = 1
PRESENCE_PENALTY = 0

# File paths
SYSTEM_MESSAGE_FILE = "system_message.txt"
CONTEXT_FILE = "context.txt"
PERMANENT_MEMORY_FILE = "permanent_memory.json"  # permanent memories stored here

# Token limit for assembling context
MAX_CONTEXT_TOKENS = 100_000

# Delimiter for context blocks
DELIMITER = "\n" + "-" * 15 + "\n"

