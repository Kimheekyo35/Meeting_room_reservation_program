import os
import re
import json
from datetime import datetime
from zoneinfo import ZoneInfo

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

# 실제 운영에서는 각 회의실마다 고유 calendarId 필요
ROOM_OPTIONS = [
    ("1번 회의실(16인)", "primary"),
    ("3번 회의실(6인)", "primary"),
    ("4번 회의실(6인)", "primary"),
    ("5번 회의실(6인/모니터 X)", "primary"),
    ("6번 회의실(6인/화상회의)", "primary"),
]

app = App(token=os.environ["SLACK_BOT_TOKEN"])


def get_calendar_service():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def parse_time(date_str: str, time_str: str) -> datetime:
    naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=SEOUL_TZ)


def is_time_conflicted(service, calendar_id: str, start_dt: datetime, end_dt: datetime) -> bool:
    body = {
        "timeMin": start_dt.isoformat(),
        "timeMax": end_dt.isoformat(),
        "timeZone": "Asia/Seoul",
        "items": [{"id": calendar_id}],
    }
    result = service.freebusy().query(body=body).execute()
    busy = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    return len(busy) > 0


def create_calendar_event(service, calendar_id: str, room_name: str, title: str, start_dt: datetime, end_dt: datetime, slack_user_name: str,slack_user_id:str):
    event_body = {
        "summary": title,
        "description": f"Slack 예약자: {slack_user_name}",
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "Asia/Seoul",
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "Asia/Seoul",
        },
        "extendedProperties":{
            "private":{
                "slack_user_id":slack_user_id,
                "room_name":room_name,
                "created_by": "Meeting_reserv",
            }
        }       
        }
    
    return service.events().insert(calendarId=calendar_id, body=event_body).execute()


def build_step1_modal():
    return {
        "type": "modal",
        "callback_id": "reservation_step1",
        "title": {"type": "plain_text", "text": "회의실 예약"},
        "submit": {"type": "plain_text", "text": "다음"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [
            {
                "type": "input",
                "block_id": "title_block",
                "label": {"type": "plain_text", "text": "사용 목적"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "title_action",
                    "placeholder": {"type": "plain_text", "text": "예: 주간 회의"}
                }
            },
            {
                "type": "input",
                "block_id": "date_block",
                "label": {"type": "plain_text", "text": "날짜"},
                "element": {
                    "type": "datepicker",
                    "action_id": "date_action"
                }
            },
            {
                "type":"input",
                "block_id": "username_block",
                "label": {"type": "plain_text", "text":"예약자 이름"},
                "element":{
                    "type": "plain_text_input",
                    "action_id":"name_action",
                    "placeholder":{"type":"plain_text","text":"예: 사업지원팀/홍길동"}
                }
            },
            {
                "type": "input",
                "block_id": "start_time_block",
                "label": {"type": "plain_text", "text": "시작 시간"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "start_time_action",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "예: 2:30 또는 02:30"
                    }
                }
            },
            {
                "type": "input",
                "block_id": "end_time_block",
                "label": {"type": "plain_text", "text": "종료 시간"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "end_time_action",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "예: 2:30 또는 02:30"
                    }
                }
            }
        ]
    }


def build_step2_modal(title: str, date_str: str, room_name:str, username:str, start_time: str, end_time: str, available_rooms: list[tuple[str, str]]):
    if not available_rooms:
        return {
            "type": "modal",
            "callback_id": "reservation_no_rooms",
            "title": {"type": "plain_text", "text": "회의실 예약"},
            "close": {"type": "plain_text", "text": "닫기"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*예약 가능한 회의실이 없습니다.*\n"
                            f"• 날짜: {date_str}\n"
                            f"• 시간: {start_time} ~ {end_time}"
                        )
                    }
                }
            ]
        }

    return {
        "type": "modal",
        "callback_id": "reservation_step2",
        "private_metadata": json.dumps({
            "title": title,
            "date": date_str,
            "room_name":room_name,
            "username":username,
            "start_time": start_time,
            "end_time": end_time,
        }),
        "title": {"type": "plain_text", "text": "회의실 선택"},
        "submit": {"type": "plain_text", "text": "예약"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*예약 시간*\n"
                        f"• 예약자명: {username}\n"
                        f"• 날짜: {date_str}\n"
                        f"• 시간: {start_time} ~ {end_time}\n"
                        f"• 제목: {title}"
                    )
                }
            },
            {
                "type": "input",
                "block_id": "room_block",
                "label": {"type": "plain_text", "text": "예약 가능한 회의실"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": "room_action",
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": room_name},
                            "value": calendar_id,
                        }
                        for room_name, calendar_id in available_rooms
                    ]
                }
            }
        ]
    }


@app.command("/회의실예약")
def open_modal(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view=build_step1_modal()
    )


@app.view("reservation_step1")
def handle_step1(ack, body, view):
    values = view["state"]["values"]

    title = values["title_block"]["title_action"]["value"].strip()
    username = values["username_block"]["name_action"]["value"].strip()
    date_str = values["date_block"]["date_action"]["selected_date"]
    start_time = values["start_time_block"]["start_time_action"]["value"]
    end_time = values["end_time_block"]["end_time_action"]["value"]

    errors = {}
    if not title:
        errors["title_block"] = "사용 목적을 입력해주세요."

    start_dt = parse_time(date_str, start_time)
    end_dt = parse_time(date_str, end_time)

    if end_dt <= start_dt:
        errors["end_block"] = "종료 시간은 시작 시간보다 늦어야 합니다."

    if errors:
        ack({
            "response_action": "errors",
            "errors": errors
        })
        return

    try:
        service = get_calendar_service()

        available_rooms = []
        for room_name, calendar_id in ROOM_OPTIONS:
            if not is_time_conflicted(service, calendar_id, start_dt, end_dt):
                available_rooms.append((room_name, calendar_id))

        ack({
            "response_action": "update",
            "view": build_step2_modal(
                title=title,
                date_str=date_str,
                username=username,
                room_name=room_name,
                start_time=start_time,
                end_time=end_time,
                available_rooms=available_rooms
            )
        })

    except Exception as e:
        ack({
            "response_action": "update",
            "view": {
                "type": "modal",
                "title": {"type": "plain_text", "text": "오류"},
                "close": {"type": "plain_text", "text": "닫기"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*오류 발생*\n```{str(e)}```"}
                    }
                ]
            }
        })


@app.view("reservation_step2")
def handle_step2(ack, body, view):
    values = view["state"]["values"]
    metadata = json.loads(view["private_metadata"])

    calendar_id = values["room_block"]["room_action"]["selected_option"]["value"]
    title = metadata["title"]
    room_name = next(
        name for name, cal_id in ROOM_OPTIONS
        if cal_id == calendar_id
    )
    name_str = metadata["username"]
    date_str = metadata["date"]
    start_time = metadata["start_time"]
    end_time = metadata["end_time"]
    slack_user_id = body["user"]["id"]
    start_dt = parse_time(date_str, start_time)
    end_dt = parse_time(date_str, end_time)

    try:
        service = get_calendar_service()

        # 마지막 저장 직전 한 번 더 충돌 확인
        if is_time_conflicted(service, calendar_id, start_dt, end_dt):
            ack({
                "response_action": "errors",
                "errors": {
                    "room_block": "방금 다른 사용자가 먼저 예약했습니다. 다시 선택해주세요."
                }
            })
            return

        created_event = create_calendar_event(
            service=service,
            calendar_id=calendar_id,
            room_name=room_name,
            title=title,
            start_dt=start_dt,
            end_dt=end_dt,
            slack_user_name=name_str,
            slack_user_id=slack_user_id
        )

        event_link = created_event.get("htmlLink", "")

        ack({
            "response_action": "update",
            "view": {
                "type": "modal",
                "title": {"type": "plain_text", "text": "예약 완료"},
                "close": {"type": "plain_text", "text": "닫기"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*예약 완료*\n"
                                f"• 제목: {title}\n"
                                f"• 날짜: {date_str}\n"
                                f"• 시간: {start_time} ~ {end_time}\n"
                                f"• 캘린더 ID: `{calendar_id}`\n"
                                + (f"• <{event_link}|캘린더 열기>" if event_link else "")
                            )
                        }
                    }
                ]
            }
        })

    except Exception as e:
        ack({
            "response_action": "update",
            "view": {
                "type": "modal",
                "title": {"type": "plain_text", "text": "예약 실패"},
                "close": {"type": "plain_text", "text": "닫기"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*오류 발생*\n```{str(e)}```"}
                    }
                ]
            }
        })


if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()