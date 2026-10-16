"""
Feed products and the feed plan.
"""

from __future__ import annotations

from typing import Dict, List

from autocann.store.connection import _open
from autocann.store.schema import ensure_schema

# ===========================================================================
# Feed chart and irrigation log
# ===========================================================================


def get_feed_plan() -> List[Dict]:
    """
    Every product with its dose grid, in display order.

    Shaped for `autocann.control.feeding`: `doses[stage][week] -> dose`.
    """
    try:
        ensure_schema()
        conn = _open(row_factory=True)
        products = conn.execute(
            "SELECT id, name, unit FROM feed_products ORDER BY position ASC, id ASC"
        ).fetchall()
        doses = conn.execute(
            "SELECT product_id, stage, week, dose FROM feed_plan"
        ).fetchall()
        conn.close()

        by_product: Dict[int, Dict[str, Dict[int, float]]] = {}
        for row in doses:
            by_product.setdefault(row["product_id"], {}).setdefault(row["stage"], {})[
                int(row["week"])
            ] = row["dose"]

        return [
            {
                "id": product["id"],
                "name": product["name"],
                "unit": product["unit"],
                "doses": by_product.get(product["id"], {}),
            }
            for product in products
        ]
    except Exception as e:
        print(f"Error getting feed plan: {e}")
        return []


def replace_feed_plan(products: List[Dict]) -> bool:
    """
    Overwrite the whole chart with `products`.

    A full replace rather than a diff: the editor sends the grid it is showing,
    and renames, reorders, additions and deletions all arrive together. Ids of
    surviving products are preserved so the irrigation log keeps pointing at
    them; a product without an id is new.

    Runs in one transaction, so a rejected row cannot leave half a chart.
    """
    try:
        ensure_schema()
        conn = _open()
        try:
            with conn:  # commits on success, rolls back on exception
                keep = [int(p["id"]) for p in products if p.get("id")]
                if keep:
                    marks = ",".join("?" * len(keep))
                    conn.execute(
                        f"DELETE FROM feed_products WHERE id NOT IN ({marks})", keep
                    )
                else:
                    conn.execute("DELETE FROM feed_products")
                # No ON DELETE CASCADE without PRAGMA foreign_keys, which is off
                # by default in sqlite; clear the orphans explicitly.
                conn.execute(
                    "DELETE FROM feed_plan WHERE product_id NOT IN "
                    "(SELECT id FROM feed_products)"
                )

                for position, product in enumerate(products):
                    name = str(product.get("name") or "").strip() or "Sin nombre"
                    unit = product.get("unit") if product.get("unit") in ("ml/L", "g/L") else "ml/L"
                    product_id = product.get("id")

                    if product_id:
                        conn.execute(
                            "UPDATE feed_products SET name = ?, unit = ?, position = ? WHERE id = ?",
                            (name, unit, position, int(product_id)),
                        )
                    else:
                        cursor = conn.execute(
                            "INSERT INTO feed_products (name, unit, position) VALUES (?, ?, ?)",
                            (name, unit, position),
                        )
                        product_id = cursor.lastrowid

                    conn.execute("DELETE FROM feed_plan WHERE product_id = ?", (product_id,))
                    rows = []
                    for stage, weeks in (product.get("doses") or {}).items():
                        for week, dose in (weeks or {}).items():
                            try:
                                dose = float(dose)
                                week = int(week)
                            except (TypeError, ValueError):
                                continue
                            if dose > 0 and week > 0:
                                rows.append((product_id, stage, week, dose))
                    if rows:
                        conn.executemany(
                            "INSERT INTO feed_plan (product_id, stage, week, dose) "
                            "VALUES (?, ?, ?, ?)",
                            rows,
                        )
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"Error replacing feed plan: {e}")
        return False
