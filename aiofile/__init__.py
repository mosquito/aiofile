from .utils import Reader, Writer

try:
    from ._aio import AIOFile as _AIOFile
except ImportError:
    from ._thread import AIOFile as _AIOFile


__all__ = 'AIOFile', 'Reader', 'Writer'
