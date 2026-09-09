"""Transitional facade for the AI failover adapter."""

from src.core.ai_failover import FailoverAIClient, clear_ai_failover_state

__all__ = ["FailoverAIClient", "clear_ai_failover_state"]

