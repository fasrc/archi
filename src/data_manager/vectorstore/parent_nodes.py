"""Helper module for cleaning up orphaned document_parent_nodes rows (issue #411).

Exported helpers run their SQL on a caller-supplied cursor.  The caller owns the
transaction; none of these helpers call commit.
"""

PARENT_TABLE_EXISTS = "SELECT to_regclass('document_parent_nodes') IS NOT NULL"

DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT = """
DELETE FROM document_parent_nodes p
WHERE p.document_id = %s
  AND NOT EXISTS (
      SELECT 1 FROM document_chunks c
      WHERE c.metadata->>'parent_id' = p.id::text
  )
"""

DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE = """
DELETE FROM document_parent_nodes p
WHERE (
        p.document_id IN (SELECT d.id FROM documents d WHERE d.resource_hash = %s)
        OR (p.document_id IS NULL AND p.metadata->>'resource_hash' = %s)
      )
  AND NOT EXISTS (
      SELECT 1 FROM document_chunks c
      WHERE c.metadata->>'parent_id' = p.id::text
  )
"""

TRUNCATE_PARENT_NODES = "TRUNCATE TABLE document_parent_nodes"


def parent_table_exists(cursor) -> bool:
    cursor.execute(PARENT_TABLE_EXISTS)
    row = cursor.fetchone()
    return bool(row[0])


def delete_unreferenced_parents(cursor, document_id) -> int:
    if document_id is None:
        return 0
    cursor.execute(DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT, (document_id,))
    return max(0, cursor.rowcount)


def delete_unreferenced_parents_for_resource(cursor, resource_hash) -> int:
    cursor.execute(
        DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE, (resource_hash, resource_hash)
    )
    return max(0, cursor.rowcount)


def delete_unreferenced_parents_for_resources(cursor, resource_hashes) -> int:
    hashes = list(resource_hashes or ())
    if not hashes:
        return 0
    if not parent_table_exists(cursor):
        return 0
    return sum(delete_unreferenced_parents_for_resource(cursor, h) for h in hashes)


def truncate_parent_nodes(cursor) -> None:
    cursor.execute(TRUNCATE_PARENT_NODES)
