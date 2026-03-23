import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import uuid
import psycopg2
from psycopg2 import Error
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

SEOUL_TZ = ZoneInfo("Asia/Seoul")

SKIP_SLACK_AUTH_TEST = os.getenv("SLACK_SKIP_AUTH_TEST", "0").lower() in ("1", "true", "yes")

# DB
DB_HOST = os.getenv("PG_HOST")
DB_PORT = os.getenv("PG_PORT")
DB_DATABASE = os.getenv("PG_DATABASE")
DB_USER = os.getenv("PG_USER")
DB_PASSWORD = os.getenv("PG_PASSWORD")

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    token_verification_enabled=not SKIP_SLACK_AUTH_TEST,
)

URL = "https://www.wemarketing.co.kr/room-reservation/"

TIME_SLOTS = [
    "09:00", "09:30",
    "10:00", "10:30",
    "11:00", "11:30",
    "12:00", "12:30",
    "13:00", "13:30",
    "14:00", "14:30",
    "15:00", "15:30",
    "16:00", "16:30",
    "17:00", "17:30",
    "18:00", "18:30",
    "19:00", "19:30",
    "20:00", "20:30",
    "21:00", "21:30",
    "22:00"
]

COMPANIES = [
    {"text": {"type": "plain_text", "text": "GT타워"}, "value": "GT타워"},
    {"text": {"type": "plain_text", "text": "메리츠타워"}, "value": "메리츠타워"},
]

COMPANIES_FLOOR = {
    "GT타워": [
        {"text": {"type": "plain_text", "text": "4층"}, "value": "4F"},
        {"text": {"type": "plain_text", "text": "7층"}, "value": "7F"},
        {"text": {"type": "plain_text", "text": "14층"}, "value": "14F"},
    ],
    "메리츠타워": [
        {"text": {"type": "plain_text", "text": "17층"}, "value": "17F"},
    ],
}

ROOMS_BY_COMPANY = {
    "메리츠타워": [
        {"17F": {"text": {"type": "plain_text", "text": "1회의실 [16인]"}, "value": "we_room_17F_1"}},
        {"17F": {"text": {"type": "plain_text", "text": "3회의실 [6인]"}, "value": "we_room_17F_3"}},
        {"17F": {"text": {"type": "plain_text", "text": "4회의실 [6인]"}, "value": "we_room_17F_4"}},
        {"17F": {"text": {"type": "plain_text", "text": "5회의실 [6인/모니터 X]"}, "value": "we_room_17F_5"}},
        {"17F": {"text": {"type": "plain_text", "text": "6회의실 [6인/화상회의]"}, "value": "we_room_17F_6"}},
    ],
    "GT타워": [
        {"4F": {"text": {"type": "plain_text", "text": "2회의실 [8인]"}, "value": "be_room_4F_2"}},
        {"4F": {"text": {"type": "plain_text", "text": "3회의실 [8인]"}, "value": "be_room_4F_3"}},
        {"4F": {"text": {"type": "plain_text", "text": "4회의실 [12인]"}, "value": "be_room_4F_4"}},
        {"4F": {"text": {"type": "plain_text", "text": "5회의실 [6인]"}, "value": "be_room_4F_5"}},
        {"4F": {"text": {"type": "plain_text", "text": "6회의실 [6인]"}, "value": "be_room_4F_6"}},
        {"4F": {"text": {"type": "plain_text", "text": "7회의실 [6인]"}, "value": "be_room_4F_7"}},
        {"4F": {"text": {"type": "plain_text", "text": "8회의실 [6인]"}, "value": "be_room_4F_8"}},
        {"7F": {"text": {"type": "plain_text", "text": "1회의실 [12인]"}, "value": "be_room_7F_1"}},
        {"7F": {"text": {"type": "plain_text", "text": "2회의실 [12인]"}, "value": "be_room_7F_2"}},
        {"7F": {"text": {"type": "plain_text", "text": "3회의실 [12인]"}, "value": "be_room_7F_3"}},
        {"14F": {"text": {"type": "plain_text", "text": "대회의실 "}, "value": "be_room_14F_Big"}},
        {"14F": {"text": {"type": "plain_text", "text": "1회의실 [12인]"}, "value": "be_room_14F_1"}},
        {"14F": {"text": {"type": "plain_text", "text": "2회의실 [12인]"}, "value": "be_room_14F_2"}},
        {"14F": {"text": {"type": "plain_text", "text": "3회의실 [8인]"}, "value": "be_room_14F_3"}},
        {"14F": {"text": {"type": "plain_text", "text": "4회의실 [8인]"}, "value": "be_room_14F_4"}},
        {"14F": {"text": {"type": "plain_text", "text": "6회의실 [8인]"}, "value": "be_room_14F_6"}},
    ],
}


def init_db():
    connection = None
    try:
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_DATABASE,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        connection.autocommit = False

        with connection.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS meeting_room_booking")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_room_booking.ROOM_BOOKING (
                    ID BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    COMPANY_ID TEXT NOT NULL,
                    RESERVE_DAY TEXT NOT NULL,
                    RESERVE_TIME TEXT NOT NULL,
                    USER_ID TEXT NOT NULL,
                    USER_EMAIL TEXT NOT NULL,
                    USER_NICKNAME TEXT NOT NULL,
                    FLOOR TEXT NOT NULL DEFAULT '',
                    ROOM_ID TEXT NOT NULL,
                    CREATED_AT TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT UQ_ROOM_SLOT
                        UNIQUE (COMPANY_ID, RESERVE_DAY, RESERVE_TIME, FLOOR, ROOM_ID)
                )
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING
                ADD COLUMN IF NOT EXISTS USER_EMAIL TEXT
            """)

            cursor.execute("""
                UPDATE meeting_room_booking.ROOM_BOOKING
                SET USER_EMAIL = 'temp@example.com'
                WHERE USER_EMAIL IS NULL
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING
                ALTER COLUMN USER_EMAIL SET NOT NULL
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING
                ADD COLUMN IF NOT EXISTS BOOKING_STATUS TEXT
            """)

            cursor.execute("""
                UPDATE meeting_room_booking.ROOM_BOOKING
                SET BOOKING_STATUS = 'ACTIVE'
                WHERE BOOKING_STATUS IS NULL
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING
                ALTER COLUMN BOOKING_STATUS SET DEFAULT 'ACTIVE'
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING
                ALTER COLUMN BOOKING_STATUS SET NOT NULL
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING
                ADD COLUMN IF NOT EXISTS CANCELLED_AT TIMESTAMPTZ
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING
                DROP CONSTRAINT IF EXISTS UQ_ROOM_SLOT
            """)

            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS UQ_ROOM_SLOT_ACTIVE
                ON meeting_room_booking.ROOM_BOOKING (
                    COMPANY_ID, RESERVE_DAY, RESERVE_TIME, FLOOR, ROOM_ID
                )
                WHERE BOOKING_STATUS = 'ACTIVE'
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING
                ADD COLUMN IF NOT EXISTS BOOKING_GROUP_ID TEXT
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_room_booking.ROOM_BOOKING_ATTENDEE (
                ID BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                BOOKING_GROUP_ID TEXT NOT NULL,
                ATTENDEE_ID TEXT NOT NULL,
                CREATED_AT TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT UQ_BOOKING_ATTENDEE UNIQUE (BOOKING_GROUP_ID, ATTENDEE_ID)
                )
            """)

            cursor.execute("""
                ALTER TABLE meeting_room_booking.ROOM_BOOKING_ATTENDEE
                ADD COLUMN IF NOT EXISTS ATTENDEE_NAME TEXT
            """)

        connection.commit()
        print("테이블 생성 완료")

    except Error as e:
        print(f"PostgreSQL 오류: {e}")
        if connection:
            connection.rollback()
    finally:
        if connection:
            connection.close()


def generate_time_slots(start_time: str, end_time: str, interval_minutes: int = 30):
    slots = []
    current = datetime.strptime(start_time, "%H:%M")
    end_dt = datetime.strptime(end_time, "%H:%M")

    while current < end_dt:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=interval_minutes)

    return slots


def save_booking(
    COMPANY_ID,
    RESERVE_DAY,
    FLOOR,
    ROOM_ID,
    USER_ID,
    USER_EMAIL,
    USER_NICKNAME,
    CREATED_AT,
    start_time,
    end_time,
    attendee_ids: list[str] | None = None
):
    connection = None
    booking_group_id = str(uuid.uuid4())
    attendee_ids = attendee_ids or []

    try:
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_DATABASE,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        connection.autocommit = False

        slots = generate_time_slots(start_time, end_time)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM meeting_room_booking.ROOM_BOOKING
                WHERE COMPANY_ID = %s
                  AND RESERVE_DAY = %s
                  AND FLOOR = %s
                  AND ROOM_ID = %s
                  AND BOOKING_STATUS = 'ACTIVE'
                  AND RESERVE_TIME = ANY(%s)
                """,
                (COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID, slots),
            )

            duplicated = cursor.fetchall()
            if duplicated:
                raise ValueError("이미 예약된 시간입니다.")

            for slot in slots:
                cursor.execute(
                    """
                    INSERT INTO meeting_room_booking.ROOM_BOOKING (
                        COMPANY_ID, RESERVE_DAY, RESERVE_TIME,
                        USER_ID, USER_EMAIL, USER_NICKNAME, FLOOR, ROOM_ID, CREATED_AT,
                        BOOKING_STATUS, BOOKING_GROUP_ID
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s)
                    """,
                    (
                        COMPANY_ID,
                        RESERVE_DAY,
                        slot,
                        USER_ID,
                        USER_EMAIL,
                        USER_NICKNAME,
                        FLOOR,
                        ROOM_ID,
                        CREATED_AT,
                        booking_group_id
                    ),
                )

            # 참석자 DB 저장
            for attendee in attendee_ids:
                cursor.execute(
                    """
                    INSERT INTO meeting_room_booking.ROOM_BOOKING_ATTENDEE (
                        BOOKING_GROUP_ID, ATTENDEE_ID, ATTENDEE_NAME
                    )
                    VALUES (%s, %s, %s)
                    ON CONFLICT (BOOKING_GROUP_ID, ATTENDEE_ID) DO NOTHING
                    """,
                    (booking_group_id, 
                    attendee["id"], 
                    attendee["name"]),
                )
        connection.commit()
        print("예약 저장 완료")

    except Exception as e:
        if connection:
            connection.rollback()
        print(f"예약 저장 오류: {e}")
        raise

    finally:
        if connection:
            connection.close()


def get_booked_slots(COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID):
    connection = None
    try:
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_DATABASE,
            user=DB_USER,
            password=DB_PASSWORD,
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT RESERVE_TIME
                FROM meeting_room_booking.ROOM_BOOKING
                WHERE COMPANY_ID = %s
                  AND RESERVE_DAY = %s
                  AND FLOOR = %s
                  AND ROOM_ID = %s
                  AND BOOKING_STATUS = 'ACTIVE'
                ORDER BY RESERVE_TIME
                """,
                (COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID),
            )

            rows = cursor.fetchall()
            booked = set()
            for row in rows:
                value = row[0]
                if hasattr(value, "strftime"):
                    booked.add(value.strftime("%H:%M"))
                else:
                    booked.add(str(value)[:5])
            return booked

    finally:
        if connection:
            connection.close()


def find_option(options, value):
    for opt in options:
        if opt["value"] == value:
            return opt
    return None


def get_room_options(company_id: str, floor_id: str) -> list[dict]:
    room_infos = ROOMS_BY_COMPANY.get(company_id, [])
    return [room_info[floor_id] for room_info in room_infos if floor_id in room_info]


def get_room_name(company_id: str, floor_id: str, room_id: str | None) -> str:
    if not room_id:
        return "-"
    room_option = find_option(get_room_options(company_id, floor_id), room_id)
    return room_option["text"]["text"] if room_option else "-"


def state_select_value(state_values, block_id, action_id):
    try:
        return state_values[block_id][action_id]["selected_option"]["value"]
    except Exception:
        return None


def state_date_value(state_values, block_id, action_id):
    try:
        return state_values[block_id][action_id]["selected_date"]
    except Exception:
        return None


def build_time_slot_options(slots: list[str]) -> list[dict]:
    return [{"text": {"type": "plain_text", "text": t}, "value": t} for t in slots]


def build_placeholder_option(text: str, value: str = "__none__") -> list[dict]:
    return [{"text": {"type": "plain_text", "text": text}, "value": value}]


# def find_available_start_slots(booked_slots: set[str]) -> list[str]:
#     starts = []
#     for i, start in enumerate(TIME_SLOTS[:-1]):
#         if start in booked_slots:
#             continue

#         has_end = False
#         for j in range(i + 1, len(TIME_SLOTS)):
#             covered = TIME_SLOTS[i:j]
#             if any(s in booked_slots for s in covered):
#                 break
#             has_end = True

#         if has_end:
#             starts.append(start)

#     return starts
def find_available_start_slots(booked_slots: set[str]) -> list[str]:
    return [slot for slot in TIME_SLOTS[:-1] if slot not in booked_slots]


def find_available_end_slots(start_time: str | None, booked_slots: set[str]) -> list[str]:
    if not start_time or start_time not in TIME_SLOTS:
        return []

    # 시작 시간의 인덱스
    i = TIME_SLOTS.index(start_time)
    ends = []
    for j in range(i + 1, len(TIME_SLOTS)):
        covered = TIME_SLOTS[i:j]

        if any(s in booked_slots for s in covered):
            break
        
        ends.append(TIME_SLOTS[j])

    return ends

def parse_current_context_from_body(body):
    view = body["view"]
    state_values = view.get("state", {}).get("values", {})
    meta = json.loads(view.get("private_metadata") or "{}")

    company_id = state_select_value(state_values, "company_block", "company_action") or meta.get("company_id")
    room_id = state_select_value(state_values, "room_block", "room_action") or meta.get("room_id")
    floor_id = state_select_value(state_values, "floor_block", "floor_action") or meta.get("floor_id")
    booking_date = state_date_value(state_values, "date_block", "date_action") or meta.get("booking_date")
    start_time = meta.get("start_time")
    end_time = meta.get("end_time")

    action = body.get("actions", [{}])[0]
    action_id = action.get("action_id")

    if action_id == "company_action":
        company_id = action["selected_option"]["value"]
        floor_id = None
        room_id = None
        start_time = None
        end_time = None
    elif action_id == "floor_action":
        floor_id = action["selected_option"]["value"]
        room_id = None
        start_time = None
        end_time = None
    elif action_id == "room_action":
        room_id = action["selected_option"]["value"]
        start_time = None
        end_time = None
    elif action_id == "date_action":
        booking_date = action["selected_date"]
        start_time = None
        end_time = None
    elif action_id == "start_time_action":
        start_time = action["selected_option"]["value"]
        end_time = None
    elif action_id == "end_time_action":
        end_time = action["selected_option"]["value"]

    return company_id, room_id, floor_id, booking_date, start_time, end_time


def build_step1_modal(company_id: str | None = None, floor_id: str | None = None):
    company_id = company_id or COMPANIES[1]["value"]
    company_option = find_option(COMPANIES, company_id)

    floor_options = COMPANIES_FLOOR.get(company_id, [])
    if not floor_id and floor_options:
        floor_id = floor_options[0]["value"]

    floor_option = find_option(floor_options, floor_id)
    floor_name = floor_option["text"]["text"] if floor_option else ""

    return {
        "type": "modal",
        "callback_id": "reservation_step1",
        "private_metadata": json.dumps({
            "company_id": company_id,
            "floor_id": floor_id,
            "floor_name": floor_name,
        }),
        "title": {"type": "plain_text", "text": "회의실"},
        "close": {"type": "plain_text", "text": "닫기"},
        "submit": {"type": "plain_text", "text": "다음"},
        "blocks": [
            {
                "type": "actions",
                "block_id": "lookup_actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "open_web_lookup",
                        "text": {"type": "plain_text", "text": ":mag: 조회"},
                        "url": URL,
                    },
                    {
                        "type": "button",
                        "action_id": "go_lookup_cancel",
                        "text": {"type": "plain_text", "text": ":warning: 예약 취소"},
                        "value": "lookup_cancel",
                    },
                ],
            },
            {"type": "divider"},
            {
                "type": "input",
                "block_id": "company_block",
                "dispatch_action": True,
                "label": {"type": "plain_text", "text": "빌딩"},
                "element": {
                    "type": "static_select",
                    "action_id": "company_action",
                    "placeholder": {"type": "plain_text", "text": "빌딩 선택"},
                    "options": COMPANIES,
                    "initial_option": company_option,
                },
            },
            {
                "type": "input",
                "block_id": "floor_block",
                "dispatch_action": True,
                "label": {"type": "plain_text", "text": "층"},
                "element": {
                    "type": "static_select",
                    "action_id": "floor_action",
                    "placeholder": {"type": "plain_text", "text": "층 선택"},
                    "options": floor_options,
                    "initial_option": floor_option,
                },
            },
        ],
    }


def build_step2_modal(
    company_id: str,
    floor_id: str,
    room_id: str | None = None,
    booking_date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    error_text: str | None = None,
):
    floor_options = COMPANIES_FLOOR.get(company_id, [])
    floor_option = find_option(floor_options, floor_id) if floor_id else None
    floor_name = floor_option["text"]["text"] if floor_option else ""

    room_options = get_room_options(company_id, floor_id)
    room_option = find_option(room_options, room_id) if room_id else None
    room_name = room_option["text"]["text"] if room_option else ""

    if not booking_date:
        booking_date = datetime.now(SEOUL_TZ).strftime("%Y-%m-%d")

    booked_slots = get_booked_slots(company_id, booking_date, floor_id, room_id) if floor_id and room_id else set()
    available_start_slots = find_available_start_slots(booked_slots)

    if start_time not in available_start_slots:
        start_time = None
        end_time = None

    available_end_slots = find_available_end_slots(start_time, booked_slots)
    if end_time not in available_end_slots:
        end_time = None

    start_options = build_time_slot_options(available_start_slots)
    end_options = build_time_slot_options(available_end_slots)

    safe_start_options = start_options if start_options else build_placeholder_option("예약 가능 시작 시간 없음")
    safe_end_options = end_options if end_options else build_placeholder_option("시작 시간을 먼저 선택하세요")

    start_option = find_option(start_options, start_time) if start_time else None
    end_option = find_option(end_options, end_time) if end_time else None

    start_element = {
        "type": "static_select",
        "action_id": "start_time_action",
        "placeholder": {"type": "plain_text", "text": "시작 시간을 입력하세요."},
        "options": safe_start_options,
    }
    if start_option:
        start_element["initial_option"] = start_option

    end_element = {
        "type": "static_select",
        "action_id": "end_time_action",
        "placeholder": {"type": "plain_text", "text": "종료 시간을 입력하세요."},
        "options": safe_end_options,
    }
    if end_option:
        end_element["initial_option"] = end_option

    room_element = {
        "type": "static_select",
        "action_id": "room_action",
        "placeholder": {"type": "plain_text", "text": "회의실을 선택하세요."},
        "options": room_options,
    }
    if room_option:
        room_element["initial_option"] = room_option

    blocks = [
        {
            "type": "input",
            "block_id": "room_block",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "회의실"},
            "element": room_element,
        },
        {
            "type": "input",    
            "block_id": "attendee_block",
            "label": {"type": "plain_text", "text": "참석자"},
            "element": {
                "type": "multi_users_select",
                "action_id": "attendee_action",
                "placeholder": {
                    "type": "plain_text",
                    "text": "사람을 선택하세요"
                }
            }
        },
        {
            "type": "input",
            "block_id": "date_block",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "날짜"},
            "element": {
                "type": "datepicker",
                "action_id": "date_action",
                "initial_date": booking_date,
            },
        },
        {
            "type": "input",
            "block_id": "start_time_block",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "시작 시간"},
            "element": start_element,
        },
        {
            "type": "input",
            "block_id": "end_time_block",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "종료 시간"},
            "element": end_element,
        },
    ]

    if error_text:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":warning: {error_text}"},
        })

    return {
        "type": "modal",
        "callback_id": "reservation_step2",
        "private_metadata": json.dumps({
            "company_id": company_id,
            "floor_id": floor_id,
            "floor_name": floor_name,
            "room_id": room_id,
            "room_name": room_name,
            "booking_date": booking_date,
            "start_time": start_time,
            "end_time": end_time,
        }),
        "title": {"type": "plain_text", "text": "회의실 예약"},
        "submit": {"type": "plain_text", "text": "예약"},
        "close": {"type": "plain_text", "text": "뒤로"},
        "blocks": blocks,
    }


def build_success_modal(company_id, floor_name, room_name, booking_date, start_time, end_time):
    return {
        "type": "modal",
        "callback_id": "reservation_success",
        "title": {"type": "plain_text", "text": "예약 완료"},
        "close": {"type": "plain_text", "text": "닫기"},
        "clear_on_close": True,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark: *회의실 예약이 완료되었습니다.*\n\n"
                        f"{company_id} {floor_name}\n"
                        f"• 장소: {room_name}\n"
                        f"• 날짜: {booking_date}\n"
                        f"• 시간: {start_time} ~ {end_time}"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": "success_actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "open_web_lookup",
                        "text": {"type": "plain_text", "text": ":mag: 조회"},
                        "url": URL,
                    },
                    {
                        "type": "button",
                        "action_id": "go_lookup_cancel",
                        "text": {"type": "plain_text", "text": ":warning: 예약 취소"},
                        "value": "lookup_cancel",
                    },
                ],
            },
        ],
    }


def build_home_view():
    return {
        "type": "home",
        "callback_id": "reservation_home",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*회의실 예약*\n홈에서 바로 예약 및 조회 할 수 있습니다.",
                },
            },
            {
                "type": "actions",
                "block_id": "home_actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "open_room_booking_from_home",
                        "text": {"type": "plain_text", "text": "회의실 예약 및 조회"},
                        "style": "primary",
                    },
                ],
            },
        ],
    }


def publish_home(client, user_id: str):
    client.views_publish(
        user_id=user_id,
        view=build_home_view(),
    )


def open_room_booking_modal(
    client,
    trigger_id: str | None,
    logger,
    source: str,
    extra_metadata: dict | None = None,
):
    if not trigger_id:
        logger.error("회의실 예약 모달 오픈 실패: trigger_id 없음 (source=%s)", source)
        return False

    view = build_step1_modal()
    metadata = json.loads(view.get("private_metadata") or "{}")
    if extra_metadata:
        metadata.update({key: value for key, value in extra_metadata.items() if value is not None})
        view["private_metadata"] = json.dumps(metadata)

    try:
        client.views_open(
            trigger_id=trigger_id,
            view=view,
        )
        logger.info("회의실 예약 모달 오픈 성공 (source=%s)", source)
        return True
    except Exception:
        logger.exception("회의실 예약 모달 오픈 실패 (source=%s)", source)
        return False


def get_user_future_booking(user_id: str) -> list[dict]:
    connection = None
    try:
        now = datetime.now(SEOUL_TZ)
        today = now.strftime("%Y-%m-%d")
        now_time = now.strftime("%H:%M")

        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_DATABASE,
            user=DB_USER,
            password=DB_PASSWORD,
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COMPANY_ID,
                    RESERVE_DAY,
                    FLOOR,
                    ROOM_ID,
                    USER_ID,
                    USER_EMAIL,
                    USER_NICKNAME,
                    CREATED_AT,
                    MIN(RESERVE_TIME) AS START_TIME,
                    MAX(RESERVE_TIME) AS LAST_SLOT
                FROM meeting_room_booking.ROOM_BOOKING
                WHERE USER_ID = %s
                  AND BOOKING_STATUS = 'ACTIVE'
                  AND (
                        RESERVE_DAY > %s
                        OR (RESERVE_DAY = %s AND RESERVE_TIME >= %s)
                  )
                GROUP BY
                    COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID,
                    USER_ID, USER_EMAIL, USER_NICKNAME, CREATED_AT
                ORDER BY RESERVE_DAY, START_TIME
                """,
                (user_id, today, today, now_time),
            )

            rows = cursor.fetchall()

        results = []
        for row in rows:
            (
                company_id,
                reserve_day,
                floor,
                room_id,
                user_id,
                user_email,
                user_nickname,
                created_at,
                start_time,
                last_slot,
            ) = row

            last_dt = datetime.strptime(str(last_slot)[:5], "%H:%M")
            end_dt = last_dt + timedelta(minutes=30)

            results.append({
                "company_id": company_id,
                "reserve_day": reserve_day,
                "floor": floor,
                "room_id": room_id,
                "user_id": user_id,
                "user_nickname": user_nickname,
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                "start_time": str(start_time)[:5],
                "end_time": end_dt.strftime("%H:%M"),
            })

        return results

    finally:
        if connection:
            connection.close()


def cancel_booking_by_created_at(
    user_id: str,
    company_id: str,
    reserve_day: str,
    floor: str,
    room_id: str,
    created_at: str,
):
    connection = None
    try:
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_DATABASE,
            user=DB_USER,
            password=DB_PASSWORD,
        )

        connection.autocommit = False

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE meeting_room_booking.ROOM_BOOKING
                SET BOOKING_STATUS = 'CANCELLED',
                    CANCELLED_AT = NOW()
                WHERE USER_ID = %s
                  AND COMPANY_ID = %s
                  AND RESERVE_DAY = %s
                  AND FLOOR = %s
                  AND ROOM_ID = %s
                  AND CREATED_AT = %s
                  AND BOOKING_STATUS = 'ACTIVE'
                """,
                (user_id, company_id, reserve_day, floor, room_id, created_at),
            )

            cancelled_count = cursor.rowcount

        connection.commit()
        return cancelled_count

    except Exception:
        if connection:
            connection.rollback()
        raise

    finally:
        if connection:
            connection.close()


def build_booking_cancel_list(bookings=None):
    blocks = []

    if not bookings:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "조회된 예약이 없습니다."},
        })
    else:
        for booking in bookings:
            payload = {
                "company_id": booking["company_id"],
                "reserve_day": booking["reserve_day"],
                "floor": booking["floor"],
                "room_id": booking["room_id"],
                "created_at": booking["created_at"],
                "user_id": booking["user_id"],
            }

            room_name = get_room_name(
                company_id=booking["company_id"],
                floor_id=booking["floor"],
                room_id=booking["room_id"],
            )

            blocks.extend([
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{booking['company_id']} / {booking['floor']} / {room_name}*\n"
                            f"{booking['reserve_day']} / {booking['start_time']}~{booking['end_time']}"
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "action_id": "cancel_booking",
                        "text": {"type": "plain_text", "text": "취소"},
                        "style": "danger",
                        "value": json.dumps(payload),
                        "confirm": {
                            "title": {"type": "plain_text", "text": "예약 취소"},
                            "text": {"type": "plain_text", "text": "이 예약을 취소할까요?"},
                            "confirm": {"type": "plain_text", "text": "취소하기"},
                            "deny": {"type": "plain_text", "text": "닫기"},
                            "style": "danger",
                        },
                    },
                },
                {"type": "divider"},
            ])

    return {
        "type": "modal",
        "callback_id": "booking_cancel_list",
        "title": {"type": "plain_text", "text": "회의실 예약 취소"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": blocks,
    }


@app.view("reservation_step1")
def handle_step1(ack, body, view):
    values = view["state"]["values"]

    company_id = values["company_block"]["company_action"]["selected_option"]["value"]
    floor_id = values["floor_block"]["floor_action"]["selected_option"]["value"]

    ack({
        "response_action": "push",
        "view": build_step2_modal(
            company_id=company_id,
            floor_id=floor_id,
        ),
    })


@app.action("company_action")
@app.action("floor_action")
@app.action("room_action")
@app.action("date_action")
@app.action("start_time_action")
@app.action("end_time_action")
@app.action("attendee_action")

def handle_modal_actions(ack, body, client):
    ack()

    company_id, room_id, floor_id, booking_date, start_time, end_time = parse_current_context_from_body(body)
    callback_id = body["view"]["callback_id"]

    if callback_id == "reservation_step1":
        client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=build_step1_modal(
                company_id=company_id,
                floor_id=floor_id,
            ),
        )
        return

    if callback_id == "reservation_step2":
        client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=build_step2_modal(
                company_id=company_id,
                floor_id=floor_id,
                room_id=room_id,
                booking_date=booking_date,
                start_time=start_time,
                end_time=end_time,
            ),
        )
        return

def notify_attendee(client, attendee_ids:list[str], user_nickname:str, booking_date:str, company_id:str, floor:str, room_name:str, start_time: str, end_time: str):
    user_name_list = []

    for user in attendee_ids or []:
        user_profile = client.users_info(user=user)["user"]
        profile = user_profile.get("profile", {})
        attendee_name = profile.get("display_name")
        user_name_list.append(attendee_name)
    # 문자열로 변환
    attendee_text = ", ".join(user_name_list)
    for user_id in attendee_ids or []:

        dm = client.conversations_open(users=[user_id])
        dm_channel_id = dm["channel"]["id"]

        client.chat_postMessage(
            channel=dm_channel_id,
            text=(
                f"*회의 초대 알림*\n"
                f"`예약자`: {user_nickname}\n"
                f"`위치`: {company_id} {floor} {room_name}\n"
                f"`예약 시간`: {booking_date} {start_time}~{end_time}\n"
                f"`참석자`: {attendee_text}"
            ),
            blocks=[
                {
                    "type":"section",
                    "text":{
                        "type":"mrkdwn",
                        "text":(
                            f"*회의 초대 알림*\n"
                            f"`예약자`: {user_nickname} \n"
                            f"`위치`: {company_id} {floor} {room_name} \n"
                            f"`예약 시간`: {booking_date} {start_time}~{end_time} \n"
                            f"`참석자`: {attendee_text}"
                        )}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": ":mag: 조회"},
                            "url":URL,
                            "action_id": "booking_view"
                        }
                    ]
                }
            ]
        )

@app.action("booking_view")
def handle_booking_view(ack, body, logger):
    ack()
    logger.info(body)


@app.view("reservation_step2")
def handle_step2(ack, body, view, client):
    values = view["state"]["values"]
    metadata = json.loads(view.get("private_metadata", "{}"))

    room_selected = values["room_block"]["room_action"].get("selected_option")
    start_selected = values["start_time_block"]["start_time_action"].get("selected_option")
    end_selected = values["end_time_block"]["end_time_action"].get("selected_option")

    room_id = room_selected["value"] if room_selected else None
    start_time = start_selected["value"] if start_selected else None
    end_time = end_selected["value"] if end_selected else None
    booking_date = values["date_block"]["date_action"].get("selected_date")

    company_id = metadata["company_id"]
    floor_id = metadata["floor_id"]

    floor_options = COMPANIES_FLOOR.get(company_id, [])
    floor_option = find_option(floor_options, floor_id) if floor_id else None
    floor_name = floor_option["text"]["text"] if floor_option else "-"

    room_name = get_room_name(company_id, floor_id, room_id)
    attendee_ids = values["attendee_block"]["attendee_action"].get("selected_users",[])
    
    attendee_infos = []

    for attendee_id in attendee_ids:
        user_profile = client.users_info(user=attendee_id)["user"]
        profile = user_profile.get("profile", {})
        attendee_name = profile.get("display_name")
        
        #DB에 저장할 때 해당 dict을 꺼내서 저장함
        attendee_infos.append({
        "id" : attendee_id,
        "name": attendee_name,
        })

    if not room_id:
        ack({
            "response_action": "errors",
            "errors": {"room_block": "회의실을 선택하세요."},
        })
        return

    if not start_time or start_time == "__none__":
        ack({
            "response_action": "errors",
            "errors": {"start_time_block": "선택 가능한 시작 시간이 없습니다."},
        })
        return

    if not end_time or end_time == "__none__":
        ack({
            "response_action": "errors",
            "errors": {"end_time_block": "종료 시간을 선택하세요."},
        })
        return

    start_dt = datetime.strptime(start_time, "%H:%M")
    end_dt = datetime.strptime(end_time, "%H:%M")

    if end_dt <= start_dt:
        ack({
            "response_action": "errors",
            "errors": {"end_time_block": "종료 시간은 시작 시간보다 뒤여야 합니다."},
        })
        return

    user_profile = client.users_info(user=body["user"]["id"])["user"]["profile"]
    user_email = user_profile.get("email", "")
    user_nickname = (
        user_profile.get("display_name")
        or user_profile.get("real_name")
        or body["user"].get("username", "")
    )
    
    try:
        save_booking(
            COMPANY_ID=company_id,
            RESERVE_DAY=booking_date,
            FLOOR=floor_id,
            ROOM_ID=room_id,
            USER_ID=body["user"]["id"],
            USER_EMAIL=user_email,
            USER_NICKNAME=user_nickname,
            CREATED_AT=datetime.now(SEOUL_TZ),
            start_time=start_time,
            end_time=end_time,
            attendee_ids = attendee_infos
        )
    except ValueError:
        ack({
            "response_action": "update",
            "view": build_step2_modal(
                company_id=company_id,
                floor_id=floor_id,
                room_id=room_id,
                booking_date=booking_date,
                start_time=None,
                end_time=None,
                error_text="해당 시간에 이미 예약된 회의실입니다. 다른 시간을 골라주세요.",
            ),
        })
        return

    ack({
        "response_action": "update",
        "view": build_success_modal(
            company_id=company_id,
            floor_name=floor_name,
            room_name=room_name,
            booking_date=booking_date,
            start_time=start_time,
            end_time=end_time,
        ),
    })
    
    # 참석자 이름 추출하기
    name_list = [info["name"] for info in attendee_infos if info.get("name")]
    attendee_name_text = ", ".join(name_list)

    client.chat_postMessage(
        channel=body["user"]["id"],
        text=f"회의실 예약 확인: {booking_date} / {company_id} / {floor_name} / {room_name} / {start_time}~{end_time} \n",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text":(
                        f"*회의실 예약 확인*\n"
                        f"`예약자`: {user_nickname} \n"
                        f"`위치`: {company_id} {floor_name} {room_name} \n"
                        f"`예약 시간`:{booking_date} {start_time}~{end_time} \n"
                        f"`참석자`: {attendee_name_text} \n"
                    )
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":mag: 조회"},
                        "url":URL,
                        "action_id": "booking_view"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":warning: 예약 취소"},
                        "style": "danger",
                        "action_id": "booking_cancel",
                        "value": "cancel"
                    }
                ]
            }
        ]
    )

    notify_attendee(
        client=client,
        attendee_ids=attendee_ids,
        user_nickname = user_nickname,
        booking_date=booking_date,
        company_id = company_id,
        floor = floor_name,
        room_name=room_name,
        start_time=start_time,
        end_time=end_time,
    )
    

@app.event("app_home_opened")
def handle_app_home_opened(event, client, logger):
    try:
        publish_home(client, event["user"])
    except Exception:
        logger.exception("App Home publish 실패")


@app.action("open_room_booking_from_home")
def handle_open_room_booking_from_home(ack, body, client, logger):
    ack()
    logger.info("Home 탭에서 회의실 예약 버튼 클릭")
    open_room_booking_modal(
        client=client,
        trigger_id=body.get("trigger_id"),
        logger=logger,
        source="home",
    )


@app.action("booking_cancel")
def click_cancel(ack, body, client):
    ack()
    user_id = body["user"]["id"]

    # 모달을 띄우려면 이렇게 해야됨
    client.views_open(
        trigger_id=body["trigger_id"],
        view=build_booking_cancel_list(get_user_future_booking(user_id))
    )

@app.action("go_lookup_cancel")
def click_cancel(ack, body, client):
    ack()
    user_id = body["user"]["id"]

    client.views_update(
        view_id=body["view"]["id"],
        hash=body["view"]["hash"],
        view=build_booking_cancel_list(get_user_future_booking(user_id)),
    )


@app.action("cancel_booking")
def handle_cancel_booking(ack, body, client):
    ack()

    payload = json.loads(body["actions"][0]["value"])

    cancel_booking_by_created_at(
        user_id=payload["user_id"],
        company_id=payload["company_id"],
        reserve_day=payload["reserve_day"],
        floor=payload["floor"],
        room_id=payload["room_id"],
        created_at=payload["created_at"],
    )

    refreshed = get_user_future_booking(payload["user_id"])

    client.views_update(
        view_id=body["view"]["id"],
        hash=body["view"]["hash"],
        view=build_booking_cancel_list(refreshed),
    )


@app.shortcut("open_room_booking")
def open_booking_modal(ack, shortcut, client, logger):
    ack()

    message = shortcut.get("message", {})
    thread_ts = message.get("thread_ts") or message.get("ts")
    channel = shortcut.get("channel", {})

    open_room_booking_modal(
        client=client,
        trigger_id=shortcut.get("trigger_id"),
        logger=logger,
        source="shortcut",
        extra_metadata={
            "channel_id": channel.get("id"),
            "thread_ts": thread_ts,
        },
    )


if __name__ == "__main__":
    init_db()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
