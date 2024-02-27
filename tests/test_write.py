from datasette.app import Datasette
from datasette_write import parse_create_alter_drop_sql
import pytest
import sqlite3
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
    sqlite3.connect(db_path2).execute("vacuum")
    ds = Datasette([db_path, db_path2])
    return ds


@pytest.mark.asyncio
async def test_permission_denied(ds):
    response = await ds.client.get("/-/write")
    assert 403 == response.status_code


@pytest.mark.asyncio
async def test_permission_granted_to_root(ds):
    response = await ds.client.get(
        "/-/write",
        cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")},
    )
    assert response.status_code == 200
    assert "<strong>Tables</strong>:" in response.text
    assert '<a href="/test/one">one</a>' in response.text

    # Should have database action menu option too:
    anon_response = (await ds.client.get("/test")).text
    fragment = ">Execute SQL write<"
    assert fragment not in anon_response
    root_response = (
        await ds.client.get(
            "/test", cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")}
        )
    ).text
    assert fragment in root_response


@pytest.mark.asyncio
@pytest.mark.parametrize("database", ["test", "test2"])
async def test_select_database(ds, database):
    response = await ds.client.get(
        "/-/write?database={}".format(database),
        cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")},
    )
    assert response.status_code == 200
    assert '<option selected="selected">{}</option>'.format(database) in response.text


@pytest.mark.asyncio
async def test_populate_sql_from_query_string(ds):
    response = await ds.client.get(
        "/-/write?sql=select+1",
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
    response = await ds.client.get("/-/write", cookies=cookies)
    assert 200 == response.status_code
    csrftoken = response.cookies["ds_csrftoken"]
    cookies["ds_csrftoken"] = csrftoken
    data = {
        "sql": sql,
        "csrftoken": csrftoken,
        "database": database,
    }
    data.update(params)
    # write to database
    response2 = await ds.client.post(
        "/-/write",
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
