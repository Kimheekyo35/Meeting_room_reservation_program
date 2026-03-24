import os
from datetime import datetime, timedelta

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor
from slack_sdk import WebClient
from zoneinfo import ZoneInfo

from test_app import COMPANIES_FLOOR, URL, get_room_name

load_dotenv()

DB_HOST = os.getenv("PG_HOST")
DB_PORT = os.getenv("PG_PORT")
DB_DATABASE = os.getenv("PG_DATABASE")
DB_USER = os.getenv("PG_USER")
DB_PASSWORD = os.getenv("PG_PASSWORD")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

SEOUL_TZ = ZoneInfo("Asia/Seoul")


def floor_name_from_id(company_id: str, floor_id: str) -> str:
    for option in COMPANIES_FLOOR.get(company_id, []):
        if option["value"] == floor_id:
            return option["text"]["text"]
    return floor_id


def format_attendee_names(attendees: list[dict]) -> str:
    names = [attendee["attendee_name"] for attendee in attendees if attendee.get("attendee_name")]
    return ", ".join(names) if names else "-"


def fetch_due_reminders(now: datetime) -> list[dict]:
    target_dt = now + timedelta(minutes=5)
    target_day = target_dt.strftime("%Y-%m-%d")
    target_time = target_dt.strftime("%H:%M")

    query = """
        WITH due_groups AS (
            SELECT
                rb.booking_group_id,
                MIN(rb.reserve_time) AS start_time,
                MAX(rb.reserve_time) AS last_slot_time,
                MIN(rb.company_id) AS company_id,
                MIN(rb.reserve_day) AS reserve_day,
                MIN(rb.floor) AS floor,
                MIN(rb.room_id) AS room_id,
                MIN(rb.user_id) AS user_id,
                MIN(rb.user_nickname) AS user_nickname,
                MIN(rb.using_reason) AS using_reason
            FROM meeting_room_booking.room_booking rb
            WHERE rb.booking_status = 'ACTIVE'
              AND rb.reminder_enabled = TRUE
              AND rb.reminder_sent_at IS NULL
              AND rb.reserve_day = %s
            GROUP BY rb.booking_group_id
            HAVING MIN(rb.reserve_time) = %s
        )
        SELECT
            dg.booking_group_id,
            dg.company_id,
            dg.reserve_day,
            dg.floor,
            dg.room_id,
            dg.user_id,
            dg.user_nickname,
            dg.using_reason,
            dg.start_time,
            TO_CHAR(
                (TO_TIMESTAMP(dg.last_slot_time, 'HH24:MI') + INTERVAL '30 minutes'),
                'HH24:MI'
            ) AS end_time,
            COALESCE(
                JSON_AGG(
                    JSON_BUILD_OBJECT(
                        'attendee_id', rba.attendee_id,
                        'attendee_name', rba.attendee_name
                    )
                ) FILTER (WHERE rba.attendee_id IS NOT NULL),
                '[]'::json
            ) AS attendees
        FROM due_groups dg
        LEFT JOIN meeting_room_booking.room_booking_attendee rba
          ON rba.booking_group_id = dg.booking_group_id
        GROUP BY
            dg.booking_group_id,
            dg.company_id,
            dg.reserve_day,
            dg.floor,
            dg.room_id,
            dg.user_id,
            dg.user_nickname,
            dg.using_reason,
            dg.start_time,
            dg.last_slot_time
        ORDER BY dg.start_time, dg.booking_group_id
    """

    with psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_DATABASE,
        user=DB_USER,
        password=DB_PASSWORD,
    ) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (target_day, target_time))
            rows = cursor.fetchall()

    for row in rows:
        row["attendees"] = list(row.get("attendees") or [])

    return rows


def open_dm_channel(client: WebClient, user_id: str) -> str:
    response = client.conversations_open(users=[user_id])
    return response["channel"]["id"]


def send_reminder(client: WebClient, booking: dict) -> None:
    attendee_names = format_attendee_names(booking["attendees"])
    floor_name = floor_name_from_id(booking["company_id"], booking["floor"])
    room_name = get_room_name(booking["company_id"], booking["floor"], booking["room_id"])

    recipients = [booking["user_id"]]
    recipients.extend(
        attendee["attendee_id"]
        for attendee in booking["attendees"]
        if attendee.get("attendee_id") and attendee["attendee_id"] != booking["user_id"]
    )

    for user_id in dict.fromkeys(recipients):
        dm_channel_id = open_dm_channel(client, user_id)
        client.chat_postMessage(
            channel=dm_channel_id,
            text=(
                f"*회의실 예약 5분 전 알림*\n"
                f"`예약자`: {booking['user_nickname']}\n"
                f"`위치`: {booking['company_id']} {floor_name} {room_name}\n"
                f"`예약 시간`: {booking['reserve_day']} {booking['start_time']}~{booking['end_time']}\n"
                f"`참석자`: {attendee_names}\n"
                f"`사용 목적`: {booking['using_reason']}"
            ),
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*회의실 예약 5분 전 알림*\n"
                            f"`예약자`: {booking['user_nickname']}\n"
                            f"`위치`: {booking['company_id']} {floor_name} {room_name}\n"
                            f"`예약 시간`: {booking['reserve_day']} {booking['start_time']}~{booking['end_time']}\n"
                            f"`참석자`: {attendee_names}\n"
                            f"`사용 목적`: {booking['using_reason']}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": ":mag: 조회"},
                            "url": URL,
                            "action_id": "booking_view",
                        }
                    ],
                },
            ],
        )


def mark_reminder_sent(booking_group_id: str, sent_at: datetime) -> None:
    query = """
        UPDATE meeting_room_booking.room_booking
        SET reminder_sent_at = %s
        WHERE booking_group_id = %s
          AND booking_status = 'ACTIVE'
          AND reminder_sent_at IS NULL
    """

    with psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_DATABASE,
        user=DB_USER,
        password=DB_PASSWORD,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (sent_at, booking_group_id))
        connection.commit()


def remind_db(now: datetime | None = None) -> int:
    current = now or datetime.now(SEOUL_TZ)
    client = WebClient(token=SLACK_BOT_TOKEN)
    bookings = fetch_due_reminders(current)

    sent_count = 0
    for booking in bookings:
        send_reminder(client, booking)
        mark_reminder_sent(booking["booking_group_id"], current)
        sent_count += 1

    return sent_count


if __name__ == "__main__":
    sent_count = remind_db()
    print(f"sent_reminders={sent_count}")
