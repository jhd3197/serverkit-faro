"""ServerKit Faro extension backend package.

Issues Faro access grants (``faro://grant`` links) for managed servers and
serves the two public Faro Grant Protocol v1 endpoints the Faro desktop app
calls (manifest fetch + public-key install). The blueprint (``bp``) is the
manifest ``entry_point``; pure protocol helpers live in ``grants_lib`` (no
``app.*`` imports, standalone-testable). See Faro's ``docs/grant-links.md``.
"""
from .grants import bp

__all__ = ['bp']
