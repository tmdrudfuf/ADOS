# Plan — Guardian Engine

Create a documentation-first guardian contract before runtime code. Each workflow transition declares required guardians; guards evaluate supplied evidence and fail before side effects.

## Design decisions

- Keep guardians small and composable.
- Use stable reason codes for automation/reporting.
- Treat allowed local untracked artifacts as project configuration, not hardcoded global exceptions.
- Exact HEAD is checked when review approval is consumed, not only when review finishes.
- Guard results are evidence, never authorization by themselves.

## Validation

Cross-check Guardian requirements against Constitution authority and the canonical workflow state machine. Ensure every destructive/publication transition has an appropriate guard and no guardian silently mutates state.