from __future__ import annotations
from typing import Iterable, Dict, Any
from datetime import datetime, date, time
import psycopg2

def to_date_or_none(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except:
        return None

def to_time_or_none(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%H:%M").time()
    except:
        return None

def is_empty_description(desc: str | None) -> bool:
    if not desc:
        return True
    # 공백/줄바꿈만 있는 것도 빈 것으로 처리
    return len(desc.strip()) == 0

def save_exhibitions(conn_info, exhibitions: Iterable[Dict[str, Any]]) -> int:
    """
    conn_info: (dbname, user, password, host, port) 튜플이나 dict 등으로 바꿔도 됨
    exhibitions: 각 크롤러가 만든 dict 리스트
    return: 실제로 INSERT된 개수
    """
    insert_sql = """
    INSERT INTO exhibition
    (title, description, address,
     author, start_date, end_date,
     open_time, close_time,
     views, img_url,
     gallery_name, phone_num,
     created_at, modified_at)
    VALUES (%s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s)
    """

    saved = 0
    today = date.today()

    dbname, user, password, host, port = conn_info

    conn = psycopg2.connect(
        dbname=dbname, user=user, password=password, host=host, port=port
    )
    try:
        cur = conn.cursor()

        for ex in exhibitions:
            # ✅ 1) description 비어있으면 스킵
            if is_empty_description(ex.get("description")):
                print(f"[DB] description 비어있음 → 스킵: {ex.get('title')}")
                continue

            end_dt = to_date_or_none(ex.get("end_date"))
            if end_dt is None:
                print(f"[DB] end_date 없음 → 스킵: {ex.get('title')}")
                continue

            start_dt = to_date_or_none(ex.get("start_date"))

            open_t = to_time_or_none(ex.get("open_time"))
            close_t = to_time_or_none(ex.get("close_time"))

            cur.execute(
                insert_sql,
                (
                    ex.get("title") or "",
                    ex.get("description") or "",
                    ex.get("address"),
                    ex.get("author") or "",
                    start_dt,
                    end_dt,
                    open_t,
                    close_t,
                    0,
                    ex.get("img_url", []),
                    ex.get("gallery_name"),
                    None,
                    today,
                    None,
                ),
            )
            saved += 1

        conn.commit()
        return saved
    except:
        conn.rollback()
        raise
    finally:
        conn.close()
