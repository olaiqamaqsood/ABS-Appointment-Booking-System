import os
import json
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/calendar"]

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

_service = None


def get_service():
    """
    Lazy-init Google Calendar service.
    Uses GOOGLE_CREDENTIALS_JSON env var if set (for Railway),
    otherwise falls back to the credentials.json file (for local).
    """
    global _service
    if _service is not None:
        return _service

    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

    if creds_json:
        # Railway / production: read from env var
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    else:
        # Local: read from file
        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError(
                f"Google credentials not found. Provide either "
                f"GOOGLE_CREDENTIALS_JSON env var or {CREDENTIALS_FILE} file."
            )
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES
        )

    _service = build(
        "calendar", "v3", credentials=creds, cache_discovery=False
    )
    return _service


def create_event(
    patient_name,
    patient_phone,
    doctor_name,
    doctor_speciality,
    appointment_date,
    start_time,
    end_time,
    symptoms=None,
    timezone="Asia/Karachi",
):
    """
    Create a Google Calendar event. Returns event_id or None on failure.
    """
    try:
        service = get_service()

        start_dt = datetime.combine(appointment_date, start_time)
        end_dt = datetime.combine(appointment_date, end_time)

        summary = f"Appointment: {patient_name} with Dr {doctor_name}"

        description_lines = [
            f"Patient: {patient_name}",
            f"Phone: {patient_phone}",
            f"Doctor: Dr {doctor_name} ({doctor_speciality})",
        ]
        if symptoms:
            description_lines.append(f"Symptoms: {symptoms}")

        event_body = {
            "summary": summary,
            "description": "\n".join(description_lines),
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": timezone,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 30},
                ],
            },
        }

        created = service.events().insert(
            calendarId=CALENDAR_ID,
            body=event_body,
        ).execute()

        return created.get("id")

    except Exception as e:
        print(f"[google_calendar] create_event failed: {e}")
        return None


def update_event(
    event_id,
    appointment_date,
    start_time,
    end_time,
    patient_name=None,
    doctor_name=None,
    timezone="Asia/Karachi",
):
    """
    Update an existing event's date/time (and summary if names given).
    """
    if not event_id:
        return False

    try:
        service = get_service()

        start_dt = datetime.combine(appointment_date, start_time)
        end_dt = datetime.combine(appointment_date, end_time)

        event_body = {
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": timezone,
            },
        }

        if patient_name and doctor_name:
            event_body["summary"] = (
                f"Appointment: {patient_name} with Dr {doctor_name}"
            )

        service.events().patch(
            calendarId=CALENDAR_ID,
            eventId=event_id,
            body=event_body,
        ).execute()

        return True

    except Exception as e:
        print(f"[google_calendar] update_event failed: {e}")
        return False


def delete_event(event_id):
    """
    Delete a calendar event.
    """
    if not event_id:
        return False

    try:
        service = get_service()
        service.events().delete(
            calendarId=CALENDAR_ID,
            eventId=event_id,
        ).execute()
        return True
    except Exception as e:
        print(f"[google_calendar] delete_event failed: {e}")
        return False