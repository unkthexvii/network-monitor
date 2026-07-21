"""Tests for core/ping_buffer.py — async ping buffer."""
import asyncio
import pytest
from core.ping_buffer import PingBuffer, PingSample


@pytest.fixture
def buf():
    return PingBuffer()


def sample(status="ONLINE", latency=10.0, loss=0.0):
    return PingSample(status=status, latency_ms=latency, packet_loss=loss)


@pytest.mark.asyncio
async def test_append_and_flush(buf):
    await buf.append(1, sample())
    snap = await buf.flush()
    assert 1 in snap
    assert len(snap[1]) == 1
    assert snap[1][0].status == "ONLINE"


@pytest.mark.asyncio
async def test_flush_clears_buffer(buf):
    await buf.append(1, sample())
    await buf.flush()
    snap = await buf.flush()
    assert len(snap) == 0


@pytest.mark.asyncio
async def test_append_batch(buf):
    batch = {1: sample("ONLINE", 5.0), 2: sample("OFFLINE", 0.0, 1.0)}
    await buf.append_batch(batch)
    snap = await buf.flush()
    assert len(snap) == 2
    assert snap[1][0].latency_ms == 5.0
    assert snap[2][0].status == "OFFLINE"


@pytest.mark.asyncio
async def test_max_samples_trims(buf):
    # Add more than MAX_SAMPLES_PER_DEVICE
    for i in range(buf.MAX_SAMPLES_PER_DEVICE + 10):
        await buf.append(1, sample(latency=float(i)))
    snap = await buf.flush()
    assert len(snap[1]) == buf.MAX_SAMPLES_PER_DEVICE
    # Oldest samples trimmed — first remaining should be sample #10
    assert snap[1][0].latency_ms == 10.0


@pytest.mark.asyncio
async def test_clear_device(buf):
    await buf.append(1, sample())
    await buf.append(2, sample())
    await buf.clear_device(1)
    snap = await buf.flush()
    assert 1 not in snap
    assert 2 in snap


@pytest.mark.asyncio
async def test_clear_nonexistent_device(buf):
    await buf.clear_device(999)  # should not raise


@pytest.mark.asyncio
async def test_multiple_devices(buf):
    await buf.append(1, sample("ONLINE", 1.0))
    await buf.append(2, sample("OFFLINE", 0.0, 1.0))
    await buf.append(1, sample("ONLINE", 2.0))
    snap = await buf.flush()
    assert len(snap[1]) == 2
    assert len(snap[2]) == 1


@pytest.mark.asyncio
async def test_ping_sample_fields():
    s = PingSample(status="ONLINE", latency_ms=42.5, packet_loss=0.05)
    assert s.status == "ONLINE"
    assert s.latency_ms == 42.5
    assert s.packet_loss == 0.05
