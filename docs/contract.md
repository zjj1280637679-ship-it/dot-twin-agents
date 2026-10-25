# Narrow contracts

## Pure modules

`contracts.validate` owns input grammar; `request.prepare` owns the single
relationship from validated task difference to a native full-history request.
`cli` owns only JSON transport. `scripts/build_plugin.py` owns the upload allowlist.
These pure modules receive no host/Kernel object, conversation store or credentials.
Optional `lifecycle` owns explicit local correlation and a caller-supplied narrow
`HostPort`, described in [the lifecycle contract](lifecycle.md). It does not receive
a conversation store or credentials and supplies no native runtime implementation.

Input: `{task_name: string, delta: string, mode?: enum}`.
Output: `{task_name: same string, message: fixed concise mode prefix + exact delta,
fork_turns: "all"}`. Unknown fields, duplicate JSON keys and invalid Unicode fail.
The task name uses lowercase ASCII letters/digits/underscores, starts with a letter,
and has at most 64 characters for portable naming. This does not override a host's
stricter limits; its admission decision remains authoritative.

The helper deliberately does not inspect the delta for instructions or secrets:
it is explicit task data. The caller still owns its content, recipient and scope.
It neither serializes nor injects history, and never calls a model or native tool.
The CLI emits no task text on failure. It is a trusted-local convenience, not a
public untrusted-input server or a security sandbox.

## Host relations

1. Verify native full-context fork support and capacity.
2. Submit the prepared request once under the current authorization.
3. Retain the actual returned child identity. Unknown admission outcomes require
   reconciliation before another submission; a generated task name is not proof
   that a child exists.
4. Parent and child work asynchronously. Material findings include evidence;
   parent checks the actual result before treating an external action as done.
5. Continue an existing idle child through the supported resume primitive.

The optional journal records the intent before its one-shot port call, binds the
actual receipt and collects later envelopes. The trusted host adapter owns the
native call and authoritative identity normalization. The journal does not verify
full-context capability or context inheritance, implement resume, or automatically
retry unknown outcomes. The existing pure builder and CLI remain unchanged.

Platform limits and approvals are inherited, not expanded. Internal/private host
rules are never bundled. A full-history fork inherits currently available history,
including its omissions and possible mistakes. Use a fresh observer when the
shared premise is under challenge.

## Versioning

The request contract is version 1 and package version 0.1.0. Incompatible future
host schemas need an explicit adapter version, not best-effort fallback to a new
backend or pretending that a summary is a full fork.
