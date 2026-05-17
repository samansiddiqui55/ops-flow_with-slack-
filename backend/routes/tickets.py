# # from fastapi import APIRouter, HTTPException
# # from models.ticket import TicketCreate, TicketUpdate
# # from services.ticket_service import ticket_service

# # router = APIRouter(prefix="/tickets", tags=["Tickets"])


# # @router.get("/")
# # async def get_all_tickets():
# #     tickets = await db.issue_logs.find().to_list(length=100)

# #     for ticket in tickets:
# #         ticket["_id"] = str(ticket["_id"])  # 🔥 FIX HERE

# #     return tickets


# # @router.get("/{ticket_id}")
# # async def get_ticket(ticket_id: str):
# #     ticket = await ticket_service.get_ticket_by_id(ticket_id)
# #     if not ticket:
# #         raise HTTPException(status_code=404, detail="Ticket not found")
# #     return ticket


# # @router.post("/")
# # async def create_ticket(payload: TicketCreate):
# #     return await ticket_service.create_ticket(payload)


# # @router.patch("/{ticket_id}")
# # async def update_ticket(ticket_id: str, payload: TicketUpdate):
# #     ticket = await ticket_service.update_ticket(ticket_id, payload)
# #     if not ticket:
# #         raise HTTPException(status_code=404, detail="Ticket not found")
# #     return ticket


# # @router.post("/{ticket_id}/resolve")
# # async def resolve_ticket(ticket_id: str, payload: TicketUpdate):
# #     ticket = await ticket_service.resolve_ticket(
# #         ticket_id=ticket_id,
# #         latest_comment=payload.latest_comment or "",
# #         resolution_notes=payload.resolution_notes or ""
# #     )
# #     if not ticket:
# #         raise HTTPException(status_code=404, detail="Ticket not found")
# #     return ticket

# from fastapi import APIRouter, HTTPException
# from typing import List

# from models.ticket import TicketCreate, TicketUpdate
# from services.ticket_service import ticket_service

# router = APIRouter(prefix="/tickets", tags=["Tickets"])


# @router.post("/")
# async def create_ticket(payload: TicketCreate):
#     return await ticket_service.create_ticket(payload)


# @router.get("/")
# async def get_all_tickets():
#     return await ticket_service.get_all_tickets()


# @router.get("/{ticket_id}")
# async def get_ticket(ticket_id: str):
#     ticket = await ticket_service.get_ticket_by_id(ticket_id)
#     if not ticket:
#         raise HTTPException(status_code=404, detail="Ticket not found")
#     return ticket


# @router.put("/{ticket_id}")
# async def update_ticket(ticket_id: str, payload: TicketUpdate):
#     ticket = await ticket_service.update_ticket(ticket_id, payload)
#     if not ticket:
#         raise HTTPException(status_code=404, detail="Ticket not found")
#     return ticket


# @router.post("/{ticket_id}/resolve")
# async def resolve_ticket(ticket_id: str, payload: TicketUpdate):
#     ticket = await ticket_service.resolve_ticket(
#         ticket_id=ticket_id,
#         latest_comment=payload.latest_comment or "",
#         resolution_notes=payload.resolution_notes or ""
#     )

#     if not ticket:
#         raise HTTPException(status_code=404, detail="Ticket not found")

#     return {
#         "message": f"Ticket {ticket_id} resolved successfully",
#         "ticket": ticket
#     }


# # CHANGE 1: Manual reopen endpoint (used by Slack handler + dashboard)
# @router.post("/{ticket_id}/reopen")
# async def reopen_ticket(ticket_id: str, payload: TicketUpdate):
#     ticket = await ticket_service.reopen_ticket(
#         ticket_id=ticket_id,
#         latest_comment=payload.latest_comment or "",
#         actor="Dashboard",
#     )
#     if not ticket:
#         raise HTTPException(status_code=404, detail="Ticket not found")
#     return {"message": f"Ticket {ticket_id} reopened", "ticket": ticket}

from fastapi import APIRouter, HTTPException
from typing import List
from pydantic import BaseModel

from models.ticket import TicketCreate, TicketUpdate
from services.ticket_service import ticket_service
from services.slack_service import slack_service

router = APIRouter(prefix="/tickets", tags=["Tickets"])


class ReplyPayload(BaseModel):
    message: str


@router.post("/")
async def create_ticket(payload: TicketCreate):
    return await ticket_service.create_ticket(payload)


@router.get("/")
async def get_all_tickets():
    return await ticket_service.get_all_tickets()


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: str):
    ticket = await ticket_service.get_ticket_by_id(ticket_id)

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return ticket


@router.put("/{ticket_id}")
async def update_ticket(ticket_id: str, payload: TicketUpdate):
    ticket = await ticket_service.update_ticket(ticket_id, payload)

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return ticket


@router.post("/{ticket_id}/resolve")
async def resolve_ticket(ticket_id: str, payload: TicketUpdate):

    ticket = await ticket_service.resolve_ticket(
        ticket_id=ticket_id,
        latest_comment=payload.latest_comment or "",
        resolution_notes=payload.resolution_notes or ""
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return {
        "message": f"Ticket {ticket_id} resolved successfully",
        "ticket": ticket
    }


# Existing reopen endpoint
@router.post("/{ticket_id}/reopen")
async def reopen_ticket(ticket_id: str, payload: TicketUpdate):

    ticket = await ticket_service.reopen_ticket(
        ticket_id=ticket_id,
        latest_comment=payload.latest_comment or "",
        actor="Dashboard",
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return {
        "message": f"Ticket {ticket_id} reopened",
        "ticket": ticket
    }


# ===============================
# NEW: Reply from Dashboard → Slack Thread
# ===============================
@router.post("/{ticket_id}/reply")
async def reply_to_ticket(ticket_id: str, payload: ReplyPayload):

    ticket = await ticket_service.get_ticket_by_id(ticket_id)

    if not ticket:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    # stored on ticket already
    channel_id = ticket.get("slack_channel_id")
    thread_ts = ticket.get("slack_thread_ts")

    if not channel_id or not thread_ts:
        raise HTTPException(
            status_code=400,
            detail="Slack thread/channel missing"
        )

    success = await slack_service.post_message(
        channel_id=channel_id,
        text=payload.message,
        thread_ts=thread_ts
    )

    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed sending Slack reply"
        )

    # save comment in activity history
    await ticket_service.append_activity(
        ticket_id=ticket_id,
        event="comment",
        message=payload.message,
        actor="Dashboard User"
    )

    updated = await ticket_service.get_ticket_by_id(ticket_id)

    return {
        "success": True,
        "ticket": updated
    }