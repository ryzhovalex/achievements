# Tools for working with PostgreSQL.


import functools
from signal import signal
from typing import Any, Awaitable, Callable, Iterable
import asyncpg
import asyncpg.transaction

import core


indentation = "    "
_connection_pool: asyncpg.Pool | None = None

class Record(asyncpg.Record):
    def __setattr__(self, __name: str, __value: Any) -> None:
        return super().__setattr__(__name, __value)

    def __getattr__(self, name) -> Any:
        keys = list(self.keys())
        if name in keys:
            return self[name]
        raise AttributeError(f"Record '{self.__class__.__name__}' has no key '{name}', available keys: {keys}")

    def to_dict(self) -> dict:
        return dict(self)

RecordExporter = Callable[[Record], Awaitable[dict]]
exporters: dict[str, RecordExporter] = {}

def record_exporter(key: str):
    def wrapper(exporter: RecordExporter):
        if key in exporters:
            raise Exception(f"Exporter '{key}' is already registered.")
        exporters[key] = exporter

        async def inner(*args, **kwargs):
            await exporter(*args, **kwargs)

        return inner

    return wrapper

async def export_record(exporter_key: str, record: Record) -> dict:
    if exporter_key not in exporters:
        raise Exception(f"Exporter '{exporter_key}' not found.")
    exporter = exporters[exporter_key]
    return await exporter(record)

# Connection wrapper object to support more convenient access to connection functions.
class Connection:
    def __init__(self, connection: asyncpg.Connection, transaction: "Transaction"):
        self.base_connection = connection
        self.transaction = transaction

    async def execute(self, query: str, *args) -> str:
        return await self.base_connection.execute(query, *args)

    async def execute_many(self, query: str, args: Iterable[Iterable]) -> str:
        return await self.base_connection.executemany(query, args)

    async def fetch(self, query: str, *args) -> list[Record]:
        return await self.base_connection.fetch(query, *args, record_class=Record)

    async def fetch_first(self, query: str, *args) -> Record:
        r = await self.try_fetch_first(query, *args)
        if r is None:
            raise Exception("record not found")
        return r

    async def try_fetch_first(self, query: str, *args) -> Record | None:
        r = await self.base_connection.fetchrow(query, *args, record_class=Record)
        return r

    async def fetch_first_value(self, query: str, *args, column: int = 0) -> Any:
        return await self.base_connection.fetchval(query, *args, column=column)

    async def commit(self):
        await self.transaction.commit()

    async def rollback(self):
        await self.transaction.rollback()

# Transaction wrapper object to support more convenient connect+transaction one-shot call.
class Transaction:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.commited_or_rollbacked = False

    async def __aenter__(self):
        self.connection = Connection(await self.pool.acquire(), self)
        self.transaction: asyncpg.transaction.Transaction = self.connection.base_connection.transaction()
        await self.transaction.start()
        return self.connection

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self.commited_or_rollbacked:
            if exc_type is not None:
                await self.rollback()
            else:
                await self.commit()
        await self.pool.release(self.connection.base_connection)

    async def commit(self):
        await self.transaction.commit()
        self.commited_or_rollbacked = True

    async def rollback(self):
        await self.transaction.rollback()
        self.commited_or_rollbacked = True

# Create a new connection and start a transaction for it. All our operations, for now, are done through a transaction, even reading ones - to avoid mistakes and ease refactoring.
def transaction() -> Transaction:
    global _connection_pool
    if _connection_pool is None:
        raise Exception("No connection pool.")
    return Transaction(_connection_pool)


fetch: Callable
fetch_first: Callable
fetch_first_value: Callable


async def create_pool():
    # We panic any exceptions that occur here - without a connection we're not able to do anything useful anyway.

    host = core.config_get("postgres", "host", "localhost")
    port = int(core.config_get("postgres", "port", "5432"))

    database = core.config_get("postgres", "database", "postgres")

    user = core.config_get("postgres", "user", "postgres")
    password = core.config_get("postgres", "password", "1234")

    core.log_info("""Connect postgres:
{ind}Host:           {host}
{ind}Port:           {port}
{ind}User:           {user}
{ind}Database:       {database}
""".format(host=host, port=port, user=user, database=database, ind=indentation))
    global _connection_pool
    _connection_pool = await asyncpg.create_pool(
        user=user,
        password=password,
        database=database,
        host=host,
        port=port,
    )

    global fetch
    fetch = functools.partial(_connection_pool.fetch, record_class=Record)
    global fetch_first
    fetch_first = functools.partial(_connection_pool.fetchrow, record_class=Record)
    global fetch_first_value
    fetch_first_value = _connection_pool.fetchval

async def init():
    await create_pool()

async def deinit():
    if _connection_pool is not None:
        await _connection_pool.close()