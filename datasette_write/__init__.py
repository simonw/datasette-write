from datasette import hookimpl, Forbidden, Response
from datasette.utils import derive_named_parameters
from urllib.parse import urlencode
import re


async def write(request, datasette):
    if not await datasette.permission_allowed(
        request.actor, "datasette-write", default=False
    ):
        raise Forbidden("Permission denied for datasette-write")
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
        sql = request.args.get("sql") or ""
        parameters = await derive_parameters(database, sql)
        # Set values based on incoming request query string
        for parameter in parameters:
            parameter["value"] = request.args.get(parameter["name"]) or ""
        return Response.html(
            await datasette.render_template(
                "datasette_write.html",
                {
                    "databases": databases,
                    "sql_from_args": sql,
                    "selected_database": selected_database,
                    "parameters": parameters,
                    "tables": tables,
                    "views": views,
                    "redirect_to": request.args.get("_redirect_to")
                    or "",  # TODO: Sign this
                    "sql_textarea_height": max(10, int(1.4 * len(sql.split("\n")))),
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

        redirect_to = formdata.get("_redirect_to") or datasette.urls.path(
            "/-/write?"
        ) + urlencode(
            {
                "database": database.name,
                "sql": sql,
            }
        )

        return Response.redirect(redirect_to)
    else:
        return Response.html("Bad method", status_code=405)


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
        (r"^/-/write$", write),
        (r"^/-/write/derive-parameters$", write_derive_parameters),
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
                    "description": "Run queries like insert/update/delete against this database",
                },
            ]

    return inner


@hookimpl
def row_actions(datasette, actor, database, table, row, request):
    async def inner():
        if database != "_internal" and await datasette.permission_allowed(
            actor, "datasette-write", default=False
        ):
            db = datasette.get_database(database)
            pks = []
            columns = []
            for details in await db.table_column_details(table):
                if details.is_pk:
                    pks.append(details.name)
                else:
                    columns.append(
                        {
                            "name": details.name,
                            "notnull": details.notnull,
                        }
                    )
            row_dict = dict(row)
            set_clause_bits = []
            args = {
                "database": database,
            }
            for column in columns:
                field_name = column["name"]
                current_value = str(row_dict.get(field_name) or "")
                if "\n" in current_value:
                    field_name = field_name + "_textarea"
                if column["notnull"]:
                    fragment = "{} = :{}"
                else:
                    fragment = "{} = nullif(:{}, '')"
                set_clause_bits.append(fragment.format(column["name"], field_name))
                args[field_name] = current_value
            set_clauses = ",\n  ".join(set_clause_bits)

            where_clauses = " and ".join("{} = :{}".format(pk, pk) for pk in pks)
            args.update([(pk, row_dict[pk]) for pk in pks])

            sql = 'update "{}" set\n  {}\nwhere {}'.format(
                table, set_clauses, where_clauses
            )
            args["sql"] = sql
            args["_redirect_to"] = request.path
            return [
                {
                    "href": datasette.urls.path("/-/write") + "?" + urlencode(args),
                    "label": "Update using SQL",
                    "description": "Compose and execute a SQL query to update this row",
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
