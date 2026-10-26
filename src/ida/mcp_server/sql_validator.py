"""Static allow-list check for model-generated SQL (first guardrail layer).

The DB connection is also read-only, so this is defence in depth: it rejects
anything that is not a single read query over known tables before it runs.
"""
import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"machines", "sensors", "readings", "alarms", "maintenance_events"}

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Pragma, exp.Attach, exp.Detach, exp.Command, exp.Transaction, exp.Commit, exp.Set,
)
FORBIDDEN_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class SQLValidationError(ValueError):
    pass


def validate_sql(sql: str) -> str:
    """Return the SQL (stripped, without trailing ';') if it is a safe read query, else raise."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SQLValidationError("Empty SQL.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLValidationError(f"SQL does not parse: {e}") from e
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")

    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SQLValidationError(f"Only SELECT queries are allowed (got {type(tree).__name__}).")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLValidationError(f"Forbidden statement: {type(node).__name__}.")
        if isinstance(node, exp.Anonymous) and node.name.lower() in FORBIDDEN_FUNCTIONS:
            raise SQLValidationError(f"Forbidden function: {node.name}.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    unknown = {t.name.lower() for t in tree.find_all(exp.Table)} - ALLOWED_TABLES - cte_names
    if unknown:
        raise SQLValidationError(f"Unknown table(s): {', '.join(sorted(unknown))}.")
    return sql
