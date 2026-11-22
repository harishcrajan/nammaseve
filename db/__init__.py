# db/__init__.py
from db.database import (
    init_db,
    insert_ticket,
    get_open_tickets,
    get_ticket_by_id,
    find_open_duplicate,
    increment_plus_one,
    update_ticket,
    mark_resolved,
)

__all__ = [
    "init_db",
    "insert_ticket",
    "get_open_tickets",
    "get_ticket_by_id",
    "find_open_duplicate",
    "increment_plus_one",
    "update_ticket",
    "mark_resolved",
]
