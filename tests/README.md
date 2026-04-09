# Test Layout

`tests/` is the canonical pytest suite for this workspace.

The root-level `tests_tmp_*` directories are generated artifacts from prior test
runs and should not be treated as source tests. Pytest is configured to ignore
those folders, along with `__pycache__` directories.

When adding or updating tests:

- Keep executable test modules under `tests/`.
- Use pytest fixtures such as `tmp_path` for temporary files.
- Avoid writing generated artifacts into new root-level `tests_tmp_*` folders.
