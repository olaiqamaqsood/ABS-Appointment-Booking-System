from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
from openai import OpenAI
import os
import json
import re
from datetime import time, datetime, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel

from config import engine, Base, SessionLocal
from models import Doctor, Patient, Appointment, Session
from prompt import SYSTEM_PROMPT


load_dotenv()

app = FastAPI(title="Appointment Booking System")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o")
PAKISTAN_TZ = ZoneInfo("Asia/Karachi")
SLOT_MINUTES = 30

Base.metadata.create_all(bind=engine)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class DoctorProfile(BaseModel):
    name: str
    speciality: str
    start_time: time
    end_time: time


DEFAULT_STATE = {
    "patient_name": None,
    "patient_phone": None,
    "patient_id": None,
    "doctor_id": None,
    "doctor_name": None,
    "doctor_speciality": None,
    "doctor_start_time": None,
    "doctor_end_time": None,
    "appointment_id": None,
    "appointment_date": None,
    "start_time": None,
    "end_time": None,
    "symptoms": None,
}



def load_session(session_id):
    db = SessionLocal()
    try:
        s = db.query(Session).filter(Session.session_id == session_id).first()
        if not s:
            s = Session(session_id=session_id, state=dict(DEFAULT_STATE), history=[])
            db.add(s)
            db.commit()
            db.refresh(s)
        state = dict(s.state or {})
        history = list(s.history or [])
        for k, v in DEFAULT_STATE.items():
            if k not in state:
                state[k] = v
        return state, history
    finally:
        db.close()


def save_session(session_id, state, history):
    db = SessionLocal()
    try:
        s = db.query(Session).filter(Session.session_id == session_id).first()
        if not s:
            s = Session(session_id=session_id, state=state, history=history)
            db.add(s)
        else:
            s.state = state
            s.history = history
        db.commit()
    finally:
        db.close()



def now_pk():
    return datetime.now(PAKISTAN_TZ)


def extract_phone(text):
    if not text:
        return None
    digits = re.sub(r"\D", "", str(text))
    if len(digits) == 11 and digits.startswith("03"):
        return digits
    return None


def normalize_speciality(value):
    if not value:
        return ""
    t = value.lower().strip()
    aliases = {
        "cardiology": "cardiologist",
        "dermatology": "dermatologist",
        "neurology": "neurologist",
        "dentistry": "dentist",
        "orthopedics": "orthopedic",
        "orthopaedics": "orthopedic",
        "pediatrics": "pediatrician",
        "paediatrics": "pediatrician",
        "gynecology": "gynecologist",
        "gynaecology": "gynecologist",
        "ophthalmology": "ophthalmologist",
    }
    return aliases.get(t, t)


def specialities_match(a, b):
    na, nb = normalize_speciality(a), normalize_speciality(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def normalize_doc_name(name):
    if not name:
        return ""
    t = name.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\b(dr|doctor|prof|professor)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def doctor_name_match(a, b):
    na, nb = normalize_doc_name(a), normalize_doc_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return set(na.split()) == set(nb.split())


def build_context(state, now):
    return {
        "current_datetime": now.strftime("%Y-%m-%d %H:%M"),
        "current_day": now.strftime("%A"),
        "current_year": now.year,
        "timezone": "Asia/Karachi",
        "state": {k: v for k, v in state.items() if v is not None},
    }



@app.post("/doctors")
def create_doctor(doctor: DoctorProfile):
    db = SessionLocal()
    try:
        d = Doctor(
            name=doctor.name.strip(),
            speciality=doctor.speciality.strip(),
            start_time=doctor.start_time,
            end_time=doctor.end_time,
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        return {
            "success": True,
            "doctor": {
                "id": d.id,
                "name": d.name,
                "speciality": d.speciality,
                "start_time": str(d.start_time),
                "end_time": str(d.end_time),
            },
        }
    finally:
        db.close()


@app.get("/doctors")
def get_doctors():
    db = SessionLocal()
    try:
        return [
            {
                "id": d.id,
                "name": d.name,
                "speciality": d.speciality,
                "start_time": str(d.start_time),
                "end_time": str(d.end_time),
            }
            for d in db.query(Doctor).all()
        ]
    finally:
        db.close()


# ============================================================
# REST API 1: Appointments by phone number
# ============================================================

@app.get("/appointments")
def api_appointments_by_phone(phone_number: str):
    """
    Return all appointments for a patient by phone number.
    Each appointment includes complete doctor and patient details.
    """
    result = get_appointments(phone_number=phone_number)

    if not result.get("success"):
        raise HTTPException(
            status_code=404,
            detail=result.get("message", "No appointments found."),
        )

    return result


# ============================================================
# REST API 2: Single doctor by ID
# ============================================================

@app.get("/doctors/{doctor_id}")
def api_doctor_by_id(doctor_id: int):
    """
    Return a single doctor's details by ID.
    """
    db = SessionLocal()
    try:
        d = db.query(Doctor).filter(Doctor.id == doctor_id).first()

        if not d:
            raise HTTPException(
                status_code=404,
                detail=f"Doctor with ID {doctor_id} not found.",
            )

        return {
            "id": d.id,
            "name": d.name,
            "speciality": d.speciality,
            "start_time": str(d.start_time),
            "end_time": str(d.end_time),
        }
    finally:
        db.close()


def find_doctor(doctor_name=None, speciality=None):
    db = SessionLocal()
    try:
        doctors = db.query(Doctor).all()
        results = []
        for d in doctors:
            name_ok = doctor_name_match(d.name, doctor_name) if doctor_name else False
            spec_ok = specialities_match(d.speciality, speciality) if speciality else False

            if doctor_name and speciality:
                if not (name_ok or spec_ok):
                    continue
            elif doctor_name:
                if not name_ok:
                    continue
            elif speciality:
                if not spec_ok:
                    continue
            else:
                continue

            results.append({
                "id": d.id,
                "name": d.name,
                "speciality": d.speciality,
                "start_time": str(d.start_time),
                "end_time": str(d.end_time),
            })

        if not results:
            available = sorted({d.speciality for d in doctors})
            return {
                "success": False,
                "message": "No matching doctor was found.",
                "available_specialities": available,
            }

        return {"success": True, "doctors": results}
    finally:
        db.close()


def create_or_get_patient(name, phone):
    if not name or not name.strip():
        return {"success": False, "message": "Patient name is required."}
    clean = extract_phone(phone)
    if not clean:
        return {"success": False, "message": "Invalid phone number."}

    db = SessionLocal()
    try:
        existing = db.query(Patient).filter(Patient.phone_number == clean).first()
        if existing:
            return {
                "success": True,
                "patient_id": existing.id,
                "name": existing.name,
                "phone_number": existing.phone_number,
            }
        p = Patient(name=name.strip(), phone_number=clean)
        db.add(p)
        db.commit()
        db.refresh(p)
        return {
            "success": True,
            "patient_id": p.id,
            "name": p.name,
            "phone_number": p.phone_number,
        }
    finally:
        db.close()


def check_availability(doctor_id, appointment_date, start_time,
                       exclude_appointment_id=None):
    db = SessionLocal()
    try:
        d = db.query(Doctor).filter(Doctor.id == doctor_id).first()
        if not d:
            return {"success": False, "available": False,
                    "message": "Doctor not found."}

        try:
            req_date = datetime.strptime(appointment_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return {"success": False, "available": False,
                    "message": "Invalid date format. Use YYYY-MM-DD."}

        try:
            req_start = datetime.strptime(start_time, "%H:%M").time()
        except (ValueError, TypeError):
            return {"success": False, "available": False,
                    "message": "Invalid time format. Use HH:MM."}

        if req_start.minute not in (0, 30):
            return {"success": False, "available": False,
                    "message": "Slots are only on :00 or :30."}

        req_end = (datetime.combine(req_date, req_start)
                   + timedelta(minutes=SLOT_MINUTES)).time()

        now = now_pk()
        if req_date < now.date():
            return {"success": False, "available": False,
                    "message": "Date is in the past."}
        if req_date == now.date():
            cur = now.time().replace(tzinfo=None, second=0, microsecond=0)
            if req_start <= cur:
                return {"success": False, "available": False,
                        "message": "Time has already passed today."}

        if req_start < d.start_time or req_end > d.end_time:
            return {"success": False, "available": False,
                    "message": (
                        f"Outside Dr. {d.name}'s hours "
                        f"({d.start_time.strftime('%I:%M %p')} - "
                        f"{d.end_time.strftime('%I:%M %p')})."
                    )}

        q = db.query(Appointment).filter(
            Appointment.doctor_id == doctor_id,
            Appointment.appointment_date == req_date,
            Appointment.status == "booked",
            Appointment.start_time < req_end,
            Appointment.end_time > req_start,
        )
        if exclude_appointment_id:
            q = q.filter(Appointment.id != exclude_appointment_id)

        if q.first():
            return {"success": False, "available": False,
                    "message": "Slot already booked."}

        return {
            "success": True,
            "available": True,
            "doctor_id": d.id,
            "doctor_name": d.name,
            "doctor_speciality": d.speciality,
            "appointment_date": req_date.strftime("%Y-%m-%d"),
            "start_time": req_start.strftime("%H:%M"),
            "end_time": req_end.strftime("%H:%M"),
        }
    finally:
        db.close()


def book_appointment(patient_name, patient_phone, doctor_id,
                     appointment_date, start_time, symptoms=None):
    # 1. Patient info
    patient_result = create_or_get_patient(patient_name, patient_phone)
    if not patient_result.get("success"):
        return patient_result
    patient_id = patient_result["patient_id"]

    db = SessionLocal()
    try:
        d = db.query(Doctor).filter(Doctor.id == doctor_id).first()
        if not d:
            return {"success": False, "message": "Doctor not found."}

        try:
            req_date = datetime.strptime(appointment_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return {"success": False, "message": "Invalid date format."}

        try:
            req_start = datetime.strptime(start_time, "%H:%M").time()
        except (ValueError, TypeError):
            return {"success": False, "message": "Invalid time format."}

        if req_start.minute not in (0, 30):
            return {"success": False, "message": "Slots are only on :00 or :30."}

        req_end = (datetime.combine(req_date, req_start)
                   + timedelta(minutes=SLOT_MINUTES)).time()

        now = now_pk()
        if req_date < now.date():
            return {"success": False, "message": "Date is in the past."}
        if req_date == now.date():
            cur = now.time().replace(tzinfo=None, second=0, microsecond=0)
            if req_start <= cur:
                return {"success": False, "message": "Time has passed today."}

        if req_start < d.start_time or req_end > d.end_time:
            return {"success": False,
                    "message": "Outside doctor's working hours."}

        # 2. Doctor double-booking check
        doctor_conflict = db.query(Appointment).filter(
            Appointment.doctor_id == doctor_id,
            Appointment.appointment_date == req_date,
            Appointment.status == "booked",
            Appointment.start_time < req_end,
            Appointment.end_time > req_start,
        ).first()
        if doctor_conflict:
            return {"success": False, "message": "Slot already booked."}

        # 3. Patient double-booking check (same patient at same time)
        patient_conflict = db.query(Appointment).filter(
            Appointment.patient_id == patient_id,
            Appointment.appointment_date == req_date,
            Appointment.status == "booked",
            Appointment.start_time < req_end,
            Appointment.end_time > req_start,
        ).first()
        if patient_conflict:
            other_doc = db.query(Doctor).filter(
                Doctor.id == patient_conflict.doctor_id
            ).first()
            other_name = other_doc.name if other_doc else "another doctor"
            return {
                "success": False,
                "message": (
                    f"You already have an appointment with {other_name} "
                    f"at this exact time ({req_start.strftime('%H:%M')} "
                    f"on {req_date.strftime('%Y-%m-%d')}). "
                    f"Please choose a different slot."
                ),
            }

        # 4. Create appointment
        a = Appointment(
            patient_id=patient_id,
            doctor_id=d.id,
            symptoms=symptoms or "",
            appointment_date=req_date,
            start_time=req_start,
            end_time=req_end,
            status="booked",
        )
        db.add(a)
        db.commit()
        db.refresh(a)

        return {
            "success": True,
            "appointment_id": a.id,
            "patient_id": patient_id,
            "patient_name": patient_result["name"],
            "phone_number": patient_result["phone_number"],
            "doctor_id": d.id,
            "doctor_name": d.name,
            "doctor_speciality": d.speciality,
            "symptoms": a.symptoms,
            "appointment_date": a.appointment_date.strftime("%Y-%m-%d"),
            "start_time": a.start_time.strftime("%H:%M"),
            "end_time": a.end_time.strftime("%H:%M"),
            "status": "booked",
        }
    finally:
        db.close()


def reschedule_appointment(appointment_id, new_date, new_start_time):
    db = SessionLocal()
    try:
        a = db.query(Appointment).filter(Appointment.id == appointment_id).first()
        if not a:
            return {"success": False, "message": "Appointment not found."}
        if a.status != "booked":
            return {"success": False, "message": "Only booked appointments can be rescheduled."}

        d = db.query(Doctor).filter(Doctor.id == a.doctor_id).first()
        if not d:
            return {"success": False, "message": "Doctor not found."}

        try:
            req_date = datetime.strptime(new_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return {"success": False, "message": "Invalid date format."}

        try:
            req_start = datetime.strptime(new_start_time, "%H:%M").time()
        except (ValueError, TypeError):
            return {"success": False, "message": "Invalid time format."}

        if req_start.minute not in (0, 30):
            return {"success": False, "message": "Slots are only on :00 or :30."}

        req_end = (datetime.combine(req_date, req_start)
                   + timedelta(minutes=SLOT_MINUTES)).time()

        now = now_pk()
        if req_date < now.date():
            return {"success": False, "message": "Date is in the past."}
        if req_date == now.date():
            cur = now.time().replace(tzinfo=None, second=0, microsecond=0)
            if req_start <= cur:
                return {"success": False, "message": "Time has passed today."}

        if req_start < d.start_time or req_end > d.end_time:
            return {"success": False, "message": "Outside doctor's working hours."}

        # Doctor conflict (excluding this appointment)
        doctor_conflict = db.query(Appointment).filter(
            Appointment.doctor_id == d.id,
            Appointment.appointment_date == req_date,
            Appointment.status == "booked",
            Appointment.id != a.id,
            Appointment.start_time < req_end,
            Appointment.end_time > req_start,
        ).first()
        if doctor_conflict:
            return {"success": False, "message": "New slot already booked."}

        # Patient conflict (excluding this appointment)
        patient_conflict = db.query(Appointment).filter(
            Appointment.patient_id == a.patient_id,
            Appointment.appointment_date == req_date,
            Appointment.status == "booked",
            Appointment.id != a.id,
            Appointment.start_time < req_end,
            Appointment.end_time > req_start,
        ).first()
        if patient_conflict:
            return {
                "success": False,
                "message": "You already have another appointment at this time.",
            }

        old_date = a.appointment_date.strftime("%Y-%m-%d")
        old_start = a.start_time.strftime("%H:%M")
        old_end = a.end_time.strftime("%H:%M")

        a.appointment_date = req_date
        a.start_time = req_start
        a.end_time = req_end
        db.commit()
        db.refresh(a)

        return {
            "success": True,
            "appointment_id": a.id,
            "doctor_id": d.id,
            "doctor_name": d.name,
            "doctor_speciality": d.speciality,
            "old_date": old_date,
            "old_start_time": old_start,
            "old_end_time": old_end,
            "new_date": a.appointment_date.strftime("%Y-%m-%d"),
            "new_start_time": a.start_time.strftime("%H:%M"),
            "new_end_time": a.end_time.strftime("%H:%M"),
            "status": "rescheduled",
        }
    finally:
        db.close()


def cancel_appointment(appointment_id, phone_number=None):
    clean = extract_phone(phone_number)
    if not clean:
        return {"success": False, "message": "A valid phone number is required."}

    db = SessionLocal()
    try:
        a = db.query(Appointment).filter(Appointment.id == appointment_id).first()
        if not a:
            return {"success": False, "message": "Appointment not found."}
        if a.status != "booked":
            return {"success": False, "message": "Appointment is not currently booked."}

        p = db.query(Patient).filter(Patient.id == a.patient_id).first()
        if not p:
            return {"success": False, "message": "Patient not found."}
        if p.phone_number != clean:
            return {"success": False, "message": "Phone does not match this appointment."}

        d = db.query(Doctor).filter(Doctor.id == a.doctor_id).first()

        a.status = "cancelled"
        db.commit()

        return {
            "success": True,
            "appointment_id": a.id,
            "patient_id": p.id,
            "patient_name": p.name,
            "phone_number": p.phone_number,
            "doctor_id": d.id if d else a.doctor_id,
            "doctor_name": d.name if d else None,
            "doctor_speciality": d.speciality if d else None,
            "appointment_date": a.appointment_date.strftime("%Y-%m-%d"),
            "start_time": a.start_time.strftime("%H:%M"),
            "end_time": a.end_time.strftime("%H:%M"),
            "status": "cancelled",
        }
    finally:
        db.close()


def get_appointments(phone_number=None):
    clean = extract_phone(phone_number)
    if not clean:
        return {"success": False,
                "message": "A valid 11-digit phone number is required."}

    db = SessionLocal()
    try:
        p = db.query(Patient).filter(Patient.phone_number == clean).first()
        if not p:
            return {"success": False,
                    "message": "No patient found for this phone number."}

        appts = db.query(Appointment).filter(
            Appointment.patient_id == p.id,
            Appointment.status == "booked",
        ).order_by(
            Appointment.appointment_date, Appointment.start_time
        ).all()

        if not appts:
            return {"success": False,
                    "message": "No booked appointments found for this phone number."}

        details = []
        for a in appts:
            d = db.query(Doctor).filter(Doctor.id == a.doctor_id).first()
            details.append({
                "appointment_id": a.id,
                "doctor_id": d.id if d else a.doctor_id,
                "doctor_name": d.name if d else None,
                "doctor_speciality": d.speciality if d else None,
                "patient_name": p.name,
                "phone_number": p.phone_number,
                "symptoms": a.symptoms,
                "appointment_date": a.appointment_date.strftime("%Y-%m-%d"),
                "start_time": a.start_time.strftime("%H:%M"),
                "end_time": a.end_time.strftime("%H:%M"),
                "status": a.status,
            })

        return {
            "success": True,
            "patient_id": p.id,
            "patient_name": p.name,
            "phone_number": p.phone_number,
            "multiple_appointments": len(details) > 1,
            "appointments": details,
        }
    finally:
        db.close()


tools = [
    {
        "type": "function",
        "function": {
            "name": "find_doctor",
            "description": (
                "Find doctors by name or speciality. "
                "Use speciality when the user describes symptoms "
                "or asks for a type of doctor. "
                "If no results, try related specialities."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_name": {"type": ["string", "null"]},
                    "speciality": {"type": ["string", "null"]},
                },
                "required": ["doctor_name", "speciality"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": (
                "Check if a doctor's slot is free. "
                "Date: YYYY-MM-DD. Time: HH:MM (00 or 30 minutes only). "
                "Pass exclude_appointment_id during reschedule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_id": {"type": "integer"},
                    "appointment_date": {"type": "string"},
                    "start_time": {"type": "string"},
                    "exclude_appointment_id": {"type": ["integer", "null"]},
                },
                "required": [
                    "doctor_id", "appointment_date", "start_time",
                    "exclude_appointment_id",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book a new appointment. Only call after the user has "
                "confirmed the exact date and time. "
                "Never use this for a reschedule or cancel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string"},
                    "patient_phone": {"type": "string"},
                    "doctor_id": {"type": "integer"},
                    "appointment_date": {"type": "string"},
                    "start_time": {"type": "string"},
                    "symptoms": {"type": ["string", "null"]},
                },
                "required": [
                    "patient_name", "patient_phone", "doctor_id",
                    "appointment_date", "start_time",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": (
                "Move an existing booked appointment to a new date/time. "
                "Never use book_appointment for a reschedule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "integer"},
                    "new_date": {"type": "string"},
                    "new_start_time": {"type": "string"},
                },
                "required": ["appointment_id", "new_date", "new_start_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": (
                "Cancel a booked appointment. Requires appointment_id "
                "and the patient's phone number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "integer"},
                    "phone_number": {"type": ["string", "null"]},
                },
                "required": ["appointment_id", "phone_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_appointments",
            "description": (
                "Get a patient's booked appointments by 11-digit phone number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string"},
                },
                "required": ["phone_number"],
            },
        },
    },
]


def execute_tool(tool_name, args, state):
    if tool_name == "find_doctor":
        return find_doctor(
            doctor_name=args.get("doctor_name"),
            speciality=args.get("speciality"),
        )
    if tool_name == "check_availability":
        return check_availability(
            doctor_id=args.get("doctor_id"),
            appointment_date=args.get("appointment_date"),
            start_time=args.get("start_time"),
            exclude_appointment_id=args.get("exclude_appointment_id"),
        )
    if tool_name == "book_appointment":
        return book_appointment(
            patient_name=args.get("patient_name"),
            patient_phone=args.get("patient_phone"),
            doctor_id=args.get("doctor_id"),
            appointment_date=args.get("appointment_date"),
            start_time=args.get("start_time"),
            symptoms=args.get("symptoms"),
        )
    if tool_name == "reschedule_appointment":
        return reschedule_appointment(
            appointment_id=args.get("appointment_id"),
            new_date=args.get("new_date"),
            new_start_time=args.get("new_start_time"),
        )
    if tool_name == "cancel_appointment":
        return cancel_appointment(
            appointment_id=args.get("appointment_id"),
            phone_number=args.get("phone_number") or state.get("patient_phone"),
        )
    if tool_name == "get_appointments":
        return get_appointments(phone_number=args.get("phone_number"))
    return {"success": False, "message": f"Unknown tool: {tool_name}"}


@app.post("/chat")
def chat(request: ChatRequest):
    session_id = request.session_id
    state, history = load_session(session_id)
    user_message = (request.message or "").strip()

    if not user_message:
        return {"response": "Please provide a message.", "session_id": session_id}

    now = now_pk()

    history.append({"role": "user", "content": user_message})

    # Build system + state + history
    ctx = build_context(state, now)
    system_message = (
        SYSTEM_PROMPT
        + "\n\n=== LIVE CONTEXT ===\n"
        + json.dumps(ctx, default=str)
    )

    messages = [{"role": "system", "content": system_message}]
    messages.extend(history)

    # First AI call
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0,
            parallel_tool_calls=False,
        )
    except Exception as e:
        err = "Sorry, I could not process that. Please try again."
        history.append({"role": "assistant", "content": err})
        save_session(session_id, state, history)
        return {"response": err, "session_id": session_id, "error": str(e)}

    # Tool loop
    safety = 0
    while response.choices[0].message.tool_calls and safety < 8:
        safety += 1
        assistant_msg = response.choices[0].message

        history.append({
            "role": "assistant",
            "content": assistant_msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in assistant_msg.tool_calls
            ],
        })

        for tool_call in assistant_msg.tool_calls:
            tool_name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                args = {}

            try:
                result = execute_tool(tool_name, args, state)
            except Exception as e:
                result = {"success": False, "message": f"Tool error: {e}"}

            # Update state from tool results
            if tool_name == "find_doctor" and result.get("success"):
                docs = result.get("doctors", [])
                if len(docs) == 1:
                    d = docs[0]
                    state["doctor_id"] = d["id"]
                    state["doctor_name"] = d["name"]
                    state["doctor_speciality"] = d["speciality"]
                    state["doctor_start_time"] = d["start_time"]
                    state["doctor_end_time"] = d["end_time"]

            elif tool_name == "check_availability" and result.get("success"):
                state["doctor_id"] = result.get("doctor_id", state.get("doctor_id"))
                state["doctor_name"] = result.get("doctor_name", state.get("doctor_name"))
                state["doctor_speciality"] = result.get("doctor_speciality", state.get("doctor_speciality"))
                state["appointment_date"] = result.get("appointment_date")
                state["start_time"] = result.get("start_time")
                state["end_time"] = result.get("end_time")

            elif tool_name == "book_appointment" and result.get("success"):
                state["patient_id"] = result.get("patient_id")
                state["patient_name"] = result.get("patient_name")
                state["patient_phone"] = result.get("phone_number")
                state["doctor_id"] = result.get("doctor_id")
                state["doctor_name"] = result.get("doctor_name")
                state["doctor_speciality"] = result.get("doctor_speciality")
                state["appointment_id"] = result.get("appointment_id")
                state["appointment_date"] = result.get("appointment_date")
                state["start_time"] = result.get("start_time")
                state["end_time"] = result.get("end_time")
                state["symptoms"] = result.get("symptoms")

            elif tool_name == "reschedule_appointment" and result.get("success"):
                state["appointment_id"] = result.get("appointment_id")
                state["doctor_id"] = result.get("doctor_id")
                state["doctor_name"] = result.get("doctor_name")
                state["doctor_speciality"] = result.get("doctor_speciality")
                state["appointment_date"] = result.get("new_date")
                state["start_time"] = result.get("new_start_time")
                state["end_time"] = result.get("new_end_time")

            elif tool_name == "cancel_appointment" and result.get("success"):
                if state.get("appointment_id") == result.get("appointment_id"):
                    state["appointment_id"] = None
                    state["appointment_date"] = None
                    state["start_time"] = None
                    state["end_time"] = None
                    state["symptoms"] = None

            elif tool_name == "get_appointments" and result.get("success"):
                state["patient_id"] = result.get("patient_id")
                state["patient_name"] = result.get("patient_name")
                state["patient_phone"] = result.get("phone_number")

            history.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, default=str),
            })

        # Rebuild messages with fresh state
        ctx = build_context(state, now)
        system_message = (
            SYSTEM_PROMPT
            + "\n\n=== LIVE CONTEXT ===\n"
            + json.dumps(ctx, default=str)
        )
        messages = [{"role": "system", "content": system_message}]
        messages.extend(history)

        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
                parallel_tool_calls=False,
            )
        except Exception as e:
            err = "Done, but I could not generate a summary."
            history.append({"role": "assistant", "content": err})
            save_session(session_id, state, history)
            return {"response": err, "session_id": session_id, "error": str(e)}

    # Final response
    final_text = (response.choices[0].message.content or "").strip()
    if not final_text:
        final_text = "How can I help you?"

    history.append({"role": "assistant", "content": final_text})

    save_session(session_id, state, history)

    return {"response": final_text, "session_id": session_id}