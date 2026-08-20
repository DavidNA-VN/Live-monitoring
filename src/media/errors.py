class MediaInputError(RuntimeError):
    retryable = True


class MediaInputFetchError(MediaInputError):
    pass


class UnsupportedMediaInputError(MediaInputError):
    retryable = False

