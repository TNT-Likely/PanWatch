"""Explicit policy and registry errors."""


class PanAgentError(Exception):
    """Base error for hosts that want to catch runtime-specific faults."""


class DuplicateToolName(PanAgentError):
    pass


class DisallowedTool(PanAgentError):
    pass


class UnknownTool(PanAgentError):
    pass

