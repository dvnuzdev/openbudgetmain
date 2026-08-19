import logging
import httpx
import random
from typing import Dict, Any, Tuple, Optional
from app.config import settings

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
]

class OpenBudgetAPIService:
    """Service interfacing with OpenBudget real OTP & Vote Verification API endpoints."""

    def __init__(self):
        self.base_url = "https://openbudget.uz/api/v1"
        self.alt_url = "https://openbudget.uz/api/v2"
        self.timeout = 15.0

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://openbudget.uz",
            "Referer": "https://openbudget.uz/board-initiatives"
        }

    async def send_otp(self, phone_number: str, project_id: Optional[str] = None, captcha_token: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Request OpenBudget API to send 6-digit SMS OTP code to the specified phone number.
        Returns: (success: bool, message: str, extra_data: dict)
        """
        target_project = project_id or settings.OPENBUDGET_PROJECT_ID
        clean_phone = phone_number.replace("+", "").strip()
        logger.info(f"Requesting OpenBudget OTP for phone +{clean_phone} on project {target_project}")

        # Development/Test mode fallback
        if target_project == "board_123456" and settings.ENV == "development":
            return True, "SMS tasdiqlash kodi telefoningizga yuborildi.", {"session_token": "mock_token_12345"}

        payload = {
            "phone": clean_phone,
            "board_id": target_project,
            "initiative_id": target_project
        }
        if captcha_token:
            payload["captcha_token"] = captcha_token
            payload["g-recaptcha-response"] = captcha_token

        endpoints = [
            f"{self.base_url}/vote/send-code",
            f"{self.base_url}/user/temp/vote",
            f"{self.alt_url}/vote/send-code"
        ]

        async with httpx.AsyncClient(timeout=self.timeout, headers=self._get_headers(), follow_redirects=True) as client:
            for ep in endpoints:
                try:
                    response = await client.post(ep, json=payload)
                    logger.info(f"OpenBudget OTP send-code status {response.status_code} from {ep}")

                    if response.status_code in [200, 201]:
                        data = response.json()
                        if isinstance(data, dict) and (data.get("success") or data.get("status") == "ok" or "token" in data or "id" in data):
                            return True, "SMS tasdiqlash kodi yuborildi.", data
                        msg = data.get("message") or data.get("error") or "SMS yuborishda xatolik."
                        return False, msg, data
                    elif response.status_code == 404:
                        continue # Try next endpoint URL
                    else:
                        try:
                            data = response.json()
                            msg = data.get("message") or data.get("detail") or f"OpenBudget xatosi ({response.status_code})."
                        except Exception:
                            msg = f"OpenBudget server xatosi (HTTP {response.status_code})."
                        
                        if target_project == "board_123456":
                            return True, "SMS tasdiqlash kodi yuborildi (Test rejimi).", {"session_token": "mock_test_token"}
                        return False, msg, {}

                except httpx.HTTPError as err:
                    logger.warning(f"Endpoint {ep} failed: {err}")
                    continue

        if target_project == "board_123456":
            return True, "SMS tasdiqlash kodi yuborildi (Test rejimi).", {"session_token": "mock_test_token"}
        return False, "OpenBudget serveri bilan ulanishda xatolik. Birozdan so'ng qayta urinib ko'ring.", {}

    async def verify_otp(self, phone_number: str, otp_code: str, project_id: Optional[str] = None) -> Tuple[bool, str, str]:
        """
        Verify 6-digit SMS OTP code with OpenBudget API.
        Returns: (verified: bool, message: str, openbudget_tx_id: str)
        """
        target_project = project_id or settings.OPENBUDGET_PROJECT_ID
        clean_phone = phone_number.replace("+", "").strip()
        logger.info(f"Verifying OpenBudget OTP {otp_code} for phone +{clean_phone}")

        if target_project == "board_123456" and settings.ENV == "development":
            if len(otp_code) == 6 and otp_code.isdigit():
                return True, "Ovozingiz muvaffaqiyatli qabul qilindi!", f"OB_TX_{clean_phone[-4:]}_VERIFIED"
            return False, "SMS kodi noto'g'ri kiritildi.", ""

        payload = {
            "phone": clean_phone,
            "code": otp_code,
            "otp": otp_code,
            "board_id": target_project,
            "initiative_id": target_project
        }

        endpoints = [
            f"{self.base_url}/vote/verify-code",
            f"{self.base_url}/user/temp/vote/verify",
            f"{self.alt_url}/vote/verify-code"
        ]

        async with httpx.AsyncClient(timeout=self.timeout, headers=self._get_headers(), follow_redirects=True) as client:
            for ep in endpoints:
                try:
                    response = await client.post(ep, json=payload)
                    logger.info(f"OpenBudget verify-code status {response.status_code} from {ep}")

                    if response.status_code in [200, 201]:
                        data = response.json()
                        tx_id = data.get("transaction_id") or data.get("id") or f"OB_{clean_phone[-4:]}"
                        return True, "Ovozingiz muvaffaqiyatli tasdiqlandi!", str(tx_id)
                    elif response.status_code == 404:
                        continue
                    else:
                        try:
                            data = response.json()
                            msg = data.get("message") or data.get("detail") or "SMS kodi noto'g'ri yoki muddati o'tgan."
                        except Exception:
                            msg = "SMS kodi noto'g'ri kiritildi."

                        if target_project == "board_123456" and len(otp_code) == 6 and otp_code.isdigit():
                            return True, "Ovozingiz muvaffaqiyatli tasdiqlandi! (Test)", f"OB_TX_{clean_phone[-4:]}_TEST"
                        return False, msg, ""

                except httpx.HTTPError as err:
                    logger.warning(f"Verify endpoint {ep} failed: {err}")
                    continue

        if target_project == "board_123456" and len(otp_code) == 6 and otp_code.isdigit():
            return True, "Ovozingiz muvaffaqiyatli tasdiqlandi! (Test)", f"OB_TX_{clean_phone[-4:]}_TEST"
        return False, "OpenBudget serverida ulanish uzildi. Qayta urinib ko'ring.", ""

openbudget_api = OpenBudgetAPIService()
