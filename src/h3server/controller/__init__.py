"""Local, trust-gated controller. Importing this package performs no IO."""

from h3server.controller.app import create_app

__all__ = ["create_app"]
