import psycopg2
import os

def check_real_db_columns():
    # 기존 설정과 동일하게 접속
    db_user = os.getenv("POSTGRES_USER", "pbl")
    db_password = os.getenv("POSTGRES_PASSWORD", "1234")
    db_name = os.getenv("POSTGRES_DB", "pbl")
    db_host = os.getenv("POSTGRES_HOST", "3.34.46.99")
    db_port = os.getenv("POSTGRES_PORT", "5432")

    conn = None
    try:
        conn = psycopg2.connect(
            dbname=db_name, user=db_user, password=db_password,
            host=db_host, port=db_port
        )
        cur = conn.cursor()
        
        # exhibition 테이블의 컬럼 정보를 조회합니다.
        print("\n🔍 데이터베이스 접속 성공. 컬럼명을 조회합니다...")
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'exhibition';
        """)
        rows = cur.fetchall()
        
        real_columns = [r[0] for r in rows]
        
        print("\n" + "="*40)
        print(f" [exhibition] 테이블의 실제 컬럼 목록 ({len(real_columns)}개)")
        print("="*40)
        print(real_columns)
        print("="*40 + "\n")
        
        # 진단 결과
        if 'image_url' in real_columns:
            print("✅ 'image_url' 컬럼이 존재합니다. (코드 문제 아님, 다른 원인 파악 필요)")
        elif 'imageUrl' in real_columns:
            print("⚠️ 실제 컬럼명은 'imageUrl' (카멜케이스) 입니다.")
            print("👉 해결책: SQL문에서 \"imageUrl\" 로 쌍따옴표를 붙여야 합니다.")
        elif 'imageurl' in real_columns:
            print("⚠️ 실제 컬럼명은 'imageurl' (소문자, 언더바 없음) 입니다.")
            print("👉 해결책: SQL문에서 imageurl 로 수정하세요.")
        else:
            print("❌ image 관련 컬럼을 찾을 수 없습니다. 목록을 보고 비슷한 이름을 찾으세요.")
            
    except Exception as e:
        print("❌ DB 접속 실패:", e)
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    check_real_db_columns()