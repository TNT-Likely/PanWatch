"""Explicit policy and registry errors."""


class AiAgentError(Exception):
    """Base error for hosts that want to catch runtime-specific faults."""


class DuplicateToolName(AiAgentError):
    pass


class DisallowedTool(AiAgentError):
    pass


class UnknownTool(AiAgentError):
    pass

