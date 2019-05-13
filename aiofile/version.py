author_info = (
    ('Dmitry Orlov', 'me@mosquito.su'),
)

project_home = 'http://github.com/mosquito/aiofile'

package_info = "Asynchronous file operations."
package_license = "Apache 2"

team_email = 'me@mosquito.su'

version_info = (1, 5, 2)

__author__ = ", ".join("{} <{}>".format(*info) for info in author_info)
__version__ = ".".join(map(str, version_info))
