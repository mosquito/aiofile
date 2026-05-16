import importlib.metadata
from email.message import Message
from email.utils import parseaddr
from typing import cast


package_metadata = cast(Message, importlib.metadata.metadata("aiofile"))

_author_email_raw = package_metadata.get("Author-email", "")
_author_name, _author_email_addr = parseaddr(_author_email_raw)

__author__ = package_metadata.get("Author", _author_name)
__version__ = package_metadata["Version"]
author_info = [(__author__, _author_email_addr or _author_email_raw)]
package_info = package_metadata.get("Summary", "")
package_license = package_metadata.get(
    "License-Expression", package_metadata.get("License", ""),
)
project_home = next(
    (
        url.split(",")[1].strip()
        for url in package_metadata.get_all("Project-URL", [])
        if "homepage" in url.lower()
    ),
    "",
)
team_email = _author_email_addr or _author_email_raw
version_info = tuple(map(int, __version__.split(".")))
