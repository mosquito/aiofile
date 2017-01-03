__all__ = (
    '__author__',
    '__version__',
    'author_info',
    'license',
    'package_info',
    'version_info',
    'AIOFile',
    'Reader',
    'Writer',
)


try:
    from ._aio import AIOFile as _AIOFile
except ImportError:
    from ._thread import AIOFile as _AIOFile

from .utils import Reader, Writer
