from setuptools import setup, Extension
from sys import platform


version_info = (0, 1, 4)

author_info = (
    ('Dmitry Orlov', 'me@mosquito.su'),
)


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
    version=".".join(str(x) for x in version_info),
    packages=[
        'aiofile',
    ],
    package_data={
        'aiofile': ['_aio.pyi']
    },
    license="Apache 2",
    description="Asynchronous file operations",
    long_description=open("README.rst").read(),
    platforms=["POSIX"],
    url='http://github.com/mosquito/aiofile',
    author=", ".join("{} <{}>".format(*info) for info in author_info),
    author_email=", ".join("{}".format(info[1]) for info in author_info),
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
