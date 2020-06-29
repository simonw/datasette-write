# datasette-write

[![PyPI](https://img.shields.io/pypi/v/datasette-write.svg)](https://pypi.org/project/datasette-write/)
[![Changelog](https://img.shields.io/github/v/release/simonw/datasette-write?label=changelog)](https://github.com/simonw/datasette-write/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/datasette-write/blob/master/LICENSE)

Datasette plugin providing a UI for writing to a database

## Installation

Install this plugin in the same environment as Datasette.

    $ pip install datasette-write

## Usage

Having installed the plugin, visit `/-/write` on your Datasette instance to submit SQL queries that will be executed against a write connection to the specified database.

By default only the `root` user can access the page - so you'll need to run Datasette with the `--root` option and click on the link shown in the terminal to sign in and access the page.

The `datasette-write` permission governs access. You can use permission plugins such as [datasette-permissions-sql](https://github.com/simonw/datasette-permissions-sql) to grant additional access to the write interface.

## Development

To set up this plugin locally, first checkout the code. Then create a new virtual environment:

    cd datasette-write
    python3 -mvenv venv
    source venv/bin/activate

Or if you are using `pipenv`:

    pipenv shell

Now install the dependencies and tests:

    pip install -e '.[test]'

To run the tests:

    pytest
