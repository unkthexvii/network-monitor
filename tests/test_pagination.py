"""Tests for core/pagination.py — paginate() with in-memory SQLite."""
import pytest
import pytest_asyncio
from sqlalchemy import Column, Integer, String, select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


# ── Minimal test model ──

class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "test_items"
    id = Column(Integer, primary_key=True)
    name = Column(String)


# ── Fixture: in-memory async SQLite session ──

@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


# ── Helpers ──

async def _seed(session, count):
    """Insert *count* rows into test_items."""
    for i in range(1, count + 1):
        session.add(Item(name=f"item_{i:03d}"))
    await session.commit()


# ── Tests ──

@pytest.mark.asyncio
async def test_empty_result_set(session):
    """paginate() on an empty table returns no items and total=0."""
    from core.pagination import paginate
    stmt = select(Item)
    result = await paginate(session, stmt, page=1, limit=10)

    assert result["items"] == []
    assert result["total"] == 0
    assert result["page"] == 1
    assert result["pages"] == 0


@pytest.mark.asyncio
async def test_single_page(session):
    """All rows fit on one page."""
    from core.pagination import paginate
    await _seed(session, 5)

    stmt = select(Item)
    result = await paginate(session, stmt, page=1, limit=10)

    assert result["total"] == 5
    assert result["pages"] == 1
    assert len(result["items"]) == 5
    assert result["page"] == 1


@pytest.mark.asyncio
async def test_multiple_pages(session):
    """Rows span multiple pages with correct page count."""
    from core.pagination import paginate
    await _seed(session, 10)

    stmt = select(Item)

    # Page 1 of 3 (limit=4 -> ceil(10/4) = 3)
    p1 = await paginate(session, stmt, page=1, limit=4)
    assert p1["total"] == 10
    assert p1["pages"] == 3
    assert len(p1["items"]) == 4

    # Page 2
    p2 = await paginate(session, stmt, page=2, limit=4)
    assert len(p2["items"]) == 4
    assert p2["page"] == 2

    # Page 3 (only 2 remaining)
    p3 = await paginate(session, stmt, page=3, limit=4)
    assert len(p3["items"]) == 2
    assert p3["page"] == 3


@pytest.mark.asyncio
async def test_page_zero_treated_as_page_one(session):
    """page=0 is clamped to 1 via max(1, page)."""
    from core.pagination import paginate
    await _seed(session, 3)

    stmt = select(Item)
    result = await paginate(session, stmt, page=0, limit=10)

    # page should be clamped to 1
    assert result["page"] == 1
    assert result["total"] == 3


@pytest.mark.asyncio
async def test_negative_page_treated_as_page_one(session):
    """Negative page numbers are clamped to 1."""
    from core.pagination import paginate
    await _seed(session, 3)

    stmt = select(Item)
    result = await paginate(session, stmt, page=-5, limit=10)

    assert result["page"] == 1


@pytest.mark.asyncio
async def test_limit_zero_treated_as_limit_one(session):
    """limit=0 is clamped to 1 via max(1, limit)."""
    from core.pagination import paginate
    await _seed(session, 5)

    stmt = select(Item)
    result = await paginate(session, stmt, page=1, limit=0)

    # With limit clamped to 1, we get 5 pages for 5 items
    assert result["pages"] == 5
    assert len(result["items"]) == 1
    assert result["total"] == 5


@pytest.mark.asyncio
async def test_negative_limit_treated_as_limit_one(session):
    """Negative limit values are clamped to 1."""
    from core.pagination import paginate
    await _seed(session, 5)

    stmt = select(Item)
    result = await paginate(session, stmt, page=1, limit=-10)

    assert result["pages"] == 5
    assert len(result["items"]) == 1


@pytest.mark.asyncio
async def test_transformer_called(session):
    """When a transformer function is provided it receives the result and its return value is used as items."""
    from core.pagination import paginate
    await _seed(session, 3)

    def my_transformer(result):
        return [row[0].name.upper() for row in result]

    stmt = select(Item)
    result = await paginate(session, stmt, page=1, limit=10, transformer=my_transformer)

    assert result["items"] == ["ITEM_001", "ITEM_002", "ITEM_003"]


@pytest.mark.asyncio
async def test_transformer_empty_result(session):
    """Transformer is not called when total is 0 (early return)."""
    from core.pagination import paginate
    called = []

    def my_transformer(result):
        called.append(True)
        return list(result)

    stmt = select(Item)
    result = await paginate(session, stmt, page=1, limit=10, transformer=my_transformer)

    assert result["items"] == []
    assert called == []  # transformer was NOT called


@pytest.mark.asyncio
async def test_default_row_mapping(session):
    """Without a transformer, rows are converted to dicts via _mapping."""
    from core.pagination import paginate
    await _seed(session, 2)

    stmt = select(Item.id, Item.name)
    result = await paginate(session, stmt, page=1, limit=10)

    # Multi-column query -> dict mapping
    assert len(result["items"]) == 2
    assert result["items"][0]["name"] == "item_001"


@pytest.mark.asyncio
async def test_single_column_returns_values(session):
    """Single-column select returns plain values (len(row)==1 path)."""
    from core.pagination import paginate
    await _seed(session, 3)

    stmt = select(Item.name)
    result = await paginate(session, stmt, page=1, limit=10)

    assert result["items"] == ["item_001", "item_002", "item_003"]
