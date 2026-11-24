from datetime import date, datetime
from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import Status
from src.utils.time import now

BOARD_ID = 1

class WeekSnapshot(Base):
    __tablename__ = 'week_snapshots'
    __table_args__ = (UniqueConstraint('week_start', 'revision'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(index=True)
    revision: Mapped[int]
    rows: Mapped[list] = mapped_column(JSON)
    row_count: Mapped[int]
    checksum: Mapped[str] = mapped_column(String(64))
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

class MigrationBoard(Base):
    __tablename__ = 'migration_board'

    id: Mapped[int] = mapped_column(primary_key=True)
    week1: Mapped[date]
    week2: Mapped[date]
    week3: Mapped[date]
    params: Mapped[dict] = mapped_column(JSON)
    generation: Mapped[int] = mapped_column(default=0)
    current_report_id: Mapped[int | None] = mapped_column(ForeignKey('migration_reports.id'))
    pub_rev1: Mapped[int] = mapped_column(default=0)
    pub_rev2: Mapped[int] = mapped_column(default=0)
    pub_rev3: Mapped[int] = mapped_column(default=0)
    pub_generation: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=now)

class MigrationReport(Base):
    __tablename__ = 'migration_reports'

    id: Mapped[int] = mapped_column(primary_key=True)
    week1: Mapped[date]
    week2: Mapped[date]
    week3: Mapped[date]
    rev1: Mapped[int]
    rev2: Mapped[int]
    rev3: Mapped[int]
    params: Mapped[dict] = mapped_column(JSON)
    generation: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default=Status.QUEUED, index=True)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None]
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]

class ShiftSuggestion(Base):
    __tablename__ = 'shift_suggestions'

    id: Mapped[int] = mapped_column(primary_key=True)
    coach_id: Mapped[str] = mapped_column(String(64))
    target_week: Mapped[date]
    action: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(String(500))
    report_id: Mapped[int] = mapped_column(ForeignKey('migration_reports.id'))
    basis: Mapped[dict] = mapped_column(JSON)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
