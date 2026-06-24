import hashlib
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from .config import DATA_DIR, CORRUPTED_PDFS_PATH

logger = logging.getLogger(__name__)


@dataclass
class AdaptiveRateLimiter:
    max_qps: float = 2.0
    min_qps: float = 0.05
    window_sec: float = 2.0
    recovery_threshold: int = 10
    max_backoff_factor: int = 16

    _slots: deque = field(default_factory=lambda: deque(maxlen=2000))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _backoff: int = 1
    _consecutive_ok: int = 0
    _consecutive_err: int = 0

    def _trim_slots(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._slots and self._slots[0] < cutoff:
            self._slots.popleft()

    @property
    def backoff(self) -> int:
        return self._backoff

    def _effective_qps(self) -> float:
        b = min(self._backoff, self.max_backoff_factor)
        return self.max_qps / b if b > 0 else self.max_qps

    def _sliding_delay(self) -> float:
        now = time.monotonic()
        self._trim_slots(now)
        capacity = int(self._effective_qps() * self.window_sec)
        if len(self._slots) >= capacity:
            wait = self._slots[0] + self.window_sec - now
            return max(wait, 0.0)
        return 0.0

    def acquire(self) -> float:
        with self._lock:
            wait = self._sliding_delay()
            if wait > 0:
                time.sleep(wait)
            now = time.monotonic()
            self._slots.append(now)
            return wait

    def report_ok(self) -> None:
        with self._lock:
            self._consecutive_ok += 1
            self._consecutive_err = 0
            if self._consecutive_ok >= self.recovery_threshold and self._backoff > 1:
                self._backoff //= 2
                self._consecutive_ok = 0
                logger.info("Rate recovered: backoff=%d, qps=%.2f", self._backoff, self._effective_qps())

    def report_err(self, status: int) -> None:
        with self._lock:
            self._consecutive_err += 1
            self._consecutive_ok = 0
            if status in (429, 403):
                self._backoff = min(self._backoff * 2, self.max_backoff_factor)
                logger.warning("HTTP %d → backoff=%d, qps=%.2f", status, self._backoff, self._effective_qps())
            elif status >= 500:
                self._backoff = min(self._backoff + 1, self.max_backoff_factor)
                logger.warning("HTTP %d → light backoff=%d", status, self._backoff)


class EastMoneyAPI:
    BASE_URL = "https://reportapi.eastmoney.com/report/list"

    def __init__(self, limiter: AdaptiveRateLimiter, session: requests.Session):
        self.limiter = limiter
        self.session = session
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://data.eastmoney.com/report/",
        }

    def fetch_list(
        self, page: int = 1, page_size: int = 50,
        date_from: str = "", date_to: str = "",
    ) -> Optional[list[dict]]:
        params = {
            "pageNo": page,
            "pageSize": min(page_size, 500),
            "qType": 0,
        }
        if date_from:
            params["beginTime"] = date_from
        if date_to:
            params["endTime"] = date_to

        self.limiter.acquire()
        try:
            resp = self.session.get(self.BASE_URL, params=params, headers=self._headers, timeout=15)
            if resp.status_code in (429, 403):
                self.limiter.report_err(resp.status_code)
                return None
            resp.raise_for_status()
            self.limiter.report_ok()
            data = resp.json()
            items = data.get("data", [])
            return items if isinstance(items, list) else []
        except requests.exceptions.Timeout:
            logger.warning("Page %d timeout", page)
            self.limiter.report_err(504)
            return None
        except requests.exceptions.RequestException as e:
            logger.warning("Page %d request failed: %s", page, e)
            self.limiter.report_err(500)
            return None
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Page %d parse failed: %s", page, e)
            return None

    def download_pdf(self, info_code: str, save_path: Path) -> bool:
        if not info_code:
            return False
        self.limiter.acquire()
        ts = int(time.time() * 1000)
        pdf_url = f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf?{ts}.pdf"
        try:
            resp = self.session.get(pdf_url, headers=self._headers, timeout=30, stream=True)
            if resp.status_code in (429, 403):
                self.limiter.report_err(resp.status_code)
                return False
            resp.raise_for_status()
            self.limiter.report_ok()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            logger.warning("PDF download failed [%s]: %s", info_code, e)
            return False


class DownloadState:
    def __init__(self, state_path: Path):
        self.state_path = state_path
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if self.state_path.exists():
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info("Loaded download state: %d already downloaded", len(self._data))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def save(self) -> None:
        with self._lock:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self.state_path)

    def is_downloaded(self, key: str) -> bool:
        return key in self._data

    def mark_downloaded(self, key: str, ts: str) -> None:
        with self._lock:
            self._data[key] = ts

    @staticmethod
    def make_key(info_code: str) -> str:
        return hashlib.md5(info_code.encode()).hexdigest()

    @property
    def count(self) -> int:
        return len(self._data)


def load_corrupted_set() -> set[str]:
    codes: set[str] = set()
    if CORRUPTED_PDFS_PATH.exists():
        with open(CORRUPTED_PDFS_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        codes.add(entry.get("code", ""))
                    except json.JSONDecodeError:
                        pass
    return codes


class YanbaoDownloader:
    def __init__(
        self,
        output_dir: str | Path = "",
        max_qps: float = 2.0,
        min_qps: float = 0.05,
        max_pages: int = 0,
        page_size: int = 50,
    ):
        self.output_dir = Path(output_dir) if output_dir else DATA_DIR
        self.max_pages = max_pages
        self.page_size = page_size
        self._download_count = 0
        self._skip_count = 0
        self._error_count = 0

        self.limiter = AdaptiveRateLimiter(max_qps=max_qps, min_qps=min_qps)
        self.session = requests.Session()
        self.api = EastMoneyAPI(self.limiter, self.session)
        self.state = DownloadState(self.output_dir / ".download_state.json")
        self._corrupted = load_corrupted_set()

    def _process_item(self, item: dict):
        info_code = (item.get("infoCode") or "").strip()
        if not info_code:
            return

        key = DownloadState.make_key(info_code)
        if self.state.is_downloaded(key):
            self._skip_count += 1
            return

        if info_code in self._corrupted:
            self._skip_count += 1
            return

        title = (item.get("title") or "无标题").strip()
        org = (item.get("orgName") or "未知机构").strip()
        date = (item.get("publishDate", "") or "")[:10]

        date_prefix = date.replace("-", "")
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-()（）、").strip()[:60]
        org_dir = org.replace(" ", "_") or "unknown"

        json_path = self.output_dir / org_dir / f"{date_prefix}_{safe_title}.json"
        pdf_path = self.output_dir / org_dir / f"{date_prefix}_{safe_title}.pdf"

        # Save JSON metadata
        json_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "infoCode": info_code,
            "title": title,
            "org": org,
            "stock": (item.get("stockName") or "").strip(),
            "stockCode": (item.get("stockCode") or "").strip(),
            "industry": (item.get("indvInduName") or "").strip(),
            "emRating": (item.get("emRatingName") or "").strip(),
            "authors": item.get("author", []) or [],
            "date": date,
            "reportUrl": f"https://data.eastmoney.com/report/{item.get('stockCode','')}/{info_code}.html",
            "pdfUrl": f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # Download PDF
        pdf_ok = self.api.download_pdf(info_code, pdf_path)

        if pdf_ok:
            self.state.mark_downloaded(key, date)
            self._download_count += 1
            logger.info("[%d] %s | %s", self._download_count, org, title[:50])
        else:
            self._error_count += 1
            logger.warning("PDF failed, metadata saved: %s", title[:40])

    def run(
        self,
        date_from: str = "",
        date_to: str = "",
        category: str = "all",
    ):
        logger.info("Output dir: %s", self.output_dir)
        logger.info("QPS: %.1f (min %.2f)", self.limiter.max_qps, self.limiter.min_qps)
        logger.info("Date range: %s ~ %s", date_from or "unlimited", date_to or "unlimited")

        page = 1
        empty_pages = 0
        last_save = time.monotonic()

        while True:
            if self.max_pages > 0 and page > self.max_pages:
                logger.info("Reached page limit %d, stopping", self.max_pages)
                break

            items = self.api.fetch_list(
                page=page, page_size=self.page_size,
                date_from=date_from, date_to=date_to,
            )

            if items is None:
                empty_pages += 1
                logger.info("Page %d failed, retrying... (empty=%d)", page, empty_pages)
                time.sleep(5 * self.limiter.backoff)
                if empty_pages >= 10:
                    logger.error("10 consecutive failures, aborting")
                    break
                continue

            if not items:
                logger.info("Page %d empty, done", page)
                break

            empty_pages = 0
            logger.info("Page %d/%s | %d items", page, self.max_pages or "∞", len(items))

            for item in items:
                self._process_item(item)

                now = time.monotonic()
                if self._download_count % 20 == 0 and self._download_count > 0 and now - last_save > 10:
                    self.state.save()
                    last_save = now

            page += 1
            time.sleep(0.3 * self.limiter.backoff)

        self.state.save()
        self._print_summary()

    def fix_corrupted(self) -> list[str]:
        logger.info("Reading corrupted PDF list: %s", CORRUPTED_PDFS_PATH)
        entries: list[dict] = []
        if CORRUPTED_PDFS_PATH.exists():
            with open(CORRUPTED_PDFS_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        if not entries:
            logger.info("No corrupted PDFs to fix")
            return []

        logger.info("Found %d corrupted PDFs, attempting re-download...", len(entries))
        fixed: list[str] = []
        remaining: list[dict] = []
        for entry in entries:
            code = entry.get("code", "")
            pdf_path_str = entry.get("pdf_path", "")
            error = entry.get("error", "unknown")
            logger.info("Re-downloading %s (previous error: %s)", code, error)
            ok = self.api.download_pdf(code, Path(pdf_path_str))
            if ok:
                fixed.append(code)
                logger.info("  Fixed: %s", code)
            else:
                remaining.append(entry)
                logger.warning("  Still failed: %s", code)

        CORRUPTED_PDFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CORRUPTED_PDFS_PATH, "w", encoding="utf-8") as f:
            for entry in remaining:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info("Fixed %d / %d corrupted PDFs", len(fixed), len(entries))
        return fixed

    def _print_summary(self):
        logger.info("=" * 56)
        logger.info("Download complete")
        logger.info("  Downloaded: %d", self._download_count)
        logger.info("  Skipped:    %d (already exist)", self._skip_count)
        logger.info("  Failed:     %d", self._error_count)
        logger.info("  State:      %d entries", self.state.count)
        logger.info("  Output:     %s", self.output_dir.resolve())
        logger.info("=" * 56)
