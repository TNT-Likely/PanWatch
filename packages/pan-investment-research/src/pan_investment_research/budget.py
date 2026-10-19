"""Per-run atomic attempt, tool, feedback and deadline accounting."""
import asyncio
import time

from .contracts import ResearchPolicy


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, policy: ResearchPolicy):
        self.policy = policy
        self.expires = time.monotonic() + policy.deadline_seconds
        self.attempts = self.tools = self.feedback = 0
        self.input_tokens = self.output_tokens = 0
        self.unknown_usage = 0
        self.responses = 0
        self.reserved_tokens = 0
        self.lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(policy.concurrency)

    def remaining(self):
        value = self.expires - time.monotonic()
        if value <= 0:
            raise BudgetExceeded("research deadline exceeded")
        return value

    async def attempt(self, reserve=0, tokens=0):
        async with self.lock:
            self.remaining()
            if self.attempts + reserve >= self.policy.max_attempts:
                raise BudgetExceeded("model attempt budget exhausted")
            if self.reserved_tokens + tokens > self.policy.max_total_tokens:
                raise BudgetExceeded("estimated token budget exhausted")
            self.attempts += 1
            self.reserved_tokens += tokens

    async def tool(self):
        async with self.lock:
            self.remaining()
            if self.tools >= self.policy.max_tools:
                raise BudgetExceeded("tool budget exhausted")
            self.tools += 1

    async def use_feedback(self, reserve=0):
        async with self.lock:
            self.remaining()
            if self.feedback >= self.policy.max_feedback or self.attempts + reserve >= self.policy.max_attempts:
                raise BudgetExceeded("feedback budget exhausted")
            self.feedback += 1

    def record_usage(self, usage):
        self.responses += 1
        if not usage:
            self.unknown_usage += 1
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)

    def snapshot(self):
        return {"attempts": self.attempts, "tools": self.tools, "feedback": self.feedback,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "unknown_usage": self.unknown_usage + self.attempts - self.responses,
                "reserved_tokens": self.reserved_tokens}
