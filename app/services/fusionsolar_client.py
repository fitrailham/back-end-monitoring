
import os
import time
import httpx

from dotenv import load_dotenv

load_dotenv()


class FusionSolarError(Exception):
    pass


class FusionSolarClient:

    def __init__(self):

        base = os.getenv(
            "FUSIONSOLAR_BASE_URL", ""
        ).rstrip("/")

        if not base.startswith("https://"):
            raise FusionSolarError(
                "FUSIONSOLAR_BASE_URL wajib HTTPS"
            )

        self.base = (
            base if base.endswith("/thirdData")
            else base + "/thirdData"
        )

        self.username = os.getenv(
            "FUSIONSOLAR_USERNAME", ""
        )
        self.password = os.getenv(
            "FUSIONSOLAR_PASSWORD", ""
        )

        if not self.username or not self.password:
            raise FusionSolarError(
                "Credential FusionSolar belum disetel di .env"
            )

        self.http = httpx.Client(
            timeout=25,
            follow_redirects=False
        )

        self.token = None
        self.expires_at = 0.0

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # =============================
    # LOGIN
    # =============================

    def login(self):

        response = self.http.post(
            self.base + "/login",
            json={
                "userName": self.username,
                "systemCode": self.password
            }
        )

        response.raise_for_status()
        payload = response.json()

        if payload.get("success") is not True:
            raise FusionSolarError(
                "Login Northbound gagal, failCode="
                f"{payload.get('failCode')}"
            )

        token = (
            response.headers.get("XSRF-TOKEN")
            or self.http.cookies.get("XSRF-TOKEN")
        )

        if not token:
            raise FusionSolarError(
                "Login berhasil tetapi XSRF-TOKEN "
                "tidak ditemukan"
            )

        self.token = token
        self.expires_at = time.monotonic() + 25 * 60

    # =============================
    # NORTHBOUND REQUEST
    # =============================

    def post(
        self,
        path: str,
        body: dict | None = None
    ) -> dict:

        if not self.token or (
            time.monotonic() >= self.expires_at
        ):
            self.login()

        path = path.strip("/")

        if not path or "/" in path or "?" in path:
            raise FusionSolarError(
                "Path endpoint Northbound tidak valid"
            )

        response = self.http.post(
            f"{self.base}/{path}",
            headers={
                "XSRF-TOKEN": self.token
            },
            json=body or {}
        )

        response.raise_for_status()
        payload = response.json()

        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
        ):
            code = (
                payload.get("failCode")
                if isinstance(payload, dict)
                else "invalid-json"
            )

            raise FusionSolarError(
                f"Northbound {path} gagal, failCode={code}"
            )

        return payload

    # =============================
    # API FUNCTIONS
    # =============================

    def get_stations(self):
        path = os.getenv(
            "FUSIONSOLAR_STATIONS_PATH",
            "getStationList"
        )
        return self.post(path)

    def get_station_kpi(self, plant_code: str):
        return self.post(
            "getStationRealKpi",
            {"stationCodes": plant_code}
        )

    def get_devices(self, plant_code: str):
        return self.post(
            "getDevList",
            {"stationCodes": plant_code}
        )

    def get_alarms(self, plant_code: str):
        path = os.getenv(
            "FUSIONSOLAR_ALARMS_PATH", ""
        )

        if not path:
            raise FusionSolarError(
                "Endpoint alarm belum dikonfigurasi"
            )

        return self.post(
            path,
            {"stationCodes": plant_code}
        )


# =========================================
# EXTRACT DATA LIST
# =========================================

def extract_list(payload: dict) -> list[dict]:

    data = payload.get("data")

    if isinstance(data, list):
        return [
            item for item in data
            if isinstance(item, dict)
        ]

    if isinstance(data, dict):
        for field in ("list", "records", "items"):
            if isinstance(data.get(field), list):
                return [
                    item for item in data[field]
                    if isinstance(item, dict)
                ]

    return []
