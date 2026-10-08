class BulkError(Exception):
    """A bulk payout could not be created, submitted, approved or processed.
    Messages are written for the merchant user and shown in the portal."""


class ParseError(BulkError):
    """The uploaded file could not be read as a payout list."""
