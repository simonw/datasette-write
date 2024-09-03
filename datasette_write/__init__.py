from datasette import hookimpl, Forbidden, Response
from datasette.utils import derive_named_parameters
from urllib.parse import urlencode
import re


async def write(request, datasette):
    if not await datasette.permission_allowed(
        request.actor, "datasette-write", default=False
    ):
        raise Forbidden("Permission denied for datasette-write")
    database_name = request.url_vars["database"]
    if request.method == "GET":
        database = datasette.get_database(database_name)
        tables = await database.table_names()
        views = await database.view_names()
        sql = request.args.get("sql") or ""
        return Response.html(
            await datasette.render_template(
                "datasette_write.html",
                {
                    "sql_from_args": sql,
                    "database_name": database_name,
                    "parameters": await derive_parameters(database, sql),
                    "tables": tables,
                    "views": views,
                },
                request=request,
            )
        )
    elif request.method == "POST":
        formdata = await request.post_vars()
        sql = formdata["sql"]
        database = datasette.get_database(database_name)

        result = None
        message = None
        params = {
            key[3:]: value for key, value in formdata.items() if key.startswith("qp_")
        }
        try:
            result = await database.execute_write(sql, params, block=True)
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
            datasette.urls.path("/-/write?")
            + urlencode(
                {
                    "database": database.name,
                    "sql": sql,
                }
            )
        )
    else:
        return Response.html("Bad method", status_code=405)


async def write_redirect(request, datasette):
    if not await datasette.permission_allowed(
        request.actor, "datasette-write", default=False
    ):
        raise Forbidden("Permission denied for datasette-write")

    db = request.args.get("database") or ""
    if not db:
        db = datasette.get_database().name

    # Preserve query string, except the database=
    pairs = [
        (key, request.args.getlist(key)) for key in request.args if key != "database"
    ]
    query_string = ""
    if pairs:
        query_string = "?" + urlencode(pairs, doseq=True)

    return Response.redirect(datasette.urls.database(db) + "/-/write" + query_string)


async def derive_parameters(db, sql):
    parameters = await derive_named_parameters(db, sql)
    return [
        {
            "name": parameter,
            "type": "textarea" if parameter.endswith("textarea") else "text",
            "label": (
                parameter.replace("_textarea", "")
                if parameter.endswith("textarea")
                else parameter
            ),
        }
        for parameter in parameters
    ]


async def write_derive_parameters(datasette, request):
    if not await datasette.permission_allowed(
        request.actor, "datasette-write", default=False
    ):
        raise Forbidden("Permission denied for datasette-write")
    try:
        db = datasette.get_database(request.args.get("database"))
    except KeyError:
        db = datasette.get_database()
    parameters = await derive_parameters(db, request.args.get("sql") or "")
    return Response.json({"parameters": parameters})


@hookimpl
def register_routes():
    return [
        (r"^/(?P<database>[^/]+)/-/write$", write),
        (r"^/-/write$", write_redirect),
        (r"^/-/write/derive-parameters$", write_derive_parameters),
    ]


@hookimpl
def permission_allowed(actor, action):
    if action == "datasette-write" and actor and actor.get("id") == "root":
        return True


@hookimpl
def database_actions(datasette, actor, database):
    async def inner():
        if database != "_internal" and await datasette.permission_allowed(
            actor, "datasette-write", default=False
        ):
            return [
                {
                    "href": datasette.urls.database(database) + "/-/write",
                    "label": "Execute SQL write",
                    "description": "Run queries like insert/update/delete against this database",
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
