# 🚀 Slack API를 활용한 회의실 예약 시스템

Slack API, PostgreSQL, Docker를 활용하여 구축한 **회의실 예약 시스템**입니다.  
기존 예약 방식에서 발생하던 **계열사 간 회의실 예약 불편**을 줄이고,  
**예약 알림 기능**과 **모바일 확인 편의성**을 높이기 위해 개발되었습니다.

사용자는 Slack에서 회의실 예약을 요청할 수 있고,  
시스템은 예약 가능 여부를 확인한 뒤 DB에 저장하며, 예약자 및 참석자에게 알림을 발송합니다.

---

## 📌 프로젝트 개요

이 프로젝트는 회사별로 분리된 Slack 워크스페이스와 Docker 환경에서 동작하며,  
사용자의 회의실 예약 요청을 받아 예약 가능 여부를 확인한 뒤 DB에 저장하는 구조로 설계되었습니다.

### 핵심 목표
- 계열사 간 회의실 예약 과정 간소화
- Slack 기반 예약/조회/취소 지원
- 예약 확인 및 참석자 초대 알림 자동화
- 모바일에서도 쉽게 예약 현황 확인 가능
- 예약 이력 및 취소 이력 관리 가능

---
## ⭐ 이용 방법
1. `/회의실` 클릭 시

<img width="522" height="373" alt="image" src="https://github.com/user-attachments/assets/83a2f3c1-5c74-4c20-8674-cb71c4683c50" />

`조회` 클릭 시 회의실 예약 현황을 알 수 있는 URL로 넘어감
 
2. `예약 취소` 클릭 시
<img width="775" height="640" alt="image" src="https://github.com/user-attachments/assets/a1777e47-69f6-4329-aa14-85a66998ae40" />

해당 모달이 뜨고 `취소` 버튼을 누르면 해당 예약이 사라지고 DB에서도 사라짐

3. `다음` 클릭 시

날짜와 참석자, 시작 시간, 종료 시간을 정하면 됨

- 이미 예약이 되어 있으면 시작 시간과 종료 시간에서 제외됨
- 종료 시간은 시작 시간보다 이후의 시간대가 모달에 뜸
- 참석자에서 같은 워크페이스에 있는 사람을 추가 가능

<img width="519" height="725" alt="image" src="https://github.com/user-attachments/assets/69087a62-612d-4562-b8dc-62c0f6e09258" />


4. 앱 DM을 통해 예약 당사자와 참석자에게 예약 정보 메세지가 발송됨
<img width="355" height="205" alt="image" src="https://github.com/user-attachments/assets/e2ce80c1-6e03-4924-82b3-884e9371a7b5" />
---

## ✨ 주요 기능

- Slack 기반 회의실 예약
- 회사 / 층 / 회의실 / 날짜 / 시간 입력
- 30분 단위 슬롯 기반 예약 관리
- 중복 예약 자동 검증
- 예약자 DM 알림 발송
- 참석자 초대 알림 발송
- 예약 조회 기능
- 예약 취소 기능
- 취소 이력(`cancelled_at`) 관리
- 이용 목적(`using_reason`) 저장
- 참석자 정보 별도 관리

---

## 🛠 Tech Stack

- **Language**: Python
- **Framework / Library**: Slack Bolt
- **Database**: PostgreSQL
- **Container**: Docker, Docker Compose

---

## 🏗 시스템 구조

### 1. 회사별 환경 분리
- 회사마다 별도의 Slack 워크스페이스 또는 운영 환경을 사용합니다.
- 각 회사는 독립된 Docker 컨테이너에서 실행됩니다.
- 회사별 환경 변수, 회의실 목록, 층 정보, 인증 정보, DB 설정을 분리하여 관리합니다.

### 2. 사용자 요청 수신
- 사용자는 Slack에서 회의실 예약 기능을 실행합니다.
- Slack Bolt를 통해 사용자 입력을 받습니다.
- 회사, 층, 회의실, 날짜, 시작 시간, 종료 시간 등의 예약 정보를 수집합니다.

### 3. 예약 시간 가공
- 입력된 시작 시간과 종료 시간을 기준으로 예약 시간을 **30분 단위 슬롯**으로 분할합니다.
- 예: `10:00 ~ 11:00` → `10:00`, `10:30`
- 분할된 슬롯 기준으로 예약 가능 여부를 판단합니다.

### 4. 중복 예약 확인
- DB에서 같은 회사, 같은 날짜, 같은 층, 같은 회의실, 같은 시간대에 이미 예약이 있는지 조회합니다.
- 하나라도 겹치는 슬롯이 있으면 중복 예약으로 처리합니다.

### 5. 예약 저장
- 중복이 없으면 예약 정보를 `room_booking` 테이블에 저장합니다.
- 예약 시간은 30분 단위 슬롯별로 저장됩니다.
- `using_reason`(이용 목적)도 함께 저장합니다.
- 참석자는 `room_booking_attendee` 테이블에 별도로 저장합니다.
- `booking_group_id`를 기준으로 예약 정보와 참석자 정보를 연결할 수 있습니다.

### 6. 예약 확인 알림
- 예약 완료 시 예약자에게 확인 DM을 발송합니다.
- 참석자에게도 초대 알림을 발송합니다.
- 예약자와 참석자가 동일인인 경우 중복 알림을 방지합니다.

### 7. 예약 취소
- 예약 취소 시 해당 예약의 `booking_status`를 `CANCELLED`로 변경합니다.
- 취소 시각은 `cancelled_at`에 저장하여 추후 이력 확인이 가능하도록 구성했습니다.

### 8. 리마인더 기능
- 회의 시작 5분 전, 참석자 전원에게 회의 알람 DM을 전송합니다.
---

## 🔄 전체 흐름

```text
Slack 사용자 입력
    ↓
예약 정보 수집 (회사 / 층 / 회의실 / 날짜 / 시간 / 이용 목적)
    ↓
예약 시간 30분 단위 슬롯 변환
    ↓
DB 중복 예약 확인
    ↓
예약 가능 시 room_booking 저장
    ↓
참석자 정보 room_booking_attendee 저장
    ↓
예약자 및 참석자에게 DM 알림 발송
    ↓
예약 조회 / 예약 취소 기능 제공
```
---

## 🗂 DB 스키마
`room_booking`
예약 기본 정보를 저장하는 테이블입니다.

```
- id
- company_id
- reserve_day
- reserve_time
- user_id
- user_email
- user_nickname
- floor
- room_id
- created_at
- booking_status
- cancelled_at
- booking_group_id
- using_reason
```
`room_booking_attendee`
예약 참석자 정보를 저장하는 테이블입니다.

```
- id
- booking_group_id
- attendee_id
- created_at
- attendee_name
```
**room_booking ↔ room_booking_attendee**
두 테이블은 booking_group_id를 기준으로 연결됩니다.

---
## 📁 환경 변수 설정
`.env` 파일에 아래 값을 설정해야 합니다.

```
SLACK_BOT_TOKEN=
SLACK_APP_TOKEN=

PG_HOST=
PG_PORT=
PG_DATABASE=
PG_USER=
PG_PASSWORD=
```

## ⚙ 회의실 정보 설정

현재 프로젝트는 GT와 메리츠타워 기준으로 구성되어 있습니다.
다른 건물을 사용하려면 아래 설정을 수정해야 합니다.
```
ROOMS_BY_COMPANY
COMPANIES
COMPANIES_FLOOR
```
건물명, 층수, 회의실 목록을 실제 운영 환경에 맞게 수정하여 사용할 수 있습니다.
