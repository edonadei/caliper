---
name: slugify
description: Convert a title into a URL slug. Use when asked to slugify a title or make a URL slug.
---

# Slugify

Turn a title into a URL slug with these rules, in order:

1. Trim leading and trailing whitespace.
2. Lowercase everything.
3. Remove any character that is not a letter, digit, space, or hyphen.
4. Replace each run of spaces with a single hyphen.

Write **only** the resulting slug to the file path you are given — no quotes,
no surrounding text, no trailing newline beyond the slug itself.
