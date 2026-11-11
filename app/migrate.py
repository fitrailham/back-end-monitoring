"""Apply supporting indexes for the normalized monitoring schema."""
from app import config
from app.database import open_db


def main():
    sql = (config.PROJECT_ROOT / "migrations" / "001_frontend_metadata.sql").read_text(encoding="utf-8")
    with open_db() as conn:
        conn.execute(sql)
    print("Normalized schema indexes applied.")


if __name__ == "__main__":
    main()
