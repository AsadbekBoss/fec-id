"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

# scope="function": the transaction is committed (or rolled back) when the endpoint function
# returns, BEFORE the response is sent. A commit failure therefore becomes a 500 instead of a
# 200 for data that was never saved.
DbSession = Annotated[AsyncSession, Depends(get_session, scope="function")]
