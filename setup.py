#!/usr/bin/env python
# encoding: utf-8
import aiofile as module
from setuptools import setup, Extension
from sys import platform

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
    name=module.__name__,
    ext_modules=extensions,
    version=module.__version__,
    packages=[
        'aiofile',
    ],
    package_data={
        'aiofile': ['_aio.pyi']
    },
    license=module.license,
    description=module.package_info,
    long_description=open("README.rst").read(),
    platforms=["POSIX"],
    url='http://github.com/mosquito/aiofile',
    author=module.__author__,
    author_email=module.author_email,
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