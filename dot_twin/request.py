"""One pure relationship: validated difference -> native full-context request."""
from .contracts import validate

INTENTS = {
    "open": "Preserve the root goal and evidence; independently reconsider strategy and completion criteria.",
    "directed": "Continue the specified task using the inherited context.",
    "verify": "Independently verify the claim against evidence; preserve disagreements and limits.",
    "consolidate": "Consolidate the specified knowledge without replacing historical evidence.",
}
BOUNDARY = "Use inherited authorization; coordinate shared-file writes. Report material findings to the parent, with evidence."


def prepare(value):
    name, delta, mode = validate(value)
    # Exact delta is preserved, not summarized or re-encoded as conversation history.
    message = "\n".join(("Same-context twin: " + INTENTS[mode], BOUNDARY, "Task difference:", delta))
    return {"task_name": name, "message": message, "fork_turns": "all"}
