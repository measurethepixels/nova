"""Custom exceptions for the processing pipeline."""


class ProcessingAbortedError(RuntimeError):
    """Raised when a manual review signals abort — stops auto_process for this target."""


class ProcessingRetryError(RuntimeError):
    """Raised when a manual review signals retry — re-runs the current step."""
    def __init__(self, step: str):
        super().__init__(f"Retry requested for step: {step}")
        self.step = step
