class CapabilityNotLicensed(Exception):
    """
    Raised when code tries to perform an activity the current BoG licence does
    not permit. This is a safety stop, not a bug: the feature is built, but the
    licence that legally allows it is not yet active in this deployment.
    """

    def __init__(self, capability, active_licence):
        self.capability = capability
        self.active_licence = active_licence
        super().__init__(
            f"Capability '{capability}' is not permitted under the active licence "
            f"'{active_licence}'. It unlocks at a higher licence tier. "
            f"See docs/LICENCE-CAPABILITY-MATRIX.md."
        )
