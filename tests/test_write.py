from bs4 import BeautifulSoup as Soup
import datasette
from datasette.app import Datasette
from datasette_write import parse_create_alter_drop_sql
import pytest
import sqlite3
import textwrap
import urllib


@pytest.fixture
def ds(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "test.db")
    db_path2 = str(db_directory / "test2.db")
    sqlite3.connect(db_path).executescript(
        """
        create table one (id integer primary key, count integer);
        insert into one (id, count) values (1, 10);
        insert into one (id, count) values (2, 20);
    """
    )
    sqlite3.connect(db_path2).executescript(
        """
        create table simple_pk (id integer primary key, name text);
        insert into simple_pk (id, name) values (1, 'one');
        create table simple_pk_multiline (id integer primary key, name text);
        insert into simple_pk_multiline (id, name) values (1, 'one' || char(10) || 'two');
        create table compound_pk (id1 integer, id2 integer, name text, primary key (id1, id2));
        insert into compound_pk (id1, id2, name) values (1, 2, 'one-two');
        create table has_not_null (id integer primary key, sql text not null);
        insert into has_not_null (id, sql) values (1, 'one');
        """
    )
    ds = Datasette([db_path, db_path2])
    return ds


@pytest.mark.asyncio
async def test_permission_denied(ds):
    response = await ds.client.get("/test/-/write")
    assert 403 == response.status_code


@pytest.mark.asyncio
async def test_permission_granted_to_root(ds):
    response = await ds.client.get(
        "/test/-/write",
        cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")},
    )
    assert response.status_code == 200
    assert "<strong>Tables</strong>:" in response.text
    assert '<a href="/test/one">one</a>' in response.text

    # Should have database action menu option too:
    anon_response = (await ds.client.get("/test")).text
    fragment = '<a href="/test/-/write">Execute SQL write'
    assert fragment not in anon_response
    root_response = (
        await ds.client.get(
            "/test", cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")}
        )
    ).text
    assert fragment in root_response


@pytest.mark.asyncio
async def test_populate_sql_from_query_string(ds):
    response = await ds.client.get(
        "/test/-/write?sql=select+1",
        cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")},
    )
    assert response.status_code == 200
    assert '">select 1</textarea>' in response.text


@pytest.mark.parametrize(
    "database,sql,params,expected_message",
    [
        (
            "test",
            "create table newtable (id integer)",
            {},
            "Created table: newtable",
        ),
        (
            "test",
            "drop table one",
            {},
            "Dropped table: one",
        ),
        (
            "test",
            "alter table one add column bigfile blob",
            {},
            "Altered table: one",
        ),
        (
            "test2",
            "create table newtable (id integer)",
            {},
            "Created table: newtable",
        ),
        (
            "test2",
            "create view blah as select 1 + 1",
            {},
            "Created view: blah",
        ),
        ("test", "update one set count = 5", {}, "2 rows affected"),
        ("test", "invalid sql", {}, 'near "invalid": syntax error'),
        # Parameterized queries
        ("test", "update one set count = :count", {"qp_count": 4}, "2 rows affected"),
        # This should error
        (
            "test",
            "update one set count = :count",
            {},
            "Incorrect number of bindings supplied. The current statement uses 1, and there are 0 supplied.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_execute_write(ds, database, sql, params, expected_message):
    # Get csrftoken
    cookies = {"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")}
    response = await ds.client.get("/{}/-/write".format(database), cookies=cookies)
    assert 200 == response.status_code
    csrftoken = response.cookies["ds_csrftoken"]
    cookies["ds_csrftoken"] = csrftoken
    data = {
        "sql": sql,
        "csrftoken": csrftoken,
    }
    data.update(params)
    # write to database
    response2 = await ds.client.post(
        "/{}/-/write".format(database),
        data=data,
        cookies=cookies,
    )
    messages = [m[0] for m in ds.unsign(response2.cookies["ds_messages"], "messages")]
    assert messages[0] == expected_message
    # Should have preserved ?database= in redirect:
    bits = dict(urllib.parse.parse_qsl(response2.headers["location"].split("?")[-1]))
    assert bits["database"] == database
    # Should have preserved ?sql= in redirect:
    assert bits["sql"] == sql


@pytest.mark.parametrize(
    "sql,expected_name,expected_verb,expected_type",
    (
        ("create table hello (...", "hello", "create", "table"),
        ("  create view hello2 as (...", "hello2", "create", "view"),
        ("select 1 + 1", None, None, None),
        # Various styles of quoting
        ("create table 'hello' (", "hello", "create", "table"),
        ('  create   \n table "hello" (', "hello", "create", "table"),
        ("create table [hello] (", "hello", "create", "table"),
        ("create view 'hello' (", "hello", "create", "view"),
        ('  create   \n view "hello" (', "hello", "create", "view"),
        ("create view [hello] (", "hello", "create", "view"),
        # Alter table
        ("alter table [hello] ", "hello", "alter", "table"),
        # But no alter view
        ("alter view [hello] ", None, None, None),
    ),
)
def test_parse_create_alter_drop_sql(sql, expected_name, expected_verb, expected_type):
    name_verb_type = parse_create_alter_drop_sql(sql)
    if expected_name is None:
        assert name_verb_type is None
    else:
        assert name_verb_type == (expected_name, expected_verb, expected_type)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_path",
    (
        ("/-/write", "/test/-/write"),
        ("/-/write?database=test", "/test/-/write"),
        ("/-/write?database=test2", "/test2/-/write"),
        ("/-/write?database=test2&a=1&a=2", "/test2/-/write?a=1&a=2"),
    ),
)
async def test_write_redirect(ds, path, expected_path):
    response = await ds.client.get(
        path,
        cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")},
    )
    assert response.status_code == 302
    assert response.headers["location"] == expected_path


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", (True, False))
async def test_redirect_to(ds, valid):
    cookies = {"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")}
    signed_redirect_to = ds.sign("/", "redirect_to")
    used_redirect_to = signed_redirect_to + ("" if valid else "invalid")
    response = await ds.client.get(
        "/test/-/write",
        params={"_redirect_to": used_redirect_to},
        cookies=cookies,
    )
    assert response.status_code == 200
    # Should have redirect_to field
    input = Soup(response.text, "html.parser").find("input", {"name": "_redirect_to"})
    assert input.attrs["value"] == used_redirect_to
    assert '<input type="hidden" name="_redirect_to"' in response.text
    csrftoken = response.cookies["ds_csrftoken"]
    cookies["ds_csrftoken"] = csrftoken
    data = {
        "sql": "select 1",
        "csrftoken": csrftoken,
        "_redirect_to": signed_redirect_to,
    }
    # POSTing this should redirect to / if signed_redirect_to is valid
    response2 = await ds.client.post(
        "/test/-/write",
        data=data,
        cookies=cookies,
    )
    assert response2.status_code == 302
    actual_redirect_to = response2.headers["location"]
    assert actual_redirect_to == "/" if valid else "/test/-/write"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ("valid", "invalid", "none"))
async def test_title(ds, scenario):
    cookies = {"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")}
    signed_title = ds.sign("Custom Title", "query_title")
    params = {}
    if scenario != "none":
        params["_title"] = signed_title + ("" if scenario == "valid" else "invalid")
    response = await ds.client.get(
        "/test/-/write",
        params=params,
        cookies=cookies,
    )
    assert response.status_code == 200
    if scenario == "valid":
        assert "<title>Custom Title</title>" in response.text
        # <details><summary only if custom title is set
        assert "<summary>SQL query</summary>" in response.text
    else:
        assert "<title>Write to test with SQL</title>" in response.text
        assert "<summary>SQL query</summary>" not in response.text


@pytest.mark.asyncio
# Skip if Datasette < ('1', '0a15')
@pytest.mark.skipif(
    datasette.__version_info__ < ("1", "0"),
    reason="Datasette < 1.0 does not support this hook",
)
@pytest.mark.parametrize(
    "path,expected",
    [
        (
            "/test2/simple_pk/1",
            textwrap.dedent(
                """
                    update "simple_pk" set
                      "name" = nullif(:name, '')
                    where "id" = :id_hidden
                """
            ).strip(),
        ),
        (
            "/test2/simple_pk_multiline/1",
            textwrap.dedent(
                """
                    update "simple_pk_multiline" set
                      "name" = nullif(:name_textarea, '')
                    where "id" = :id_hidden
                """
            ).strip(),
        ),
        (
            "/test2/compound_pk/1,2",
            textwrap.dedent(
                """
                    update "compound_pk" set
                      "name" = nullif(:name, '')
                    where "id1" = :id1_hidden and "id2" = :id2_hidden
                """
            ).strip(),
        ),
        (
            "/test2/has_not_null/1",
            textwrap.dedent(
                """
                    update "has_not_null" set
                      "sql" = :_sql
                    where "id" = :id_hidden
                """
            ).strip(),
        ),
    ],
)
async def test_row_actions(ds, path, expected):
    cookies = {"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")}
    response = await ds.client.get(
        path,
        cookies=cookies,
    )
    href = Soup(response.text, "html.parser").select(".dropdown-menu a")[0]["href"]
    qs = href.split("?")[-1]
    bits = dict(urllib.parse.parse_qsl(qs))
    actual = bits["sql"]
    assert actual == expected
