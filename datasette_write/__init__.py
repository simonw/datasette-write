from datasette import hookimpl
from datasette.utils.asgi import Response


async def write(request, datasette):
    if not await datasette.permission_allowed(
        request.actor, "datasette-write", default=False
    ):
        return Response.html("Permission denied for datasette-write", status=403)
    databases = [db for db in datasette.databases.values() if db.is_mutable]
    if request.method == "GET":
        return Response.html(
            await datasette.render_template(
                "datasette_write.html",
                {"databases": databases, "sql": request.args.get("sql") or ""},
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

        error = None
        result = None
        message = None
        try:
            result = await database.execute_write(sql, block=True)
            if result.rowcount == -1:
                message = "Query executed"
            else:
                message = "{} row{} affected".format(
                    result.rowcount, "" if result.rowcount == 1 else "s"
                )
        except Exception as e:
            error = e
            message = str(e)
        datasette.add_message(
            request, message, type=datasette.INFO if result else datasette.ERROR,
        )
        return Response.redirect("/-/write")
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
