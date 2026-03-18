import os
import re
import json
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2 import Error, sql
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
]

SEOUL_TZ = ZoneInfo("Asia/Seoul")

SKIP_SLACK_AUTH_TEST = os.getenv("SLACK_SKIP_AUTH_TEST", "0").lower() in ("1", "true", "yes")

# DB 저장
DB_HOST = os.getenv("PG_HOST")
DB_PORT = os.getenv("PG_PORT")
DB_DATABASE = os.getenv("PG_DATABASE")
DB_USER = os.getenv("PG_USER")
DB_PASSWORD = os.getenv("PG_PASSWORD")

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    token_verification_enabled=not SKIP_SLACK_AUTH_TEST,
)

URL = "https://www.naver.com/"
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

SLOT_OPTIONS = [
    {
        "text":{"type":"plain_text","text":t},
        "value":t
    }
    for t in TIME_SLOTS
]

COMPANIES = [
    {"text":{"type":"plain_text", "text":"비나우"},"value":"BENOW"},
    {"text":{"type":"plain_text","text":"위마케팅"},"value":"Wemarketing"},
]


COMPANIES_FLOOR ={
    "BENOW":[
    {"text": {"type":"plain_text", "text":"4층"}, "value":"4F"},
    {"text": {"type":"plain_text", "text":"7층"}, "value":"7F"},
    {"text": {"type":"plain_text", "text":"14층"}, "value":"14F"}
    ],
    "Wemarketing":[
    {"text": {"type":"plain_text", "text":"17층"}, "value":"17F"}
    ]
}


ROOMS_BY_COMPANY = {
    "Wemarketing":[
        {"text": {"type": "plain_text", "text": "1회의실 [16인]"}, "value": "we_room_1"},
        {"text":{"type":"plain_text","text":"3회의실 [6인]"}, "value":"we_room_3"},
        {"text":{"type":"plain_text","text":"4회의실 [6인]"}, "value":"we_room_4"},
        {"text":{"type":"plain_text","text":"5회의실 [6인/모니터 X]"}, "value":"we_room_5"},
        {"text":{"type":"plain_text","text":"6회의실 [6인/화상회의]"}, "value":"we_room_6"},
    ],
    "BENOW": [
        {"text": {"type": "plain_text", "text": "A회의실"}, "value": "be_room_a"},
        {"text": {"type": "plain_text", "text": "B회의실"}, "value": "be_room_b"},
    ]
}


def init_db():
    """
    schema = "meeting_room_booking"
    table 이름 = "ROOM_BOOKING"
    ID, COMPANY_ID, RESERVE_TIME, RESERVE_DAY, USER_ID, USER_NICKNAME, FLOOR, ROOM_ID, CREATED_AT
    하지만 TIME의 경우, 1:00 - 2:00 예약이면 1:00, 1:30 를 저장하여 클릭할 수 없게 저장
    """

    connection = None
    try:
        connection = psycopg2.connect(
            host = DB_HOST,
            port = DB_PORT,
            database = DB_DATABASE,
            user = DB_USER,
            password = DB_PASSWORD
        )

        connection.autocommit = False

        with connection.cursor() as cursor:
            # 없으면 스키마 생성
            cursor.execute("CREATE SCHEMA IF NOT EXISTS meeting_room_booking")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_room_booking.ROOM_BOOKING (
                    ID BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    COMPANY_ID TEXT NOT NULL,
                    RESERVE_DAY TEXT NOT NULL,
                    RESERVE_TIME TEXT NOT NULL,
                    USER_ID TEXT NOT NULL,
                    USER_NICKNAME TEXT NOT NULL,
                    FLOOR TEXT NOT NULL DEFAULT '',
                    ROOM_ID TEXT NOT NULL,
                    CREATED_AT TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT UQ_ROOM_SLOT
                           UNIQUE (COMPANY_ID, RESERVE_DAY, RESERVE_TIME, FLOOR, ROOM_ID)
                    )
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
    current = datetime.strptime(start_time,"%H:%M")
    end_dt = datetime.strptime(end_time, "%H:%M")

    # 30분 간격으로 저장될 수 있게
    while current < end_dt:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=interval_minutes)

    return slots

# 예약 저장하는 함수
def save_booking(COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID, USER_ID, USER_NICKNAME, CREATED_AT, start_time, end_time):
    connection = None

    try:
        connection = psycopg2.connect(
            host = DB_HOST,
            port = DB_PORT,
            database = DB_DATABASE,
            user = DB_USER,
            password = DB_PASSWORD
        )

        connection.autocommit = False

        slots = generate_time_slots(start_time,end_time)

        with connection.cursor() as cursor:
            # 이미 예약된 슬롯 있는지 확인
            cursor.execute(
                """
                SELECT 1
                FROM meeting_room_booking.ROOM_BOOKING
                WHERE COMPANY_ID = %s
                  AND RESERVE_DAY = %s
                  AND FLOOR = %s
                  AND ROOM_ID = %s
                  AND RESERVE_TIME = ANY(%s)
            """, (COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID, slots))

            duplicated = cursor.fetchall()
            if duplicated:
                raise ValueError(f"이미 예약된 시간 존재: {duplicated}")
            
            # 예약 저장 / 30분 단위로
            for slot in slots:
                cursor.execute("""
                    INSERT INTO meeting_room_booking.ROOM_BOOKING (
                        COMPANY_ID, RESERVE_DAY, RESERVE_TIME,
                        USER_ID, USER_NICKNAME, FLOOR, ROOM_ID, CREATED_AT
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        COMPANY_ID,
                        RESERVE_DAY,
                        slot, #RESERVE_TIME 에 저장
                        USER_ID,
                        USER_NICKNAME,
                        FLOOR,
                        ROOM_ID,
                        CREATED_AT
                    ))

                
            connection.commit()
            print("예약 저장 완료")

    except Exception as e:
        if connection:
            connection.rollback()
        print(f"예약 저장 오류: {e}")

    finally:
        if connection:
            connection.close()

# 예약 조회하는 함수
def get_booked_slots(COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID):
    connection = None
    try:
        connection = psycopg2.connect(
            host = DB_HOST,
            port = DB_PORT,
            database = DB_DATABASE,
            user = DB_USER,
            password = DB_PASSWORD
        )

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT RESERVE_TIME
                FROM meeting_room_booking.ROOM_BOOKING
                WHERE COMPANY_ID = %s
                    AND RESERVE_DAY = %s
                    AND FLOOR = %s
                    AND ROOM_ID = %s
                ORDER BY RESERVE_TIME
                """, (COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID)
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


def chunked(items, n):
    for i in range(0,len(items), n):
        yield items[i:i+n]


def find_option(options, value):
    for opt in options:
        if opt["value"] == value:
            return opt
    return None


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


def minutes_from_slot(slot: str) -> int:
    dt = datetime.strptime(slot, "%H:%M")
    return dt.hour * 60 + dt.minute


def build_time_slot_options(slots: list[str]) -> list[dict]:
    return [{"text": {"type": "plain_text", "text": t}, "value": t} for t in slots]


def build_placeholder_option(text: str, value: str = "__none__") -> list[dict]:
    return [{"text": {"type": "plain_text", "text": text}, "value": value}]


def find_available_start_slots(booked_slots: set[str]) -> list[str]:
    starts = []
    for i, start in enumerate(TIME_SLOTS[:-1]):
        if start in booked_slots:
            continue
        has_end = False
        for j in range(i + 1, len(TIME_SLOTS)):
            covered = TIME_SLOTS[i:j]
            if any(s in booked_slots for s in covered):
                break
            has_end = True
        if has_end:
            starts.append(start)
    return starts


def find_available_end_slots(start_time: str | None, booked_slots: set[str]) -> list[str]:
    if not start_time or start_time not in TIME_SLOTS:
        return []

    i = TIME_SLOTS.index(start_time)
    ends = []
    for j in range(i + 1, len(TIME_SLOTS)):
        covered = TIME_SLOTS[i:j]
        if any(s in booked_slots for s in covered):
            break
        ends.append(TIME_SLOTS[j])
    return ends
    

def parse_current_context_from_body(body):
    """
    모달에서 회사/층/회의실/날짜/예약시간 가져오기
    """

    view = body["view"]
    state_values = view.get("state", {}).get("values",{})
    meta = json.loads(view.get("private_metadata") or "{}")

    company_id = state_select_value(state_values, "company_block","company_action")
    room_id = state_select_value(state_values, "room_block", "room_action")
    floor_id = state_select_value(state_values,"floor_block","floor_action")
    booking_date = state_date_value(state_values, "date_block","date_action")
    start_time = meta.get("start_time")
    end_time = meta.get("end_time")

    # 현재 action 값으로 덮어쓰기
    action = body.get("actions", [{}])[0]
    action_id = action.get("action_id")

    if action_id == "company_action":
        company_id = action["selected_option"]["value"]
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

# 조회 / 예약 버튼
def start_modal():
    return {
        "type": "actions",
        "callback_id":"reservation_start",
        "title": {
            "type":"plain_text",
            "text":"회의실 예약"
        },
        "close": {
            "type": "plain_text",
            "text": "닫기"
        },
        "submit": {
            "type": "plain_text",
            "text": "예약"
        },
        "blocks": [
            {
                "type": "actions",
                "block_id": "lookup_actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "open_web_lookup",
                        "text": {
                            "type": "plain_text",
                            "text": "조회"
                        },
                        "url": URL
                    }
                ]
            }
        ]
    }

# 슬랙 모달의 흐름 점검
"""
build_booking1_modal() 와 같은 함수 => UI 설계하는 함수

@app.view("reservation_step1") 에는 callback_id가 들어감
=> “이 callback_id인 모달이 제출되면 이 함수 실행해”라고 등록하는 코드


"""

# 첫 번째 모달
def build_step1_modal(
        company_id: str | None = None,
        floor_id: str | None = None,
    ):

    company_id = company_id or COMPANIES[1]["value"]
    company_option = find_option(COMPANIES, company_id)

    floor_options = COMPANIES_FLOOR.get(company_id, [])
    if not floor_id and floor_options:
        floor_id = floor_options[0]["value"]
    floor_option = find_option(floor_options, floor_id)
    floor_name = floor_option["text"]["text"] if floor_option else ""

    return {
        "type":"modal",
        "callback_id":"reservation_step1",
        "private_metadata": json.dumps({
            "company_id": company_id,
            "floor_id": floor_id,
            "floor_name": floor_name,
        }),
        "title": {"type": "plain_text", "text": "회의실 예약/조회"},
        "submit": {"type": "plain_text", "text": "다음"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks" : [
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
        {
            "type": "actions",
            "block_id": "lookup_actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "open_web_lookup",
                    "text": {
                        "type": "plain_text",
                        "text": "조회"
                    },
                    "url": URL
                }
            ]
        }
    ]
}
    

# 두 번째 모달이 보이는 방법
def build_step2_modal(
        company_id: str,
        floor_id: str,
        room_id: str | None = None,
        booking_date: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        error_text: str | None = None
    ):
    floor_options = COMPANIES_FLOOR.get(company_id, [])
    floor_option = find_option(floor_options, floor_id) if floor_id else None
    floor_name = floor_option["text"]["text"] if floor_option else ""

    room_options = ROOMS_BY_COMPANY.get(company_id, [])
    if not room_id and room_options:
        room_id = room_options[0]["value"]

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
            "text": {"type": "mrkdwn", "text": f":warning: {error_text}"}
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
        "title": {
            "type": "plain_text",
            "text": "회의실 예약 / 조회"
        },
        "submit": {
            "type": "plain_text",
            "text": "예약"
        },
        "close": {
            "type": "plain_text",
            "text": "취소"
        },
        "blocks": blocks,
    }

# 마지막 예약 확인
def build_success_modal(company_id, floor_name, room_name, booking_date, start_time, end_time):
    return {
        "type": "modal",
        "callback_id": "reservation_success",
        "title": {
            "type": "plain_text",
            "text": "예약 완료"
        },
        "close": {
            "type": "plain_text",
            "text": "닫기"
        },
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark: *회의실 예약이 완료되었습니다.*\n\n"
                        f"• 회사: {company_id}\n"
                        f"• 층: {floor_name}\n"
                        f"• 회의실: {room_name}\n"
                        f"• 날짜: {booking_date}\n"
                        f"• 시간: {start_time} ~ {end_time}"
                    )
                }
            },
            {
                "type": "actions",
                "block_id": "success_actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "open_web_lookup",
                        "text": {
                            "type": "plain_text",
                            "text": "조회"
                        },
                        "url": URL
                    }
                ]
            }
        ]
    }
@app.view("reservation_start")
def handle_reservation_start(ack, body, view):
    ack({
        "response_action": "push",
        "view": build_step1_modal()
    })


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
        )
    })

@app.action("open_web_lookup")
def handle_lookup_button(ack, body):
    ack()

# 해당 버튼들이 변화됐을 때
@app.action("company_action")
@app.action("floor_action")
@app.action("room_action")
@app.action("date_action")
@app.action("start_time_action")
@app.action("end_time_action")
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
            )
        )
        return
    
    if callback_id == "reservation_step2":
        client.view_update(
            view_id = body["view"]["id"],
            hash = body["view"]["hash"],
            view = build_step2_modal(
                company_id=company_id,
                floor_id=floor_id,
                room_id=room_id,
                booking_date=booking_date,
                start_time=start_time,
                end_time=end_time
            )
        )
        return
    

# reservation_step2 불러오기

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
    attendee_ids = values["attendee_block"]["attendee_action"].get("selected_users", [])

    company_id = metadata["company_id"]
    floor_id = metadata["floor_id"]
    floor_options = COMPANIES_FLOOR.get(company_id, [])
    floor_option = find_option(floor_options, floor_id) if floor_id else None
    floor_name = floor_option["text"]["text"] if floor_option else "-"

    room_options = ROOMS_BY_COMPANY.get(company_id, [])
    room_option = find_option(room_options, room_id) if room_id else None
    room_name = room_option["text"]["text"] if room_option else "-"

    if not room_id:
        ack({
            "response_action": "errors",
            "errors": {"room_block": "회의실을 선택하세요."}
        })
        return

    if not start_time or start_time == "__none__":
        ack({
            "response_action": "errors",
            "errors": {"start_time_block": "선택 가능한 시작 시간이 없습니다."}
        })
        return

    if not end_time or end_time == "__none__":
        ack({
            "response_action": "errors",
            "errors": {"end_time_block": "종료 시간을 선택하세요."}
        })
        return

    start_dt = datetime.strptime(start_time, "%H:%M")
    end_dt = datetime.strptime(end_time, "%H:%M")

    if end_dt <= start_dt:
        ack({
            "response_action": "errors",
            "errors": {"end_time_block": "종료 시간은 시작 시간보다 뒤여야 합니다."}
        })
        return

    try:
        save_booking(
            COMPANY_ID=company_id,
            RESERVE_DAY=booking_date,
            FLOOR=floor_id,
            ROOM_ID=room_id,
            USER_ID=body["user"]["id"],
            USER_NICKNAME=body["user"].get("username", ""),
            CREATED_AT=datetime.now(SEOUL_TZ),
            start_time=start_time,
            end_time=end_time,
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
                error_text="해당 시간에 이미 예약된 회의실입니다. 다른 시간을 골라주세요."
            )
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
        )
    })

    client.chat_postMessage(
        channel=body["user"]["id"],
        text=f"회의실 예약 확인: {booking_date} / {room_name} / {start_time}~{end_time}"
    )

def build_step3_modal(metadata:dict):

    company_id = metadata.get("company_id", "-")
    floor_name = metadata.get("floor_name","-")
    room_name = metadata.get("room_name", "-")
    booking_date = metadata.get("booking_date", "-")
    start_time = metadata.get("start_time", "-")
    end_time = metadata.get("end_time", "-")

    
    return {
        "type": "section",
        "title": "회의실 예약 / 조회",
        "close": "닫기",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*선택 정보*\n"
                f"• 회사: {company_id}\n"
                f"• 층: {floor_name}\n"
                f"• 회의실: {room_name}\n"
                f"• 날짜: {booking_date}\n"
                f"• 시작 시간: {start_time or '-'} \n"
                f"• 종료 시간: {end_time or '-'} "
            )
        },
        "blocks":
                    {
                "type": "actions",
                "block_id": "lookup_actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "open_web_lookup",
                        "text": {
                            "type": "plain_text",
                            "text": "조회"
                        },
                        "url": "https://www.naver.com/"
                    }
                ]
            }
    }

    if not start_options:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "시작 시간을 입력하세요."}],
        })
    elif start_time and not end_options:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "종료 시간을 입력하세요."}],
        })

    return {
        "type": "modal",
        "callback_id": "room_booking_submit",
        "private_metadata": meta,
        "title": {"type": "plain_text", "text": "회의실 예약"},
        "submit": {"type": "plain_text", "text": "예약 저장"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": blocks,
    }

    

@app.command("/회의실예약")
def open_booking_modal(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view=start_modal()
    )


@app.action("company_action")
@app.action("floor_action")
@app.action("room_action")
@app.action("date_action")
@app.action("start_time_action")
@app.action("end_time_action")
def handle_modal_actions(ack, body, client):
    ack()

    company_id, room_id, floor_id, booking_date, start_time, end_time = parse_current_context_from_body(body)

    client.views_update(
        view_id=body["view"]["id"],
        hash=body["view"]["hash"],
        view=build_step1_modal(
            company_id=company_id,
            floor_id=floor_id,
        )
    )


@app.view("room_booking_submit")
def submit_booking(ack, body, view, client):
    meta = json.loads(view.get("private_metadata") or "{}")

    company_id = meta.get("company_id")
    room_id = meta.get("room_id")
    floor_id = meta.get("floor_id")
    booking_date = meta.get("booking_date")
    room_name = meta.get("room_name")

    values = view["state"]["values"]

    start_time = values["start_time_block"]["start_time_action"]["selected_option"]["value"]
    end_time = values["end_time_block"]["end_time_action"]["selected_option"]["value"]

    if start_time == "__none__":
        ack({
            "response_action": "errors",
            "errors": {
                "start_time_block": "선택 가능한 시작 시간이 없습니다."
            }
        })
        return

    if end_time == "__none__":
        ack({
            "response_action": "errors",
            "errors": {
                "end_time_block": "시작 시간을 먼저 선택하거나 종료 시간을 다시 선택하세요."
            }
        })
        return

    start_dt = datetime.strptime(start_time, "%H:%M")
    end_dt = datetime.strptime(end_time, "%H:%M")

    if end_dt <= start_dt:
        ack({
            "response_action": "errors",
            "errors": {
                "end_time_block": "종료 시간을 다시 설정하세요."
            }
        })
        return

    try:
        save_booking(
            COMPANY_ID=company_id,
            RESERVE_DAY=booking_date,
            FLOOR=floor_id,
            ROOM_ID=room_id,
            USER_ID=body["user"]["id"],
            USER_NICKNAME=body["user"].get("username", ""),
            CREATED_AT=datetime.now(SEOUL_TZ),
            start_time=start_time,
            end_time=end_time,
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
                error_text=f"해당 시간에 이미 예약된 회의실입니다. 다른 시간을 골라주세요."
            )
        })
        return

    ack()

    # 각 사용자 앱에 DM 발송
    client.chat_postMessage(
        channel=body["user"]["id"],
        text=f"회의실 예약 확인: {booking_date} / {room_name} / {start_time}~{end_time}",
    )


if __name__ == "__main__":
    init_db()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
