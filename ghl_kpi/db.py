"""Storage through SQLAlchemy, so the same code runs on SQLite (default) or Postgres.

Current-state tables (contacts, opportunities, stages, appointments) are upserted
on every sync. `opportunity_snapshots` keeps one row per opportunity per sync, so
stage history builds up over time even though the API only returns the latest stage.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Store naive UTC, hand back aware UTC. SQLite drops tzinfo otherwise."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    pass


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_id: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / ok / failed
    error: Mapped[str] = mapped_column(Text, nullable=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)


class Stage(Base):
    __tablename__ = "stages"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(String(64))
    pipeline_name: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer)


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    location_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=True)
    source: Mapped[str] = mapped_column(String(200), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    date_added: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    first_outbound_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    speed_checked: Mapped[bool] = mapped_column(Boolean, default=False)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contact_id: Mapped[str] = mapped_column(String(64), nullable=True)
    pipeline_id: Mapped[str] = mapped_column(String(64), nullable=True)
    stage_id: Mapped[str] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=True)
    monetary_value: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    last_stage_change_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    last_status_change_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)


class OpportunitySnapshot(Base):
    __tablename__ = "opportunity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id"))
    opportunity_id: Mapped[str] = mapped_column(String(64), index=True)
    stage_id: Mapped[str] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=True)
    monetary_value: Mapped[float] = mapped_column(Float, default=0.0)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(64), nullable=True)
    contact_id: Mapped[str] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=True)
    start_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    date_added: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)


def make_session_factory(url: str) -> sessionmaker:
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def last_run(session: Session, ok_only: bool = False):
    q = select(SyncRun).order_by(SyncRun.id.desc())
    if ok_only:
        q = q.where(SyncRun.status == "ok")
    return session.scalars(q.limit(1)).first()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
