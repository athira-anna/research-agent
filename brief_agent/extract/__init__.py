"""Stage 3: extract claims per source and check that each one is grounded in, and supported by, its quote."""

from .pipeline import extract_claims

__all__ = ["extract_claims"]
