from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Time,
    Date,
    ForeignKey,
    JSON,
    DateTime,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from config import Base


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    speciality = Column(String, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # Relationship with appointments (optional but helpful)
    appointments = relationship("Appointment", back_populates="doctor")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone_number = Column(String, unique=True, nullable=False, index=True)

    # Relationship with appointments
    appointments = relationship("Appointment", back_populates="patient")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    symptoms = Column(Text, nullable=True)  # symptoms optional
    appointment_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    status = Column(String, default="booked")  # default value
    google_event_id = Column(String, nullable=True, index=True) 

    # Relationships
    patient = relationship("Patient", back_populates="appointments")
    doctor = relationship("Doctor", back_populates="appointments")


# ============================================================
# SESSION (for persistent conversation state + history)
# ============================================================

class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)

    session_id = Column(
        String,
        unique=True,
        index=True,
        nullable=False
    )

    # Full booking state as JSON
    state = Column(
        JSON,
        nullable=False,
        default=dict
    )

    # Conversation messages as JSON list
    history = Column(
        JSON,
        nullable=False,
        default=list
    )

    created_at = Column(
        DateTime,
        server_default=func.now()
    )

    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now()
    )