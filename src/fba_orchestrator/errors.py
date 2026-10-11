class WorkflowError(RuntimeError):
    """Base class for explicit, business-safe failures."""


class SafetyGateError(WorkflowError):
    pass


class ConflictError(WorkflowError):
    pass


class UnknownWriteError(WorkflowError):
    pass


class TransientReadError(WorkflowError):
    pass


class PermanentReadError(WorkflowError):
    pass
