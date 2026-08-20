class PlaylistLoadError(RuntimeError):

    def __init__(
        self,
        uri: str,
        message: str,
    ):
        super().__init__(
            f"Unable to load playlist {uri}: {message}"
        )

        self.uri = uri