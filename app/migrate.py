"""Apply the additive frontend metadata migration to the configured database."""
from app import config
from app.database import open_db


def main():
    sql = (config.PROJECT_ROOT / "migrations" / "001_frontend_metadata.sql").read_text(encoding="utf-8")
    with open_db() as conn:
        conn.execute(sql)
    print("Frontend metadata migration applied.")


if __name__ == "__main__":
    main()
