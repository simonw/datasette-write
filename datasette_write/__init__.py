from datasette import hookimpl, Forbidden, Response
from datasette.utils import derive_named_parameters
import itsdangerous
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
        parameters = await derive_parameters(database, sql)
        # Set values based on incoming request query string
        for parameter in parameters:
            parameter["value"] = request.args.get(parameter["name"]) or ""
        custom_title = ""
        try:
            custom_title = datasette.unsign(
                request.args.get("_title", ""), "query_title"
            )
        except itsdangerous.BadSignature:
            pass
        return Response.html(
            await datasette.render_template(
                "datasette_write.html",
                {
                    "custom_title": custom_title,
                    "sql_from_args": sql,
                    "parameters": parameters,
                    "database_name": database_name,
                    "tables": tables,
                    "views": views,
                    "redirect_to": request.args.get("_redirect_to"),
                    "sql_textarea_height": max(10, int(1.4 * len(sql.split("\n")))),
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
        # Default redirect back to this page
        redirect_to = datasette.urls.path("/-/write?") + urlencode(
            {
                "database": database.name,
                "sql": sql,
            }
        )
        try:
            # Unless value and valid signature for _redirect_to=
            redirect_to = datasette.unsign(formdata["_redirect_to"], "redirect_to")
        except (itsdangerous.BadSignature, KeyError):
            pass
        return Response.redirect(redirect_to)
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

    def _type(parameter):
        type = "text"
        if parameter.endswith("_textarea"):
            type = "textarea"
        if parameter.endswith("_hidden"):
            type = "hidden"
        return type

    def _label(parameter):
        if parameter.endswith("_textarea"):
            return parameter[: -len("_textarea")]
        if parameter.endswith("_hidden"):
            return parameter[: -len("_hidden")]
        return parameter

    return [
        {"name": parameter, "type": _type(parameter), "label": _label(parameter)}
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
                column_name = column["name"]
                field_name = column_name
                if column_name in ("sql", "_redirect_to", "_title"):
                    field_name = "_{}".format(column_name)
                current_value = str(row_dict.get(column_name) or "")
                if "\n" in current_value:
                    field_name = field_name + "_textarea"
                if column["notnull"]:
                    fragment = '"{}" = :{}'
                else:
                    fragment = "\"{}\" = nullif(:{}, '')"
                set_clause_bits.append(fragment.format(column["name"], field_name))
                args[field_name] = current_value
            set_clauses = ",\n  ".join(set_clause_bits)

            # Add the where clauses, with _hidden to prevent edits
            where_clauses = " and ".join(
                '"{}" = :{}_hidden'.format(pk, pk) for pk in pks
            )
            args.update([("{}_hidden".format(pk), row_dict[pk]) for pk in pks])

            row_desc = ", ".join(
                "{}={}".format(k, v) for k, v in row_dict.items() if k in pks
            )

            sql = 'update "{}" set\n  {}\nwhere {}'.format(
                table, set_clauses, where_clauses
            )
            args["sql"] = sql
            args["_redirect_to"] = datasette.sign(request.path, "redirect_to")
            args["_title"] = datasette.sign(
                "Update {} where {}".format(table, row_desc), "query_title"
            )
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
