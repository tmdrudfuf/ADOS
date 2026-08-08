# Research: AIverse Integration

The existing `docs/integrations/aiverse.md` predates the executable Project Configuration model and uses YAML-style names that no runtime loader accepts. Spec010 should not introduce another parser or adapter format. The least ambiguous integration path is to express AIverse using the same JSON configuration shape validated by `ProjectConfig`.

Decision: keep AIverse integration as data and documentation, not runtime special casing.
