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

# DB 저장
DB_HOST = os.getenv("PG_HOST")
DB_PORT = os.getenv("PG_PORT")
DB_DATABASE = os.getenv("PG_DATABASE")
DB_USER = os.getenv("PG_USER")
DB_PASSWORD = os.getenv("PG_PASSWORD")

app = App(token=os.environ["SLACK_BOT_TOKEN"])


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
def save_booking(COMPANY_ID, RESERVE_DAY, FLOOR, ROOM_ID, USER_ID, USER_NICKNAME, CREATED_AT, selected_slot, start_time, end_time):
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
            return {row[0].strftime("%H:%M") for row in rows}
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
    selected_slot = meta.get("selected_slot")

    # 현재 action 값으로 덮어쓰기
    action = body.get("actions", [{}])[0]
    action_id = action.get("action_id")

    if action_id == "company_action":
        company_id = action["selected_option"]["value"]
        room_id = None
        selected_slot = None
    elif action_id == "floor_action":
        floor_id = action["selected_option"]["value"]
        room_id = None
        selected_slot = None
    elif action_id == "room_action":
        room_id = action["selected_option"]["value"]
        selected_slot = None
    elif action_id == "date_action":
        booking_date = action["selected_date"] 
        selected_slot = None
    elif action_id == "slot_action":
        selected_slot = action["value"]

    return company_id, room_id, floor_id, booking_date, selected_slot


def build_booking_modal(
        company_id: str | None = None,
        room_id: str | None = None,
        floor_id: str | None = None,
        booking_date: str | None = None,
        selected_slot: str | None = None,
        error_text: str | None = None,
):
    company_id = company_id or COMPANIES[1]["value"]
    # get(key값, 대체값)
    # 드롭 다운에 넣을 거라 리스트 형태로 받음
    floor_options = COMPANIES_FLOOR.get(company_id,[])
    # 리스트에서 실제 선택된 회의실 하나 뽑기
    if not floor_id and floor_options:
        floor_id = floor_options[0]["value"]
        floor_name = floor_options[0]["text"]["text"]
    room_options = ROOMS_BY_COMPANY.get(company_id, [])
    if not room_id and room_options:
        room_id = room_options[0]["value"]
        room_name = room_options[0]["text"]["text"]

    if not booking_date:
        booking_date = str(datetime.today())

    booked_slots = get_booked_slots(company_id, room_id, floor_id, booking_date)
    available_slots = [t for t in TIME_SLOTS if t not in booked_slots]
    
    company_option = find_option(COMPANIES, company_id)
    floor_option = find_option(floor_options, floor_id)
    room_option = find_option(room_options, room_id)

    meta = json.dumps({
        "selected_slot": selected_slot,
        "company_id": company_id,
        "floor_id" : floor_id,
        "room_id": room_id,
        "booking_date": booking_date,
    })
    
    blocks = [
        {
            "type": "input",
            "block_id": "company_block",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "회사"},
            "element": {
                "type": "static_select",
                "action_id": "company_action",
                "placeholder": {"type": "plain_text", "text": "회사 선택"},
                "options": COMPANIES,
                "initial_option": company_option,
            },
        },
        {
            "type": "input",
            "block_id":"floor_block",
            "dispatch_action": True,
            "label": {"type":"plain_text", "text":"층"},
            "element":{
                "type": "static_select",
                "action_id": "company_action",
                "placeholder": {"type": "plain_text", "text": "층 선택"},
                "options": floor_options,
                "initial_option": floor_option,
            },
        },
        {
            "type": "input",
            "block_id": "room_block",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "회의실"},
            "element": {
                "type": "static_select",
                "action_id": "room_action",
                "placeholder": {"type": "plain_text", "text": "회의실 선택"},
                "options": room_options,
                "initial_option": room_option,
            },
        },
        {
            "type":"input",
            "block_id":"start_time_block",
            "label": {"type": "plain_text", "text": "시작 시간"},
            "element":{
                "type":"static_select",
                "action_id": "start_time_action",
                "placeholder": {"type": "plain_text", "text": "시작 시간 선택"},
                "options":SLOT_OPTIONS
            }
        },
        {
            "type":"input",
            "block_id":"end_time_block",
            "label": {"type": "plain_text", "text": "종료 시간"},
            "element": {
                "type": "static_select",
                "action_id": "end_time_action",
                "placeholder": {"type": "plain_text", "text": "종료 시간 선택"},
                "options": SLOT_OPTIONS,
            },
        }
    ]

    if error_text:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":warning: {error_text}"}
        })

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*선택 정보*\n"
                f"• 회사: `{company_id}`\n"
                f"• 층: `{floor_name}`\n"
                f"• 회의실: `{room_name}`\n"
                f"• 날짜: `{booking_date}`"
            )
        }
    })


    # 1) 클릭 가능한 시간 => 버튼
    blocks.append({
        "type":"section",
        "text":{"type":"mrkdwn", "text":"*예약 가능한 시간*"}
    })

    if available_slots:
        for idx, row in enumerate(chunked(available_slots, 4)):
            elements = []
            for slot in row:
                button = {
                    "type":"button",
                    "text":{"type": "plain_text", "text": slot},
                    "action_id": "slot_action",
                    "value": slot,
                }
                if slot == selected_slot:
                    button["style"] = "primary"
                elements.append(button)

            blocks.append({
                "type": "actions",
                "block_id": f"available_slots_{idx}",
                "elements": elements
            })
    else:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "_예약 가능한 시간이 없습니다._"}
            ]
        })

    # 2) 클릭 불가 시간 = 텍스트
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*이미 예약된 시간 (클릭 불가)*"}
    })

    if booked_slots:
        for idx, row in enumerate(chunked(sorted(booked_slots),4)):
            fields = []
            for slot in row:
                fields.append({
                    "action_id": "slot_action",
                    "value": slot,
                })
                if slot == selected_slot:
                    button["style"] = "primary"
                elements.append(button)

            blocks.append({
                "type": "actions",
                "block_id": f"available_slots_{idx}",
                "elements": elements
            })
    
    else:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "_예약 가능한 시간이 없습니다._"}
            ]
        })

    # 2) 클릭 불가 시간 = 텍스트
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*이미 예약된 시간 (클릭 불가)*"}
    })

    if booked_slots:
        for idx, row in enumerate(chunked(sorted(booked_slots), 4)):
            fields = []
            for slot in row:
                fields.append({
                    "type": "mrkdwn",
                    "text": f"~{slot}~\n예약됨"
                })
            blocks.append({
                "type": "section",
                "block_id": f"booked_slots_{idx}",
                "fields": fields
            })
    else:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "_예약된 시간이 없습니다._"}
            ]
        })

    blocks.append({"type": "divider"})

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*현재 선택된 시간:* `{selected_slot}`" if selected_slot else "*현재 선택된 시간:* 없음"
        }
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
        view=build_booking_modal()
    )


@app.action("company_action")
@app.action("room_action")
@app.action("date_action")
@app.action("slot_actin")
def handle_modal_actions(ack, body, client):
    ack()

    company_id, room_id, floor_id, booking_date, selected_slot = parse_current_context_from_body(body)

    client.views_update(
        view_id=body["view"]["id"],
        hash=body["view"]["hash"],
        view=build_booking_modal(
            company_id=company_id,
            room_id=room_id,
            floor_id=floor_id,
            booking_date=booking_date,
            selected_slot=selected_slot,
        )
    )


@app.view("room_booking_submit")
def submit_booking(ack, body, view, client):
    meta = json.loads(view.get("private_metadata") or "{}")

    company_id = meta.get("company_id")
    room_id = meta.get("room_id")
    floor_id = meta.get("floor_id")
    booking_date = meta.get("booking_date")
    selected_slot = meta.get("selected_slot")

    values = view["state"]["values"]

    start_time = values["start_time_block"]["start_time_action"]["selected_option"]["value"]
    end_time = values["end_time_block"]["end_time_action"]["selected_option"]["value"]
    
    start_dt = datetime.strptime(start_time, "%H:%M")
    end_dt = datetime.strptime(end_time, "%H:%M")   
    
    if end_dt <= start_dt:
        ack({
            "response_action": "errors",
            "errors": {
                "end_time_block": "종료 시간은 시작 시간보다 뒤여야 합니다."
            }
        })
        return
    
    if not selected_slot:
        # 시간 선택 안 했으면 모달 닫지 말고 다시 그리기
        ack({
            "response_action": "update",
            "view": build_booking_modal(
                company_id=company_id,
                room_id=room_id,
                floor_id=floor_id,
                booking_date=booking_date,
                selected_slot=None,
                error_text="시간을 먼저 클릭해서 선택해 주세요."
            )
        })
        return

    try:
        save_booking(
            COMPANY_ID=company_id,
            RESERVE_DAY=booking_date,
            FLOOR=floor_id,
            ROOM_ID=room_id,
            USER_ID = body["user"]["id"],
            USER_NICKNAME=body["user"].get("username",""),
            CREATED_AT=datetime.now(SEOUL_TZ),
            selected_slot=selected_slot,
            start_time=start_time,
            end_time=end_time
        )
    except ValueError:
        # 방금 다른 사용자가 선점했을 때 다시 조회해서 갱신
        ack({
            "response_action": "update",
            "view": build_booking_modal(
                company_id=company_id,
                room_id=room_id,
                booking_date=booking_date,
                selected_slot=None,
                error_text=f"{selected_slot} 은 이미 예약됐어요. 다른 시간을 골라주세요."
            )
        })
        return

    ack()

    client.chat_postMessage(
        channel=body["user"]["id"],
        text=f"예약 완료: {booking_date} / {room_id} / {selected_slot}"
    )


if __name__ == "__main__":
    init_db()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()