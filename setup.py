from setuptools import setup
import os

VERSION = "0.1a"


def get_long_description():
    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md"),
        encoding="utf8",
    ) as fp:
        return fp.read()


setup(
    name="datasette-write",
    description="Datasette plugin providing a UI for writing to a database",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    author="Simon Willison",
    url="https://github.com/simonw/datasette-write",
    project_urls={
        "Issues": "https://github.com/simonw/datasette-write/issues",
        "CI": "https://github.com/simonw/datasette-write/actions",
        "Changelog": "https://github.com/simonw/datasette-write/releases",
    },
    license="Apache License, Version 2.0",
    version=VERSION,
    packages=["datasette_write"],
    package_data={"datasette_write": ["templates/datasette_write.html",],},
    entry_points={"datasette": ["write = datasette_write"]},
    install_requires=["datasette>=0.45a4"],
    extras_require={"test": ["pytest", "pytest-asyncio", "httpx"]},
    tests_require=["datasette-write[test]"],
)
