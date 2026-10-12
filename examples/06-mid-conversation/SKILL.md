---
name: repro-first
description: Bug-fix mode that reproduces a bug in a failing test before changing any code. Entered with /repro-first.
disable-model-invocation: true
---

# Repro first

While this mode is on, fix every bug in this order:

1. Add a test that reproduces the bug, next to the existing tests.
2. Run it and see it fail. Show the failing output.
3. Only then change the code.
4. Run the whole suite again and show it passing.

Never edit the code under test before step 2 has failed. If you can't make the
test fail, say so and stop instead of fixing anything.
