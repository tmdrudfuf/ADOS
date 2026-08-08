# Quickstart: Validation Engine

Run validation from policy:

```powershell
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
```

A `PASS` result is valid only for `head_before == head_after`.
