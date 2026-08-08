"""shipproof — prove a deploy actually landed."""

from .core import (  # noqa: F401
    Fingerprint,
    Marker,
    Probe,
    ProofError,
    Result,
    check_stability,
    constant_warning,
    fingerprint,
    probe,
    verify,
)

__version__ = "0.1.0"
