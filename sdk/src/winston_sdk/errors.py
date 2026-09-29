"""The errors an extension sees or raises."""


class NotPermitted(Exception):
    """A call outside the extension's permissions: an undeclared host, Home Assistant without the
    permission, an entity it was not given."""


class HomeAssistantError(Exception):
    """Home Assistant failed or refused a request."""


class SettingsError(Exception):
    """Raised by an `@ext.validate` handler; the message shows on that field of the card."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message
