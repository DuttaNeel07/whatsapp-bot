"""Persistence for SIH 2026 problem-statement counts and 300-submission alerts.

Paste these model classes into db/models.py (next to FellowshipAlert):

    class SIHProblemStatement(Base):
        __tablename__ = "sih_problem_statements"

        ps_number: Mapped[str] = mapped_column(String(64), primary_key=True)
        serial_number: Mapped[str] = mapped_column(String(32), nullable=False, default="")
        organization: Mapped[str] = mapped_column(Text, nullable=False, default="")
        title: Mapped[str] = mapped_column(Text, nullable=False, default="")
        category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
        theme: Mapped[str] = mapped_column(String(128), nullable=False, default="")
        submitted_ideas_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
        submitted_ideas_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
        deadline: Mapped[str] = mapped_column(String(64), nullable=False, default="")
        last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class SIHAlert(Base):
        __tablename__ = "sih_alerts"
        __table_args__ = (UniqueConstraint("ps_number", "threshold", name="uq_sih_alert_ps_threshold"),)

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        ps_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
        title: Mapped[str] = mapped_column(Text, nullable=False, default="")
        submitted_ideas_count: Mapped[int] = mapped_column(Integer, nullable=False)
        threshold: Mapped[int] = mapped_column(Integer, nullable=False)
        sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class SIHSnapshot(Base):
        __tablename__ = "sih_snapshots"

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
        source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
        total: Mapped[int] = mapped_column(Integer, nullable=False)
        over_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SIHAlert, SIHProblemStatement, SIHSnapshot


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SIHStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            for row in rows:
                ps_number = (row.get("ps_number") or "").strip()
                if not ps_number:
                    continue
                existing = session.get(SIHProblemStatement, ps_number)
                count = int(row.get("submitted_ideas_count") or 0)
                limit = _optional_int(row.get("submitted_ideas_limit"))
                deadline = row.get("deadline") or ""
                if existing is None:
                    session.add(
                        SIHProblemStatement(
                            ps_number=ps_number,
                            serial_number=row.get("serial_number") or "",
                            organization=row.get("organization") or "",
                            title=row.get("title") or "",
                            category=row.get("category") or "",
                            theme=row.get("theme") or "",
                            submitted_ideas_count=count,
                            submitted_ideas_limit=limit,
                            deadline=deadline,
                            last_seen_at=now,
                            updated_at=now,
                        )
                    )
                    continue
                existing.serial_number = row.get("serial_number") or existing.serial_number
                existing.organization = row.get("organization") or existing.organization
                existing.title = row.get("title") or existing.title
                existing.category = row.get("category") or existing.category
                existing.theme = row.get("theme") or existing.theme
                existing.submitted_ideas_count = count
                existing.submitted_ideas_limit = limit if limit is not None else existing.submitted_ideas_limit
                existing.deadline = deadline or existing.deadline
                existing.last_seen_at = now
                existing.updated_at = now

    def record_snapshot(self, source_url: str, total: int, over_threshold: int) -> None:
        with self.session_factory.begin() as session:
            session.add(
                SIHSnapshot(
                    fetched_at=datetime.now(timezone.utc),
                    source_url=source_url or "",
                    total=total,
                    over_threshold=over_threshold,
                )
            )

    def list_problem_statements(self, min_count: int | None = None) -> list[dict[str, Any]]:
        stmt = select(SIHProblemStatement).order_by(
            SIHProblemStatement.submitted_ideas_count.desc(),
            SIHProblemStatement.ps_number.asc(),
        )
        if min_count is not None:
            stmt = stmt.where(SIHProblemStatement.submitted_ideas_count >= min_count)
        with self.session_factory() as session:
            return [self._to_dict(row) for row in session.scalars(stmt).all()]

    def get(self, ps_number: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(SIHProblemStatement, ps_number.strip())
            return self._to_dict(row) if row is not None else None

    def pending_alerts(self, threshold: int) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            alerted = {
                row.ps_number
                for row in session.scalars(
                    select(SIHAlert).where(SIHAlert.threshold == threshold)
                ).all()
            }
            rows = session.scalars(
                select(SIHProblemStatement)
                .where(SIHProblemStatement.submitted_ideas_count >= threshold)
                .order_by(SIHProblemStatement.submitted_ideas_count.desc())
            ).all()
            return [self._to_dict(row) for row in rows if row.ps_number not in alerted]

    def mark_alerted(self, ps_number: str, title: str, count: int, threshold: int) -> None:
        with self.session_factory.begin() as session:
            existing = session.scalars(
                select(SIHAlert).where(
                    SIHAlert.ps_number == ps_number,
                    SIHAlert.threshold == threshold,
                )
            ).first()
            if existing is not None:
                return
            session.add(
                SIHAlert(
                    ps_number=ps_number,
                    title=title or "",
                    submitted_ideas_count=count,
                    threshold=threshold,
                    sent_at=datetime.now(timezone.utc),
                )
            )

    @staticmethod
    def _to_dict(row: SIHProblemStatement) -> dict[str, Any]:
        return {
            "ps_number": row.ps_number,
            "serial_number": row.serial_number,
            "organization": row.organization,
            "title": row.title,
            "category": row.category,
            "theme": row.theme,
            "submitted_ideas_count": row.submitted_ideas_count,
            "submitted_ideas_limit": row.submitted_ideas_limit,
            "deadline": row.deadline,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }
