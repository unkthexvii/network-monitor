from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pydantic import BaseModel, Field
from typing import List, Optional

from database.session import get_db
from database.models import Device, DeviceStatus, TopologyLink, TopologyNode, TopologyTab
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class NodePos(BaseModel):
    id: int
    x: Optional[float] = None
    y: Optional[float] = None

class EdgeData(BaseModel):
    from_: int = Field(alias='from')
    to: int
    label: str = ""

class TopologySaveReq(BaseModel):
    tab_id: int
    nodes: List[NodePos]
    edges: List[EdgeData]

class TabCreateReq(BaseModel):
    name: str

@router.get("/api/topology")
async def get_topology(session: AsyncSession = Depends(get_db)):
    """
    Returns tabs, and all nodes/links grouped by tab.
    """
    tabs_result = await session.execute(select(TopologyTab))
    tabs = tabs_result.scalars().all()
    if not tabs:
        default_tab = TopologyTab(name="Default")
        session.add(default_tab)
        await session.commit()
        await session.refresh(default_tab)
        tabs = [default_tab]

    dev_stmt = select(TopologyNode, Device, DeviceStatus).join(
        Device, TopologyNode.device_id == Device.id
    ).outerjoin(
        DeviceStatus, Device.id == DeviceStatus.device_id
    )
    dev_result = await session.execute(dev_stmt)
    
    nodes = []
    for node_rec, device, status_rec in dev_result:
        status = status_rec.status if status_rec else "UNKNOWN"
        color = "#28a745"
        if status == "OFFLINE":
            color = "#dc3545"
        elif status == "UNKNOWN":
            color = "#6c757d"
            
        dt = (device.device_type or "").lower()
        icon_code = "\uf6a6" # default pc-display / workstation
        
        if "server" in dt:
            icon_code = "\uf52c"
        elif "router" in dt or "switch" in dt:
            icon_code = "\uf6ec" if "router" in dt else "\uf40d"
        elif "firewall" in dt:
            icon_code = "\uf538"
        elif "database" in dt:
            icon_code = "\uf8c4"
        elif "storage" in dt or "nas" in dt:
            icon_code = "\uf412"
        elif "access point" in dt or "wifi" in dt:
            icon_code = "\uf61c"
        elif "laptop" in dt:
            icon_code = "\uf456"
        elif "printer" in dt:
            icon_code = "\uf501"
        elif "camera" in dt:
            icon_code = "\uf21f"
        elif "iot" in dt:
            icon_code = "\uf2d6"

        node_data = {
            "tab_id": node_rec.tab_id,
            "id": device.id,
            "label": f"{device.name}\n{device.ip_address}",
            "color": color,
            "title": f"Remark: {device.remark}\nType: {device.device_type} | Status: {status}" if device.remark else f"Type: {device.device_type} | Status: {status}",
            "shape": "icon",
            "icon": {
                "face": "bootstrap-icons",
                "code": icon_code,
                "size": 50,
                "color": color
            },
            "x": node_rec.pos_x,
            "y": node_rec.pos_y
        }
        nodes.append(node_data)
        
    link_stmt = select(TopologyLink)
    link_result = await session.execute(link_stmt)
    
    edges = []
    for link in link_result.scalars().all():
        edges.append({
            "id": link.id,
            "tab_id": link.tab_id,
            "from": link.parent_device_id,
            "to": link.child_device_id,
            "label": link.link_type
        })
        
    return {
        "tabs": [{"id": t.id, "name": t.name} for t in tabs],
        "nodes": nodes,
        "edges": edges
    }

@router.post("/api/topology/tab")
async def create_topology_tab(req: TabCreateReq, session: AsyncSession = Depends(get_db)):
    new_tab = TopologyTab(name=req.name)
    session.add(new_tab)
    await session.commit()
    await session.refresh(new_tab)
    return {"id": new_tab.id, "name": new_tab.name}

@router.post("/api/topology/save")
async def save_topology(req: TopologySaveReq, session: AsyncSession = Depends(get_db)):
    await session.execute(delete(TopologyNode).where(TopologyNode.tab_id == req.tab_id))
    await session.execute(delete(TopologyLink).where(TopologyLink.tab_id == req.tab_id))
    
    for n in req.nodes:
        if n.x is not None and n.y is not None:
            new_node = TopologyNode(tab_id=req.tab_id, device_id=n.id, pos_x=n.x, pos_y=n.y)
            session.add(new_node)
            
    for e in req.edges:
        new_link = TopologyLink(tab_id=req.tab_id, parent_device_id=e.from_, child_device_id=e.to, link_type=e.label)
        session.add(new_link)
        
    await session.commit()
    return {"status": "ok"}

@router.delete("/api/topology/tab/{tab_id}")
async def delete_topology_tab(tab_id: int, session: AsyncSession = Depends(get_db)):
    """Delete a topology tab and its nodes/links."""
    await session.execute(delete(TopologyNode).where(TopologyNode.tab_id == tab_id))
    await session.execute(delete(TopologyLink).where(TopologyLink.tab_id == tab_id))
    await session.execute(delete(TopologyTab).where(TopologyTab.id == tab_id))
    await session.commit()
    return {"status": "deleted"}
