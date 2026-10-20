---
name: log-prompts
description: Curate the key prompts of this build into prompts.md for the competition submission ("show how you worked with the model"). Use when the user says "log this", "update prompts.md", "record that prompt", or after finishing a milestone (a pipeline stage working, a design decision made, an approach abandoned).
---

# Log key prompts into prompts.md

Raw capture is already automatic:

- `ai-log/prompt_log.md`: every prompt, added by the `UserPromptSubmit` hook.
- `ai-log/transcripts/<session>.md` / `.jsonl`: full session transcript, exported by the `Stop` hook.

This skill picks out the **key** prompts and writes them to `prompts.md`.

## Steps

1. Read `prompts.md` and note the last entry number.
2. Read `ai-log/prompt_log.md` for prompts after that entry.
3. Keep only key prompts: ones that set direction, made or changed a design decision, corrected the
   AI, debugged a real failure, or abandoned an approach. Skip trivial ones ("yes", "continue", "run it").
4. Append one entry per key prompt using the template below, continuing the numbering.
5. Tell the user in one line how many entries were added.

## Entry template (use exactly these three fields, nothing else)

```markdown
### N. <short title>

**Prompt**
> <verbatim prompt; trim only if very long, marking cuts with […]>

**Changes by AI:** <what the AI changed or produced: files, code, answer; 1–3 lines>

**Outcome:** <result for the project; 1–2 lines>
```

## Rules

- Never invent prompts. Every entry must exist in `ai-log/prompt_log.md` or the conversation.
- Never rewrite earlier entries except to fix a factual error.
- No extra sections, tables or commentary in `prompts.md`.
