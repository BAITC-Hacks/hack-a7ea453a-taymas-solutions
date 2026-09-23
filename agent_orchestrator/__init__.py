"""Optional AML Copilot. Importing this package never starts network or file I/O."""

from .contracts import Answer, Request, RequestError, for_browser
from .orchestrator import GraphBackend, answer, run

__all__ = ["Answer", "Request", "RequestError", "GraphBackend", "answer", "run", "for_browser"]
