from __future__ import annotations

import email.utils
import json
import os
import threading
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox


APP_NAME = "Codex Usage Widget"
APP_VERSION = "1.0"
WIDTH = 286
CLOCK_HEIGHT = 58
PANEL_WIDTH = WIDTH // 3
COLLAPSED_HEIGHT = CLOCK_HEIGHT + 116
RESET_ROW_HEIGHT = 47
REFRESH_MS = 60_000
TIME_SYNC_INTERVAL_MS = 60 * 60 * 1000
TIME_TICK_INTERVAL_MS = 250
TRANSPARENT = "#ff00ff"
WATCH_ZONES = (
    ("SEOUL", "Asia/Seoul", "#ff7aa2"),
    ("VANCOUVER", "America/Vancouver", "#5d8cff"),
    ("LONDON", "Europe/London", "#60b886"),
)
TIME_SOURCES = (
    "https://worldtimeapi.org/api/timezone/Etc/UTC",
    "https://www.google.com/generate_204",
    "https://www.cloudflare.com/cdn-cgi/trace",
)


@dataclass(frozen=True)
class Usage:
    used_percent: float
    window_minutes: int
    resets_at: int
    timestamp: datetime
    plan_type: str = ""

    @property
    def remaining_percent(self) -> int:
        return max(0, min(100, round(100 - self.used_percent)))


@dataclass(frozen=True)
class ResetCredit:
    title: str
    expires_at: datetime


@dataclass(frozen=True)
class AccountUsage:
    usage: Usage
    reset_count: int
    reset_credits: tuple[ResetCredit, ...]


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _read_latest_usage(path: Path) -> Usage | None:
    """Read a JSONL file backwards and return its newest rate-limit record."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            position = stream.tell()
            pending = b""
            while position > 0:
                size = min(64 * 1024, position)
                position -= size
                stream.seek(position)
                pending = stream.read(size) + pending
                lines = pending.split(b"\n")
                pending = lines[0]
                for raw in reversed(lines[1:]):
                    usage = _usage_from_line(raw)
                    if usage:
                        return usage
            return _usage_from_line(pending)
    except (OSError, PermissionError):
        return None


def _usage_from_line(raw: bytes) -> Usage | None:
    if b'"rate_limits"' not in raw or b'"token_count"' not in raw:
        return None
    try:
        record = json.loads(raw)
        payload = record.get("payload", {})
        limits = payload.get("rate_limits") or {}
        primary = limits.get("primary") or {}
        if "used_percent" not in primary:
            return None
        return Usage(
            used_percent=float(primary["used_percent"]),
            window_minutes=int(primary.get("window_minutes") or 0),
            resets_at=int(primary.get("resets_at") or 0),
            timestamp=_parse_timestamp(record["timestamp"]),
            plan_type=str(limits.get("plan_type") or ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def find_latest_usage(home: Path | None = None) -> Usage | None:
    sessions = (home or codex_home()) / "sessions"
    if not sessions.exists():
        return None
    try:
        candidates = sorted(
            sessions.rglob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True
        )[:24]
    except OSError:
        return None

    found = [usage for path in candidates if (usage := _read_latest_usage(path))]
    return max(found, key=lambda item: item.timestamp) if found else None


def _get_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def _account_from_payloads(usage_data: dict, credits_data: dict) -> AccountUsage:
    window = usage_data["rate_limit"]["primary_window"]
    usage = Usage(
        used_percent=float(window["used_percent"]),
        window_minutes=round(int(window["limit_window_seconds"]) / 60),
        resets_at=int(window["reset_at"]),
        timestamp=datetime.now(timezone.utc),
        plan_type=str(usage_data.get("plan_type") or ""),
    )
    credits = []
    for item in credits_data.get("credits") or []:
        if item.get("status") != "available" or not item.get("expires_at"):
            continue
        credits.append(
            ResetCredit(
                title=str(item.get("title") or "Full reset"),
                expires_at=_parse_timestamp(item["expires_at"]),
            )
        )
    credits.sort(key=lambda item: item.expires_at)
    return AccountUsage(
        usage=usage,
        reset_count=int(credits_data.get("available_count", len(credits))),
        reset_credits=tuple(credits),
    )


def fetch_account_usage(home: Path | None = None) -> AccountUsage | None:
    """Fetch the same usage/reset-credit snapshot shown by Codex."""
    try:
        auth = json.loads(((home or codex_home()) / "auth.json").read_text(encoding="utf-8"))
        tokens = auth["tokens"]
        headers = {
            "Authorization": f"Bearer {tokens['access_token']}",
            "ChatGPT-Account-Id": tokens["account_id"],
            "User-Agent": "CodexUsageWidget/1.0",
        }
        base = "https://chatgpt.com/backend-api"
        usage_data = _get_json(base + "/wham/usage", headers)
        credits_data = _get_json(base + "/wham/rate-limit-reset-credits", headers)
        return _account_from_payloads(usage_data, credits_data)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None


def window_label(minutes: int) -> str:
    if minutes >= 28 * 24 * 60:
        return "월간 한도"
    if minutes >= 24 * 60:
        days = round(minutes / (24 * 60))
        return f"{days}일 한도"
    if minutes >= 60:
        return f"{round(minutes / 60)}시간 한도"
    return "사용 한도"


def fetch_utc_from_worldtimeapi(url: str) -> datetime:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return datetime.fromisoformat(payload["utc_datetime"]) + timedelta(seconds=(time.perf_counter() - started) / 2)


def fetch_utc_from_date_header(url: str) -> datetime:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=8) as response:
        date_header = response.headers.get("Date")
    if not date_header:
        raise ValueError("missing Date header")
    return email.utils.parsedate_to_datetime(date_header).astimezone(timezone.utc) + timedelta(seconds=(time.perf_counter() - started) / 2)


def fetch_server_utc() -> datetime:
    last_error: Exception | None = None
    for url in TIME_SOURCES:
        try:
            return fetch_utc_from_worldtimeapi(url) if "worldtimeapi.org" in url else fetch_utc_from_date_header(url)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
    raise RuntimeError(f"all time sources failed: {last_error}")


def nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> datetime:
    current = datetime(year, month, 1, tzinfo=timezone.utc)
    return current + timedelta(days=(weekday - current.weekday()) % 7 + (nth - 1) * 7)


def last_weekday_of_month(year: int, month: int, weekday: int) -> datetime:
    current = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def pacific_offset_seconds(utc_now: datetime) -> int:
    # The final dual-watch build follows British Columbia's 2026 permanent daylight-time rule.
    if utc_now >= datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc):
        return -7 * 3600
    dst_start = nth_weekday_of_month(utc_now.year, 3, 6, 2).replace(hour=10)
    dst_end = nth_weekday_of_month(utc_now.year, 11, 6, 1).replace(hour=9)
    return -7 * 3600 if dst_start <= utc_now < dst_end else -8 * 3600


def london_offset_seconds(utc_now: datetime) -> int:
    bst_start = last_weekday_of_month(utc_now.year, 3, 6).replace(hour=1)
    bst_end = last_weekday_of_month(utc_now.year, 10, 6).replace(hour=1)
    return 3600 if bst_start <= utc_now < bst_end else 0


def offset_seconds_for_zone(tz_name: str, utc_now: datetime) -> int:
    if tz_name == "Asia/Seoul":
        return 9 * 3600
    if tz_name == "America/Vancouver":
        return pacific_offset_seconds(utc_now)
    if tz_name == "Europe/London":
        return london_offset_seconds(utc_now)
    return 0


def format_clock(utc_now: datetime, tz_name: str) -> tuple[str, str]:
    local = utc_now + timedelta(seconds=offset_seconds_for_zone(tz_name, utc_now))
    return local.strftime("%H:%M:%S"), local.strftime("%a, %b %d")


def rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class UsageWidget:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            self.root.configure(bg="#ffffff")

        self.settings_path = Path(os.environ.get("APPDATA", Path.home())) / "CodexUsageWidget" / "settings.json"
        self.settings = self._load_settings()
        self.always_on_top = tk.BooleanVar(value=bool(self.settings.get("always_on_top", True)))
        self.expanded = False
        self.usage: Usage | None = None
        self.reset_count: int | None = None
        self.reset_credits: tuple[ResetCredit, ...] = ()
        self.fetching = False
        self.after_id: str | None = None
        self.clock_tick_after_id: str | None = None
        self.clock_sync_after_id: str | None = None
        self.clock_syncing = False
        self.synced_utc = datetime.now(timezone.utc)
        self.synced_perf = time.perf_counter()
        self.current_height = COLLAPSED_HEIGHT
        self._place_window()

        self.canvas = tk.Canvas(
            self.root, width=WIDTH, height=COLLAPSED_HEIGHT, bg=TRANSPARENT,
            highlightthickness=0, bd=0,
        )
        self.canvas.pack()
        self._bind_actions()
        self._drag_origin: tuple[int, int, int, int] | None = None
        self.refresh()
        self.tick_clock()
        self.sync_clock_now()

    def _draw(self) -> None:
        row_count = len(self.reset_credits) if self.expanded else 0
        height = COLLAPSED_HEIGHT + row_count * RESET_ROW_HEIGHT
        if height != self.current_height:
            self.current_height = height
            self.canvas.configure(height=height)
            self.root.geometry(f"{WIDTH}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")

        self.canvas.delete("all")
        rounded_rect(self.canvas, 2, 2, WIDTH - 2, height - 2, 15, fill="#ffffff", outline="#dddddd", width=1)
        utc_now = self.current_utc()
        for index, (title, tz_name, accent) in enumerate(WATCH_ZONES):
            x = index * PANEL_WIDTH
            if index:
                self.canvas.create_line(x, 5, x, CLOCK_HEIGHT - 5, fill="#ededed")
            self.canvas.create_oval(x + 7, 7, x + 12, 12, fill=accent, outline=accent)
            self.canvas.create_text(x + 16, 10, text=title, anchor="w", fill="#777777", font=("Segoe UI", 6, "bold"))
            clock_text, date_text = format_clock(utc_now, tz_name)
            self.canvas.create_text(x + PANEL_WIDTH // 2, 30, text=clock_text, fill="#202124", font=("Consolas", 11, "bold"), tags=(f"clock_time_{index}",))
            self.canvas.create_text(x + PANEL_WIDTH // 2, 48, text=date_text, fill="#888888", font=("Segoe UI", 6), tags=(f"clock_date_{index}",))
        self.canvas.create_line(12, CLOCK_HEIGHT, WIDTH - 12, CLOCK_HEIGHT, fill="#ededed")

        usage_y = CLOCK_HEIGHT
        self.canvas.create_text(16, usage_y + 17, text="CODEX", anchor="w", fill="#202124", font=("Segoe UI", 9, "bold"))
        label = window_label(self.usage.window_minutes) if self.usage else "월간 한도"
        self.canvas.create_text(65, usage_y + 17, text=label, anchor="w", fill="#777777", font=("Malgun Gothic", 8))
        percent = f"{self.usage.remaining_percent}%" if self.usage else "--%"
        self.canvas.create_text(WIDTH - 37, usage_y + 17, text=percent, anchor="e", fill="#202124", font=("Malgun Gothic", 13, "bold"))
        self.canvas.create_text(WIDTH - 25, usage_y + 17, text="남음", anchor="w", fill="#777777", font=("Malgun Gothic", 7))
        self.canvas.create_oval(WIDTH - 24, 4, WIDTH - 7, 21, fill="#f4f4f4", outline="", tags=("close",))
        self.canvas.create_text(WIDTH - 15.5, 12.5, text="×", fill="#666666", font=("Segoe UI", 10, "bold"), tags=("close",))

        rounded_rect(self.canvas, 16, usage_y + 38, WIDTH - 16, usage_y + 46, 4, fill="#e6e6e6", outline="")
        bar_right = 17 if not self.usage else 16 + max(1, (WIDTH - 32) * self.usage.remaining_percent / 100)
        rounded_rect(self.canvas, 16, usage_y + 38, bar_right, usage_y + 46, 4, fill="#202124", outline="")
        if self.usage and self.usage.resets_at:
            reset = datetime.fromtimestamp(self.usage.resets_at)
            updated = self.usage.timestamp.astimezone().strftime("%H:%M 갱신")
            status = f"{reset.month}월 {reset.day}일 재설정  ·  {updated}"
        else:
            status = "Codex 데이터를 찾는 중…"
        self.canvas.create_text(16, usage_y + 67, text=status, anchor="w", fill="#777777", font=("Malgun Gothic", 8))
        self.canvas.create_text(WIDTH - 16, usage_y + 67, text="↻", anchor="e", fill="#888888", font=("Segoe UI Symbol", 12), tags=("refresh",))

        self.canvas.create_line(12, usage_y + 85, WIDTH - 12, usage_y + 85, fill="#ededed")
        self.canvas.create_text(16, usage_y + 101, text="사용 한도 재설정", anchor="w", fill="#202124", font=("Malgun Gothic", 8, "bold"), tags=("reset_header",))
        if self.reset_count is None:
            badge = "확인 중"
            badge_color = "#f1f1f1"
            badge_text = "#777777"
        else:
            badge = f"{self.reset_count}회 사용 가능"
            badge_color = "#d9f5e3"
            badge_text = "#087a3e"
        badge_left = WIDTH - 39 - max(52, len(badge) * 8)
        rounded_rect(self.canvas, badge_left, usage_y + 91, WIDTH - 29, usage_y + 111, 10, fill=badge_color, outline="", tags=("reset_header",))
        self.canvas.create_text((badge_left + WIDTH - 29) / 2, usage_y + 101, text=badge, fill=badge_text, font=("Malgun Gothic", 8, "bold"), tags=("reset_header",))
        arrow = "⌃" if self.expanded else "⌄"
        self.canvas.create_text(WIDTH - 14, usage_y + 100, text=arrow, anchor="e", fill="#888888", font=("Segoe UI Symbol", 10), tags=("reset_header",))

        for index, credit in enumerate(self.reset_credits if self.expanded else ()):
            top = COLLAPSED_HEIGHT + index * RESET_ROW_HEIGHT
            self.canvas.create_line(12, top, WIDTH - 12, top, fill="#ededed")
            self.canvas.create_text(16, top + 15, text=credit.title, anchor="w", fill="#202124", font=("Segoe UI", 8, "bold"))
            expires = credit.expires_at.astimezone()
            self.canvas.create_text(16, top + 33, text=f"{expires.month}. {expires.day}. 만료", anchor="w", fill="#777777", font=("Malgun Gothic", 8))
            rounded_rect(self.canvas, WIDTH - 82, top + 11, WIDTH - 16, top + 37, 11, fill="#202124", outline="")
            self.canvas.create_text(WIDTH - 49, top + 24, text="사용 가능", fill="#ffffff", font=("Malgun Gothic", 8, "bold"))

        self.canvas.tag_bind("close", "<Button-1>", lambda _event: self.close())
        self.canvas.tag_bind("refresh", "<Button-1>", lambda _event: self.refresh())
        self.canvas.tag_bind("reset_header", "<Button-1>", lambda _event: self._toggle_expanded())

    def _bind_actions(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)
        self.canvas.bind("<Button-3>", self._show_menu)

    def _show_menu(self, event: tk.Event) -> None:
        menu = tk.Menu(self.root, tearoff=False, font=("Malgun Gothic", 9))
        menu.add_command(label="지금 새로고침", command=self.refresh)
        menu.add_checkbutton(label="항상 위에 표시", variable=self.always_on_top, command=self._toggle_topmost)
        menu.add_separator()
        menu.add_command(label="끝내기", command=self.close)
        menu.tk_popup(event.x_root, event.y_root)

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.always_on_top.get())
        self.settings["always_on_top"] = self.always_on_top.get()
        self._save_settings()

    def _start_drag(self, event: tk.Event) -> None:
        tags = self.canvas.gettags("current")
        if any(tag in tags for tag in ("close", "refresh", "reset_header")):
            return
        self._drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        if not self._drag_origin:
            return
        start_x, start_y, window_x, window_y = self._drag_origin
        self.root.geometry(f"+{window_x + event.x_root - start_x}+{window_y + event.y_root - start_y}")

    def _end_drag(self, _event: tk.Event) -> None:
        if self._drag_origin:
            self.settings.update({"x": self.root.winfo_x(), "y": self.root.winfo_y()})
            self._save_settings()
        self._drag_origin = None

    def _place_window(self) -> None:
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        x = int(self.settings.get("x", screen_w - WIDTH - 24))
        y = int(self.settings.get("y", 24))
        x = max(0, min(x, screen_w - WIDTH))
        y = max(0, y)
        self.root.geometry(f"{WIDTH}x{COLLAPSED_HEIGHT}+{x}+{y}")

    def refresh(self) -> None:
        local = find_latest_usage()
        if local:
            self.usage = local
        self._draw()
        if not self.fetching:
            self.fetching = True
            threading.Thread(target=self._fetch_in_background, daemon=True).start()
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.after_id = self.root.after(REFRESH_MS, self.refresh)

    def current_utc(self) -> datetime:
        return self.synced_utc + timedelta(seconds=time.perf_counter() - self.synced_perf)

    def tick_clock(self) -> None:
        utc_now = self.current_utc()
        for index, (_, tz_name, _) in enumerate(WATCH_ZONES):
            clock_text, date_text = format_clock(utc_now, tz_name)
            self.canvas.itemconfigure(f"clock_time_{index}", text=clock_text)
            self.canvas.itemconfigure(f"clock_date_{index}", text=date_text)
        self.clock_tick_after_id = self.root.after(TIME_TICK_INTERVAL_MS, self.tick_clock)

    def sync_clock_now(self) -> None:
        if self.clock_syncing:
            return
        self.clock_syncing = True
        started = time.perf_counter()

        def worker() -> None:
            try:
                utc_now = fetch_server_utc()
                finished = time.perf_counter()
                adjusted_perf = finished - ((finished - started) / 2)
                self.root.after(0, self._apply_clock_sync, utc_now, adjusted_perf)
            except Exception:
                self.root.after(0, self._apply_clock_sync, datetime.now(timezone.utc), time.perf_counter())

        threading.Thread(target=worker, daemon=True).start()

    def _apply_clock_sync(self, utc_now: datetime, synced_perf: float) -> None:
        self.synced_utc = utc_now.astimezone(timezone.utc)
        self.synced_perf = synced_perf
        self.clock_syncing = False
        if self.clock_sync_after_id:
            self.root.after_cancel(self.clock_sync_after_id)
        self.clock_sync_after_id = self.root.after(TIME_SYNC_INTERVAL_MS, self.sync_clock_now)

    def _fetch_in_background(self) -> None:
        result = fetch_account_usage()
        try:
            self.root.after(0, self._apply_account_usage, result)
        except tk.TclError:
            pass

    def _apply_account_usage(self, result: AccountUsage | None) -> None:
        self.fetching = False
        if result:
            self.usage = result.usage
            self.reset_count = result.reset_count
            self.reset_credits = result.reset_credits
        self._draw()

    def _toggle_expanded(self) -> None:
        if self.reset_credits:
            self.expanded = not self.expanded
            self._draw()

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(self.settings, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def close(self) -> None:
        if self.after_id:
            self.root.after_cancel(self.after_id)
        if self.clock_tick_after_id:
            self.root.after_cancel(self.clock_tick_after_id)
        if self.clock_sync_after_id:
            self.root.after_cancel(self.clock_sync_after_id)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    try:
        UsageWidget().run()
    except tk.TclError as exc:
        messagebox.showerror(APP_NAME, f"위젯을 시작할 수 없습니다.\n{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
