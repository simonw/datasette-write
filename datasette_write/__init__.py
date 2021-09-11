from datasette import hookimpl
from datasette.utils.asgi import Response
from urllib.parse import urlencode
import re


async def write(request, datasette):
    if not await datasette.permission_allowed(
        request.actor, "datasette-write", default=False
    ):
        return Response.html("Permission denied for datasette-write", status=403)
    databases = [
        db
        for db in datasette.databases.values()
        if db.is_mutable and db.name != "_internal"
    ]
    if request.method == "GET":
        selected_database = request.args.get("database") or ""
        if not selected_database or selected_database == "_internal":
            selected_database = databases[0].name
        database = datasette.get_database(selected_database)
        tables = await database.table_names()
        views = await database.view_names()
        return Response.html(
            await datasette.render_template(
                "datasette_write.html",
                {
                    "databases": databases,
                    "sql": request.args.get("sql") or "",
                    "selected_database": selected_database,
                    "tables": tables,
                    "views": views,
                },
                request=request,
            )
        )
    elif request.method == "POST":
        formdata = await request.post_vars()
        database_name = formdata["database"]
        sql = formdata["sql"]
        try:
            database = [db for db in databases if db.name == database_name][0]
        except IndexError:
            return Response.html("Database not found", status_code=404)

        result = None
        message = None
        try:
            result = await database.execute_write(sql, block=True)
            if result.rowcount == -1:
                # Maybe it was a create table / create view?
                name_verb_type = parse_create_alter_drop_sql(sql)
                if name_verb_type:
                    name, verb, type = name_verb_type
                    message = "{verb} {type}: {name}".format(
                        name=name,
                        type=type,
                        verb={
                            "create": "Created",
                            "drop": "Dropped",
                            "alter": "Altered",
                        }[verb],
                    )
                else:
                    message = "Query executed"
            else:
                message = "{} row{} affected".format(
                    result.rowcount, "" if result.rowcount == 1 else "s"
                )
        except Exception as e:
            message = str(e)
        datasette.add_message(
            request,
            message,
            type=datasette.INFO if result else datasette.ERROR,
        )
        return Response.redirect(
            datasette.urls.path("/-/write?") + urlencode({"database": database.name})
        )
    else:
        return Response.html("Bad method", status_code=405)


@hookimpl
def register_routes():
    return [
        (r"^/-/write$", write),
    ]


@hookimpl
def permission_allowed(actor, action):
    if action == "datasette-write" and actor and actor.get("id") == "root":
        return True


@hookimpl
def menu_links(datasette, actor):
    async def inner():
        if await datasette.permission_allowed(actor, "datasette-write", default=False):
            return [
                {
                    "href": datasette.urls.path("/-/write"),
                    "label": "Execute SQL write",
                },
            ]

    return inner


@hookimpl
def database_actions(datasette, actor, database):
    async def inner():
        if database != "_internal" and await datasette.permission_allowed(
            actor, "datasette-write", default=False
        ):
            return [
                {
                    "href": datasette.urls.path(
                        "/-/write?"
                        + urlencode(
                            {
                                "database": database,
                            }
                        )
                    ),
                    "label": "Execute SQL write",
                },
            ]

    return inner


_name_patterns = (
    r"\[([^\]]+)\]",  # create table [foo]
    r'"([^"]+)"',  # create table "foo"
    r"'([^']+)'",  # create table 'foo'
    r"([a-zA-Z_][a-zA-Z0-9_]*)",  # create table foo123
)
_res = []
for type in ("table", "view"):
    for name_pattern in _name_patterns:
        for verb in ("create", "drop"):
            pattern = r"\s*{}\s+{}\s+{}.*".format(verb, type, name_pattern)
            _res.append((type, verb, re.compile(pattern, re.I)))
        alter_table_pattern = r"\s*alter\s+table\s+{}.*".format(name_pattern)
        _res.append(("table", "alter", re.compile(alter_table_pattern, re.I)))


def parse_create_alter_drop_sql(sql):
    """
    Simple regex-based detection of 'create table foo' type queries

    Returns the view or table name, or None if none was identified
    """
    for type, verb, _re in _res:
        match = _re.match(sql)
        if match is not None:
            return match.group(1), verb, type
    return None
