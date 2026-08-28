class PresentationAdapterError(Exception):
    """Base error exposed by presentation adapters."""


class PresentationConflictError(PresentationAdapterError):
    """The request conflicts with an existing presentation resource."""


class PresentationServiceUnavailableError(PresentationAdapterError):
    """A presentation dependency or public read model is unavailable."""
