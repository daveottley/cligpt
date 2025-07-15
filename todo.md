# Add the following commands:
* gpt tail -n X             # Tail the context.txt log
* gpt repeat [ N ]          # Repeat the Nth last unique response
* gpt repeat 'context'      # Repeat the last response about 'context' or complain about no context
* gpt grep 'topic'          # Repeat all replys that are relevent to 'topic' (fuzzy?, AI?)

# Add response IDs        # UID to use for future reference

# Allow syntax like this: cat code.c | gpt 'Help me evaluate this C code:'
  - Allow unlimited arguments after query (or implied query) command
      e.g.  >gpt what is the meaning of life \?
      e.g.  >gpt query "what" is the meaning of '$USER='"$USER"
  - Merge all arguments after query into the single query string
  - If input is not terminal, pipe from std as per the ChatGPT log

# Rewrite cligpt in C

# Create a kind of trivia game using cligpt
  - Add logic for a daily trivia question.
  - Track player score in context

# Add configurable log output stream (default is context.txt)
  - Research how this is done in linux
  - Can I use stderr?
  - Is there a stdlog?

# Track user information via permanent_memory -> linux user information

# Implement terminal skills test
  - gpt test {shell,c,javascript,linux,etc.}
