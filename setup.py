from setuptools import setup, Extension
from sys import platform
from os import path

from importlib.machinery import SourceFileLoader


module = SourceFileLoader(
    "version", path.join("aiofile", "version.py")
).load_module()

libraries = []


if platform == 'linux':
    libraries.append('rt')

if platform in ('linux', 'darwin'):
    try:
        from Cython.Build import cythonize

        extensions = cythonize([
            Extension(
                "aiofile.posix_aio",
                ["aiofile/posix_aio.pyx"],
                libraries=libraries,
            ),
        ], force=True, emit_linenums=False, quiet=True)

    except ImportError:
        extensions = [
            Extension(
                "aiofile.posix_aio",
                ["aiofile/posix_aio.c"],
                libraries=libraries,
            ),
        ]
else:
    extensions = []


setup(
    name='aiofile',
    ext_modules=extensions,
    version=module.__version__,
    packages=[
        'aiofile',
    ],
    license=module.package_license,
    description=module.package_info,
    long_description=open("README.rst").read(),
    platforms=["POSIX"],
    url=module.project_home,
    author=module.__author__,
    author_email=module.team_email,
    provides=["aiofile"],
    build_requires=['cython'],
    keywords=["aio", "python", "asyncio", "cython", "fileio", "io"],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
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
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: Implementation :: CPython',
        'Topic :: Software Development :: Libraries',
        'Topic :: System',
        'Topic :: System :: Operating System',
    ],
    python_requires=">3.4.*, <4",
    extras_require={
        'develop': [
            'Cython',
            'pytest==4.0.2',
            'pytest-asyncio~=0.9.0',
            'pytest-cov',
        ],
        ':python_version < "3.5"': 'typing >= 3.5.3',
    },
)
