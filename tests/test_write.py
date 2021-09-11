from datasette.app import Datasette
import pytest
import sqlite3
import httpx


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


@pytest.mark.asyncio
@pytest.mark.parametrize("database", ["test", "test2"])
async def test_select_database(ds, database):
    response = await ds.client.get(
        "/-/write?database={}".format(database),
        cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")},
    )
    assert response.status_code == 200
    assert '<option selected="selected">{}</option>'.format(database) in response.text


@pytest.mark.parametrize(
    "sql,expected_message",
    [
        ("create table newtable (id integer)", 'message-info">Query executed<'),
        ("update one set count = 5", 'message-info">2 rows affected<'),
        ("invalid sql", 'message-error">near &#34;invalid&#34;: syntax error<'),
    ],
)
@pytest.mark.asyncio
async def test_execute_write(ds, sql, expected_message):
    async with httpx.AsyncClient(
        app=ds.app(), cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")}
    ) as client:
        # Get csrftoken
        response = await client.get("http://localhost/-/write")
        assert 200 == response.status_code
        csrftoken = response.cookies["ds_csrftoken"]
        # write to database
        response2 = await client.post(
            "http://localhost/-/write",
            data={
                "sql": sql,
                "csrftoken": csrftoken,
                "database": "test",
            },
        )
        assert expected_message in response2.text
