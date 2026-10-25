# Verification scope

Run `python -m unittest discover -s tests -v`. Tests exercise the actual pure
implementation and archive contents, including hostile field/JSON inputs,
exact Unicode task-difference preservation, all four modes, immutable input,
stdout error privacy, deterministic archive bytes and rejected extra/symlink files.

These are implementation tests, not a formal proof of a host, a live installation,
permission enforcement, semantic fidelity, cost savings or parallel scheduling.
The four mode cases give finite mode coverage; arbitrary language meaning is not
enumerated. Native runtime availability must be checked in the current session.

Before this package was built, the corresponding native same-context protocol was
used in a project review and surfaced two useful last-mile issues: separating pure
search from search-and-open, and reducing manual host coordination. This is a
qualitative protocol example, not a test of this exact ZIP or a performance study.

A release records exact package/source hashes and actual local test counts.
An optional official-host import and real fork smoke test must be reported
separately. No import or additional native execution is implied by building ZIP.

## Recorded local run (2026-10-25 UTC)

- Python unittest: 20 tests, all passed.
- Skill creator `quick_validate.py`: passed.
- Deterministic skill ZIP: 2,515 bytes, SHA-256
  `21c1fea7ba7ad7607bf52195226506e2fec93163265c3dddb76fffa4878807d0`.
- Python wheel built without dependency downloads; installed into a new virtual
  environment and its console entrypoint ran outside the checkout successfully.
- Manifest reviewed against the official
  [Agent Plugins 1.0.0 schema](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json).
- Official host ZIP import and this package's live fork are not tested here.

Archive scan rules are leak tripwires, not a complete secret detector. Public
source and artifact contents also require review before publication.
