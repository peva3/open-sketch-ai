"""Bundled SketchUp agent guide (package data).

These are byte-for-byte copies of ``docs/agents/guide/*.md`` so the agent can
assemble its system prompt outside a repo checkout (installed wheel or frozen
binary). The canonical source is ``docs/agents/guide/``; refresh the copies with
``python -m supex_driver.agent.guide.sync`` from the repo root, and the parity
test ``test_guide_bundle_matches_repo`` guards against drift.
"""
