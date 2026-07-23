from __future__ import annotations
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

async def paginate(session: AsyncSession, stmt, page: int, limit: int, transformer=None):
    """
    Executes a paginated SQLAlchemy query.
    - Runs a COUNT() query to get total rows.
    - Applies OFFSET and LIMIT to the provided statement.
    - Executes the paginated query and passes the result to `transformer` if provided.
    
    Returns a dictionary suitable for JSON serialization:
    { "items": [...], "total": N, "page": X, "pages": Y }
    """
    # Defensive programming: ensure minimum bounds
    page = max(1, page)
    limit = max(1, limit)

    # Strip ORDER BY from the count query — sorting all rows before counting
    # is wasteful and unnecessary. The subquery preserves WHERE/JOIN clauses
    # so the count reflects the actual result set.
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    if total == 0:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "pages": 0
        }

    offset = (page - 1) * limit
    paginated_stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(paginated_stmt)

    if transformer:
        items = transformer(result)
    else:
        # Default mapping fallback
        items = []
        for row in result:
            if len(row) == 1:
                items.append(row[0])
            else:
                items.append(dict(row._mapping))

    pages = (total + limit - 1) // limit

    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": pages
    }
