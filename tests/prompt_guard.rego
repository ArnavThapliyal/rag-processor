package rag.prompt_guard

# Default allow unless a violation is found
default is_safe = true

# Block if the prompt contains known injection phrases
is_safe = false {
    banned_words := [
        "ignore all previous instructions", 
        "system prompt", 
        "bypass guardrails"
    ]
    some i
    contains(lower(input.prompt), banned_words[i])
}