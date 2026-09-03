from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class University(Base):
    __tablename__ = "universities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    schedule_page_path: Mapped[str] = mapped_column(String(255), nullable=False)

    specialties: Mapped[list["Specialty"]] = relationship(back_populates="university")


class Specialty(Base):
    __tablename__ = "specialties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    university: Mapped[University] = relationship(back_populates="specialties")
    schedule_sources: Mapped[list["ScheduleSource"]] = relationship(back_populates="specialty")

    __table_args__ = (UniqueConstraint("university_id", "code", name="uq_specialty_code"),)


class ScheduleSource(Base):
    __tablename__ = "schedule_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id"), nullable=False)
    course_number: Mapped[int] = mapped_column(Integer, nullable=False)
    variant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pdf_path: Mapped[str] = mapped_column(String(512), nullable=False)

    specialty: Mapped[Specialty] = relationship(back_populates="schedule_sources")
    cache_entries: Mapped[list["ScheduleCache"]] = relationship(back_populates="source")

    __table_args__ = (
        UniqueConstraint("specialty_id", "course_number", "pdf_path", name="uq_schedule_source"),
    )


class ScheduleCache(Base):
    __tablename__ = "schedule_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("schedule_sources.id"), nullable=False)
    group_number: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped[ScheduleSource] = relationship(back_populates="cache_entries")

    __table_args__ = (UniqueConstraint("source_id", "group_number", name="uq_schedule_cache"),)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_keyboard_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subscription: Mapped["UserSubscription | None"] = relationship(
        back_populates="user", uselist=False
    )


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("schedule_sources.id"), nullable=False)
    group_number: Mapped[int] = mapped_column(Integer, nullable=False)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    morning_hour: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    morning_minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped[User] = relationship(back_populates="subscription")
    source: Mapped[ScheduleSource] = relationship()


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    notification_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "notification_key", name="uq_notification"),)
