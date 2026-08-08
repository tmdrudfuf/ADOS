# Quickstart: Claude Review Engine

Run an independent review using the configured reviewer command:

```powershell
python -m ados review run --policy review-policy.json --candidate-sha <sha> --base-sha <sha> --scope "Spec005"
```

The result is valid only for `reviewed_sha`.
