"""Neo4j driver lifecycle and health verification.

The driver is created at startup and closed at shutdown, like the Postgres
pool. Connectivity is *verified* at startup rather than assumed, so a bad URI
or a wrong password fails the deployment instead of failing the first document
somebody tries to ingest.

Neo4j has no row-level security. Isolation on this path is enforced entirely in
application code: every node carries ``workspace_id`` and every query filters
on it. That is why the Cypher lives in templates under ``app/ai/graph`` rather
than being assembled at call sites.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.core.config import Settings
from app.core.exceptions import GraphError

logger: Final = logging.getLogger(__name__)


class Neo4jDatabase:
    """Owns the Neo4j driver for the process."""

    def __init__(self, settings: Settings) -> None:
        self._uri = settings.neo4j_uri
        self._auth = (
            settings.neo4j_username,
            settings.neo4j_password.get_secret_value(),
        )
        self._database = settings.neo4j_database
        self._timeout = settings.neo4j_timeout_seconds
        self._driver: AsyncDriver | None = None

    @property
    def database(self) -> str:
        return self._database

    @property
    def driver(self) -> AsyncDriver:
        """The live driver.

        Raises:
            RuntimeError: accessed before :meth:`connect`. A wiring bug -- the
                application lifespan did not run -- rather than a condition to
                recover from.
        """
        if self._driver is None:
            raise RuntimeError("Neo4j driver has not been initialised")
        return self._driver

    @property
    def is_connected(self) -> bool:
        return self._driver is not None

    async def connect(self) -> None:
        if self._driver is not None:
            return
        logger.info("Opening Neo4j driver for %s", self._uri)
        self._driver = AsyncGraphDatabase.driver(
            self._uri,
            auth=self._auth,
            connection_acquisition_timeout=self._timeout,
        )

    async def disconnect(self) -> None:
        if self._driver is None:
            return
        logger.info("Closing Neo4j driver")
        await self._driver.close()
        self._driver = None

    async def verify(self) -> None:
        """Check that the database is reachable and will answer a query.

        ``verify_connectivity`` alone proves a socket and a handshake;
        it does not prove the configured database exists or that the
        credentials can read it. Running a trivial query does, which is the
        difference between a health check that passes while the service is
        broken and one that does not.

        Raises:
            GraphError: the database is not usable.
        """
        try:
            await self.driver.verify_connectivity()
            async with self.driver.session(database=self._database) as session:
                result = await session.run("RETURN 1 AS ok")
                record = await result.single()
        except RuntimeError:
            raise
        except Exception as exc:
            # The driver raises a wide family of its own exceptions; the
            # message can carry the URI and the user, so only the type is
            # surfaced and the detail goes to the log.
            logger.exception("Neo4j verification failed")
            raise GraphError("verification", type(exc).__name__) from exc

        if record is None or record.get("ok") != 1:
            raise GraphError("verification", "unexpected response")

    async def execute_write(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a write query in a managed transaction."""
        return await self._execute(query, parameters, write=True)

    async def execute_read(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a read query in a read-only transaction.

        Read paths use a read transaction so that a mistake in a template --
        or a future one built from a model's output -- cannot mutate the
        graph.
        """
        return await self._execute(query, parameters, write=False)

    async def _execute(
        self, query: str, parameters: dict[str, Any] | None, *, write: bool
    ) -> list[dict[str, Any]]:
        operation = "write" if write else "read"
        try:
            async with self.driver.session(database=self._database) as session:
                runner = session.execute_write if write else session.execute_read

                async def work(tx: Any) -> list[dict[str, Any]]:
                    result = await tx.run(query, parameters or {})
                    return [record.data() async for record in result]

                return await runner(work)
        except RuntimeError:
            raise
        except Exception as exc:
            logger.exception("Neo4j %s failed", operation)
            raise GraphError(operation, type(exc).__name__) from exc
