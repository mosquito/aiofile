#!/usr/bin/env python
# encoding: utf-8
from setuptools import setup, Extension
from sys import platform


package_info = "Asynchronous file operations"
version_info = (0, 1, 0)


author_info = (
    ('Dmitry Orlov', 'me@mosquito.su'),
)

author_email = ", ".join("{}".format(info[1]) for info in author_info)

license = "Apache 2"

__version__ = ".".join(str(x) for x in version_info)
__author__ = ", ".join("{} <{}>".format(*info) for info in author_info)


libraries = []


if platform == 'linux':
    libraries.append('rt')

if platform in ('linux', 'darwin'):
    try:
        from Cython.Build import cythonize

        extensions = cythonize([
            Extension(
                "aiofile._aio",
                ["aiofile/_aio.pyx"],
                libraries=libraries,
            ),
        ], force=True, emit_linenums=False)

    except ImportError:
        extensions = [
            Extension(
                "aiofile._aio",
                ["aiofile/_aio.c"],
                libraries=libraries,
            ),
        ]
else:
    extensions = []


setup(
    name='aiofile',
    ext_modules=extensions,
    version=__version__,
    packages=[
        'aiofile',
    ],
    package_data={
        'aiofile': ['_aio.pyi']
    },
    license=license,
    description=package_info,
    long_description=open("README.rst").read(),
    platforms=["POSIX"],
    url='http://github.com/mosquito/aiofile',
    author=__author__,
    author_email=author_email,
    provides=["aiofile"],
    build_requires=['cython'],
    keywords="aio, python, asyncio, cython",
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Intended Audience :: Education',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: Apache Software License',
        'Natural Language :: English',
        'Natural Language :: Russian',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS :: MacOS X',
        'Programming Language :: Cython',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: Implementation :: CPython',
        'Topic :: Software Development :: Libraries',
        'Topic :: System',
        'Topic :: System :: Operating System',
    ],
)