- [ ] Add gpt -i - functionality (read from stdin)
- [ ] Add something to do during LONG wait times (2-5 mins experienced)
- [ ] Add the following commands:
    - [ ] gpt tail -n X             # Tail the context.txt log
    - [ ] gpt repeat [ N ]          # Repeat the Nth last unique response
    - [ ] gpt repeat 'context'      # Repeat the last response about 'context' or complain about no context
    - [ ] gpt grep 'topic'          # Repeat all replys that are relevent to 'topic' (fuzzy?, AI?)

- [ ] Add response IDs        # UID to use for future reference

- [ ] Add --fast flag that moves to the realtime model (implement with WebSockets)

- [ ] Add --global flag that alters a global config file with sane defaults.

- [ ] Add --stream flag that enables streaming (default --stream=NO. Set in global config file)

- [ ] Add tests to the program

- [ ] Play around with structured responses and piping to jc

- [ ] Play around with all models on OpenAI API Playground. Check models here when done.
    - [ ] gpt-4o
    - [ ] o3
        - [ ] Structured Outputs
        - [ ] File Inputs
        - [ ] Function calling
        - [ ] Deep Research
        - [ ] Tool Use
    - [ ] o4-mini
    - [ ] gpt-4o-realtime-preview (WebSockets)

- [ ] Add background processing for non-realtime queries
    - [ ] Considering  using a switch such as -b
    - [ ] Add a switch to check jobs and print output from jobs
    - [ ] Try to mimic a 'tab' system where the user can split their train of though

- [ ] Add cloud save feature

- [ ] Allow syntax like this: cat code.c | gpt 'Help me evaluate this C code:'
    - [ ] Allow unlimited arguments after query (or implied query) command
      > e.g. gpt what is the meaning of life \?
      > e.g. gpt query "what" is the meaning of '$USER='"$USER"
    - [ ] Merge all arguments after query into the single query string
    - [ ] If input is not terminal, pipe from std as per the ChatGPT log

- [ ] Rewrite cligpt in C

- [ ] Create a kind of trivia game using cligpt
    - [ ] Add logic for a daily trivia question.
    - [ ] Track player score in context

- [ ] Add configurable log output stream (default is context.txt)
    - [ ] Research how this is done in linux
    - [ ] Can I use stderr?
    - [ ] Is there a stdlog?

- [ ] Track user information via permanent_memory -> linux user information

- [ ] Implement terminal skills test
    - [ ] gpt test {shell,c,javascript,linux,etc.}

- [ ] Add background processing for non-realtime queries
    - [ ] Considering  using a switch such as -b
    - [ ] Add a switch to check jobs and print output from jobs
    - [ ] Try to mimic a 'tab' system where the user can split their train of though
