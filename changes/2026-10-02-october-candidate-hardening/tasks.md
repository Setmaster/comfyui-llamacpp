# Tasks

- [x] Review and baseline, ENG-78.
- [x] Launch option normalization and redaction, ENG-79.
- [x] Transactional replacement endpoint check, ENG-80.
- [x] Text-only Gemma recognition, ENG-81.
- [x] Absolute HTTP deadlines, ENG-82.
- [x] Exact direct discovery identity, ENG-83.
- [x] Preflight and async Stop terminal feedback, ENG-84.
- [x] Stable/candidate documentation, ENG-85.
- [x] Locked CI and isolated distribution gates, ENG-86.
- [x] Exact public router IDs through Comfy validation, ENG-93.
- [ ] Integrated current-runtime acceptance and dev handoff, ENG-87.

Verification: `.venv/bin/python -m pytest -q`; `npm test`; `.venv/bin/ruff check .`; `.venv/bin/ruff format --check .`; `uv sync --locked --extra dev`; `uv build`; `uvx twine check dist/*`; `.venv/bin/python tests/check_distribution.py dist`; extracted artifact checks; `git diff --check`; full diff review; real Windows ComfyUI and both browser renderers; real generation and release; pushed CI. Exact results go in docs/validation-2026-10.md.
