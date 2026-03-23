from dataclasses import dataclass


@dataclass
class GoldenFailureGate:
    first_failure_nodeid: str | None = None
    first_failure_phase: str | None = None

    def record_failure(self, nodeid: str, when: str) -> None:
        if self.first_failure_nodeid is not None:
            return
        self.first_failure_nodeid = nodeid
        self.first_failure_phase = when

    def skip_reason_for(self, nodeid: str, *, is_golden: bool) -> str | None:
        if not is_golden or self.first_failure_nodeid is None or nodeid == self.first_failure_nodeid:
            return None
        phase = self.first_failure_phase or "call"
        return (
            "Skipping after earlier golden failure in "
            f"{self.first_failure_nodeid} ({phase}) to avoid cascading follow-on "
            "failures from the shared golden harness."
        )
