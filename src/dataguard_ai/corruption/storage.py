"""SQL operations whose write targets are restricted to owned experiment schemas."""

import hashlib
import json
import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from dataguard_ai.corruption.planning import MUTATIONS
from dataguard_ai.quality.rules import PRIMARY_KEYS

OWNER = "DataGuard AI pilot-v1 owned experiment"


def require_experiment_schema(schema: str) -> None:
    if not re.fullmatch(r"experiment_[a-z0-9_]{1,40}", schema):
        raise ValueError("Writes require an experiment_<name> schema; raw is forbidden")


def source_checksums(
    connection: psycopg.Connection, schema: str, tables: list[str]
) -> dict:
    """Order-independent count/content guards; hashtextextended is not cryptographic."""
    results = {}
    for table in tables:
        row = connection.execute(
            sql.SQL(
                "SELECT COUNT(*), "
                "COALESCE(SUM(hashtextextended(to_jsonb(t)::text, 0)::numeric), 0) "
                "FROM {} AS t"
            ).format(sql.Identifier(schema, table))
        ).fetchone()
        results[table] = {"rows": row[0], "content_checksum": str(row[1])}
    return results


def reset_owned_schema(
    connection: psycopg.Connection, schema: str, source: str
) -> dict:
    """Transactional replacement without CASCADE or changes to source tables."""
    require_experiment_schema(schema)
    existing = connection.execute(
        "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace "
        "WHERE nspname = %s",
        (schema,),
    ).fetchone()
    if existing is not None and existing[0] != OWNER:
        raise ValueError(
            "Refusing to replace an experiment schema without our ownership marker"
        )
    if existing is None:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(
            sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
                sql.Identifier(schema),
                sql.Literal(OWNER),
            )
        )
    columns = {}
    for table in PRIMARY_KEYS:
        connection.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} "
                "(LIKE {}, _experiment_row_id TEXT PRIMARY KEY)"
            ).format(sql.Identifier(schema, table), sql.Identifier(source, table))
        )
        # pg_attribute also works for session TEMP source tables.
        attributes = connection.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass "
            "AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
            (f'"{source}"."{table}"',),
        ).fetchall()
        columns[table] = [row[0] for row in attributes]
    connection.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {} "
            "(mutation_index INTEGER PRIMARY KEY, details JSONB NOT NULL)"
        ).format(sql.Identifier(schema, "_mutations"))
    )
    connection.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {} "
            "(experiment_id TEXT PRIMARY KEY, details JSONB NOT NULL)"
        ).format(sql.Identifier(schema, "_run"))
    )
    targets = [*PRIMARY_KEYS, "_mutations", "_run"]
    connection.execute(
        sql.SQL("TRUNCATE TABLE {}").format(
            sql.SQL(", ").join(sql.Identifier(schema, table) for table in targets),
        )
    )
    return columns


def previous_run(connection: psycopg.Connection, schema: str) -> dict | None:
    require_experiment_schema(schema)
    marker = connection.execute(
        "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace "
        "WHERE nspname = %s",
        (schema,),
    ).fetchone()
    if marker is None or marker[0] != OWNER:
        return None
    row = connection.execute(
        sql.SQL("SELECT details FROM {}").format(
            sql.Identifier(schema, "_run"),
        )
    ).fetchone()
    return None if row is None else row[0]


def copy_pilot(
    connection: psycopg.Connection,
    schema: str,
    source: str,
    columns: dict,
    order_ids: list[str],
) -> None:
    require_experiment_schema(schema)
    selections = {
        "orders": ("order_id", None),
        "order_items": ("order_id", None),
        "order_payments": ("order_id", None),
        "order_reviews": ("order_id", None),
        "customers": ("customer_id", ("orders", "customer_id")),
        "products": ("product_id", ("order_items", "product_id")),
        "sellers": ("seller_id", ("order_items", "seller_id")),
        "category_translation": (None, None),
    }
    for table, (column, parent) in selections.items():
        values = sql.SQL(", ").join(
            sql.Identifier("s", name) for name in columns[table]
        )
        order = sql.SQL(", ").join(
            sql.SQL('{} COLLATE "C" NULLS FIRST').format(sql.Identifier("s", name))
            for name in columns[table]
        )
        params = ()
        if column is None:
            predicate = sql.SQL("TRUE")
        elif parent is None:
            predicate = sql.SQL("{} = ANY(%s)").format(sql.Identifier("s", column))
            params = (order_ids,)
        else:
            predicate = sql.SQL("EXISTS (SELECT 1 FROM {} p WHERE {} = {})").format(
                sql.Identifier(schema, parent[0]),
                sql.Identifier("p", parent[1]),
                sql.Identifier("s", column),
            )
        query = sql.SQL(
            "INSERT INTO {} ({}, _experiment_row_id) "
            "SELECT {}, %s || lpad((row_number() OVER (ORDER BY {}))::text, 8, '0') "
            "FROM {} s WHERE {}"
        ).format(
            sql.Identifier(schema, table),
            sql.SQL(", ").join(sql.Identifier(name) for name in columns[table]),
            values,
            order,
            sql.Identifier(source, table),
            predicate,
        )
        connection.execute(query, (f"{table}:", *params))


def content_digest(connection: psycopg.Connection, schema: str) -> str:
    """Canonical SHA-256 of the small pilot, including stable row IDs and duplicates."""
    digest = hashlib.sha256()
    for table in PRIMARY_KEYS:
        digest.update((table + "\n").encode())
        with connection.cursor(name="pilot_digest", row_factory=dict_row) as cursor:
            cursor.execute(
                sql.SQL("SELECT * FROM {} ORDER BY _experiment_row_id").format(
                    sql.Identifier(schema, table),
                )
            )
            for row in cursor:
                digest.update(
                    (
                        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
                    ).encode()
                )
    return digest.hexdigest()


def apply_mutations(
    connection: psycopg.Connection, schema: str, columns: dict, manifest: list
) -> None:
    require_experiment_schema(schema)
    for index, event in enumerate(manifest, 1):
        table, field, _ = MUTATIONS[event["mutation_type"]]
        if (table, field) != (event["target_table"], event["field"]):
            raise ValueError("Manifest target does not match its mutation contract")
        if field == "__row__":
            names = sql.SQL(", ").join(sql.Identifier(name) for name in columns[table])
            cursor = connection.execute(
                sql.SQL(
                    "INSERT INTO {} ({}, _experiment_row_id) SELECT {}, %s FROM {} "
                    "WHERE _experiment_row_id = %s"
                ).format(
                    sql.Identifier(schema, table),
                    names,
                    names,
                    sql.Identifier(schema, table),
                ),
                (event["row_locator"], event["source_row_locator"]),
            )
        else:
            cursor = connection.execute(
                sql.SQL(
                    "UPDATE {} SET {} = %s WHERE _experiment_row_id = %s "
                    "AND {} IS NOT DISTINCT FROM %s"
                ).format(
                    sql.Identifier(schema, table),
                    sql.Identifier(field),
                    sql.Identifier(field),
                ),
                (event["mutated_value"], event["row_locator"], event["original_value"]),
            )
        if cursor.rowcount != 1:
            raise ValueError("Mutation did not affect exactly one experiment row")
        connection.execute(
            sql.SQL("INSERT INTO {} VALUES (%s, %s)").format(
                sql.Identifier(schema, "_mutations"),
            ),
            (index, Jsonb(event)),
        )
