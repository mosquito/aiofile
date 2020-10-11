from importlib.machinery import SourceFileLoader
from os import path

from setuptools import setup


module = SourceFileLoader(
    "version", path.join("aiofile", "version.py"),
).load_module()

libraries = []


setup(
    name="aiofile",
    version=module.__version__,
    packages=["aiofile"],
    license=module.package_license,
    description=module.package_info,
    long_description=open("README.rst").read(),
    platforms=["POSIX"],
    url=module.project_home,
    author=module.__author__,
    author_email=module.team_email,
    provides=["aiofile"],
    keywords=["aio", "python", "asyncio", "fileio", "io"],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Intended Audience :: Education",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Natural Language :: Russian",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS :: MacOS X",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: Implementation :: CPython",
        "Topic :: Software Development :: Libraries",
        "Topic :: System",
        "Topic :: System :: Operating System",
    ],
    python_requires=">3.4.*, <4",
    extras_require={
        "develop": [
            "aiomisc",
            "asynctest",
            "pytest",
            "pytest-cov",
        ],
    },
    install_requires=["caio~=0.6.0"],
)
