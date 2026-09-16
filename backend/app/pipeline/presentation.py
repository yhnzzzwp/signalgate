"""One publication boundary shared by batch scans and single-case research."""
from app.pipeline.gate import apply_gate, sanitize_for_display
from app.pipeline.schema import GateStatus, ScreenedEvent
from app.pipeline.validate import validate_verdict


def screen_outcome(event, snapshot, outcome) -> ScreenedEvent:
    issues = validate_verdict(event, snapshot, outcome.verdict)
    gate = apply_gate(outcome.verdict)
    if issues or outcome.status != "completed":
        gate = gate.model_copy(update={"status": GateStatus.needs_review})
    return ScreenedEvent(event=event, snapshot=snapshot,
                         verdict=sanitize_for_display(outcome.verdict, gate), gate=gate,
                         numeric_issues=issues,
                         research=outcome.model_dump(mode="json", exclude={"verdict"}))
