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


def parse_google_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    if "T" not in value:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=SEOUL_TZ)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(SEOUL_TZ)


def format_event_window(start_raw: str | None, end_raw: str | None) -> str:
    start_dt = parse_google_datetime(start_raw)
    end_dt = parse_google_datetime(end_raw)
    if not start_dt or not end_dt:
        return "시간 정보 없음"
    return f"{start_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%H:%M')}"


def list_user_reservations(service, slack_user_id: str) -> list[dict]:
    now_iso = datetime.now(SEOUL_TZ).isoformat()
    calendar_to_room = {}
    for room_name, calendar_id in ROOM_OPTIONS:
        if calendar_id not in calendar_to_room:
            calendar_to_room[calendar_id] = room_name

    reservations = []
    for calendar_id, fallback_room_name in calendar_to_room.items():
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=now_iso,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
            privateExtendedProperty=[
                f"slack_user_id={slack_user_id}",
                "created_by=Meeting_reserv",
            ],
        ).execute()

        for event in result.get("items", []):
            ext = event.get("extendedProperties", {}).get("private", {})
            room_name = ext.get("room_name", fallback_room_name)
            start_raw = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
            start_dt = parse_google_datetime(start_raw)
            reservations.append(
                {
                    "calendar_id": calendar_id,
                    "event_id": event.get("id"),
                    "summary": event.get("summary", "(No title)"),
                    "room_name": room_name,
                    "start_dt": start_dt,
                    "start_raw": start_raw,
                    "end_raw": event.get("end", {}).get("dateTime") or event.get("end", {}).get("date"),
                }
            )

    reservations.sort(key=lambda x: x["start_dt"] or datetime.max.replace(tzinfo=SEOUL_TZ))
    return reservations


def build_home_view(slack_user_id: str, reservations: list[dict], notice: str | None = None):
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*내 회의실 예약 목록*",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "refresh_home",
                    "text": {"type": "plain_text", "text": "새로고침"},
                }
            ],
        },
    ]

    if notice:
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": notice},
                },
            ]
        )

    if not reservations:
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "예정된 예약이 없습니다."},
                },
            ]
        )
    else:
        for reservation in reservations[:20]:
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{reservation['summary']}*\n"
                                f"회의실: {reservation['room_name']}\n"
                                f"시간: {format_event_window(reservation['start_raw'], reservation['end_raw'])}"
                            ),
                        },
                        "accessory": {
                            "type": "button",
                            "action_id": "cancel_reservation",
                            "text": {"type": "plain_text", "text": "취소"},
                            "style": "danger",
                            "value": json.dumps(
                                {
                                    "calendar_id": reservation["calendar_id"],
                                    "event_id": reservation["event_id"],
                                }
                            ),
                        },
                    },
                ]
            )

    return {
        "type": "home",
        "callback_id": "reservation_home",
        "private_metadata": json.dumps({"user_id": slack_user_id}),
        "blocks": blocks,
    }


def publish_home(client, slack_user_id: str, notice: str | None = None):
    service = get_calendar_service()
    reservations = list_user_reservations(service, slack_user_id)
    client.views_publish(
        user_id=slack_user_id,
        view=build_home_view(slack_user_id, reservations, notice=notice),
    )


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


@app.event("app_home_opened")
def handle_app_home_opened(event, client, logger):
    try:
        publish_home(client, event["user"])
    except Exception as e:
        logger.exception(e)


@app.action("refresh_home")
def handle_refresh_home(ack, body, client):
    ack()
    publish_home(client, body["user"]["id"])


@app.action("cancel_reservation")
def handle_cancel_reservation(ack, body, client):
    ack()

    payload = json.loads(body["actions"][0]["value"])
    calendar_id = payload["calendar_id"]
    event_id = payload["event_id"]
    slack_user_id = body["user"]["id"]

    service = get_calendar_service()
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    private_props = event.get("extendedProperties", {}).get("private", {})
    owner_slack_user_id = private_props.get("slack_user_id")
    created_by = private_props.get("created_by")

    if owner_slack_user_id != slack_user_id or created_by != "Meeting_reserv":
        publish_home(client, slack_user_id, notice=":warning: 본인이 예약한 일정만 취소할 수 있습니다.")
        return

    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    publish_home(client, slack_user_id, notice=":white_check_mark: 예약이 취소되었습니다.")


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
