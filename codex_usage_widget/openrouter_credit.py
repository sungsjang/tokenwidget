"""OpenRouter account balance and Windows-protected management key storage."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from decimal import Decimal
import json
import os
from pathlib import Path
import urllib.request


API_URL = "https://openrouter.ai/api/v1/credits"
# Reuse the key saved by the user's standalone OpenRouter widget.
KEY_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "OpenRouterCreditWidget" / "management-key.dpapi"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt(data: bytes, *, encrypt: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Windows key protection is required")
    source_buffer = ctypes.create_string_buffer(data)
    source = DATA_BLOB(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    result = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    operation = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    operation.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                          ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(result.pbData)


def save_key(key: str) -> None:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(_crypt(key.encode("utf-8"), encrypt=True))


def load_key() -> str:
    try:
        return _crypt(KEY_PATH.read_bytes(), encrypt=False).decode("utf-8")
    except (OSError, UnicodeError):
        return ""


def remaining_from_payload(payload: dict) -> Decimal:
    data = payload["data"]
    return Decimal(str(data["total_credits"])) - Decimal(str(data["total_usage"]))


def fetch_credits(key: str) -> Decimal:
    request = urllib.request.Request(API_URL, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return remaining_from_payload(json.load(response))
