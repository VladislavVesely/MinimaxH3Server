"""Local controller; target bootstrap imports must not require FastAPI/Paramiko.

Preserve the public factory while deferring controller-only dependencies until
it is requested. The verified wheel's target module runs before pip installation.
"""

__all__ = ["create_app"]


def __getattr__(name):
    if name == "create_app":
        from h3server.controller.app import create_app
        return create_app
    raise AttributeError(name)
