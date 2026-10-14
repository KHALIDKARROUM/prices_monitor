"""Record a captioned Price Monitor walkthrough against disposable sample data.

Requires the project's browser extras, Pillow, Chrome/Chromium, and ffmpeg.
The isolated server uses a temporary SQLite database and never contacts providers.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
import gc
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "presentations" / "price-monitor-demo.mp4"
POSTER = ROOT / "presentations" / "price-monitor-demo-poster.png"
DASHBOARD_PREVIEW = ROOT / "presentations" / "price-monitor-demo-dashboard.png"
SIZE = (1600, 900)
FONT = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
FONT_BOLD = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguisb.ttf"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT), size)


def sample_quote(supplier: str, reference: str, price: float, transport: int) -> dict:
    today = date.today()
    return {
        "product": "bitume", "supplier": supplier, "reference": reference,
        "grade": "50/70", "specification": "EN 12591", "origin": "Morocco",
        "incoterm": "DAP", "incoterm_place": "Casablanca", "destination": "Casablanca",
        "payment_terms": "30 days", "packaging": "bulk", "document_type": "quotation",
        "quote_date": today.isoformat(), "valid_until": (today + timedelta(days=30)).isoformat(),
        "delivery_date": (today + timedelta(days=14)).isoformat(),
        "quantity": 100, "unit_price": price, "currency": "USD", "exchange_rate": 1,
        "transport_cost": transport, "insurance_cost": 210, "inspection_cost": 180,
        "duties_cost": 340, "storage_cost": 95, "heating_cost": 120,
        "actor": "Demo team", "notes": "Illustrative offer for the video walkthrough.",
    }


def seed_sample_data(db_path: Path) -> None:
    from price_monitor.database import Database
    from price_monitor.bitumen import benchmark_payload
    from price_monitor.services.alerts import AlertManager

    db = Database(db_path)
    connection = db.connect()
    try:
        connection.execute("DELETE FROM price_observations")
        connection.execute("DELETE FROM market_benchmarks")
        connection.commit()
    finally:
        connection.close()

    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    for offset in range(150, -1, -1):
        elapsed = 150 - offset
        day = today - timedelta(days=offset)
        values = (
            ("brent", "brent_spot", 78 + elapsed * .035 + 2.3 * math.sin(elapsed / 7), "EIA/FRED - DCOILBRENTEU"),
            ("brent", "brent_futures", 79.6 + elapsed * .036 + 2.2 * math.sin(elapsed / 7 + .3), "Yahoo Finance - Brent futures BZ=F"),
            ("gasoil", "diesel_spot", 2.48 + elapsed * .0012 + .095 * math.sin(elapsed / 8 + 1), "EIA/FRED - DDFUELNYH"),
        )
        for second, (product, instrument, price, source) in enumerate(values):
            db.insert_observation({"product": product, "instrument": instrument, "price": round(price, 4),
                                   "source": source, "source_date": day.date().isoformat(),
                                   "collected_at": (day + timedelta(seconds=second)).isoformat()})

    first_month = date.today().year * 12 + date.today().month - 1
    for offset in range(59, -1, -1):
        month_index = first_month - offset
        month = date(month_index // 12, month_index % 12 + 1, 1)
        value = 294 + (59 - offset) * .28 + 5.5 * math.sin((59 - offset) / 3.8)
        db.insert_benchmark(benchmark_payload(month.isoformat(), round(value, 2)))

    first = db.create_quote(sample_quote("Sample Supplier A", "DEMO-A-101", 515, 1420))
    db.create_quote(sample_quote("Sample Supplier B", "DEMO-B-202", 507, 2300))
    db.attach_quote_document(first["id"], {
        "expected_version": first["version"], "filename": "sample-quotation.txt",
        "document_type": "quotation", "actor": "Demo team",
        "change_reason": "Attach sample quotation",
        "content_base64": base64.b64encode(b"Illustrative sample quotation for Price Monitor demo.\n").decode(),
    })
    AlertManager(db).evaluate_prices(db.latest_by_instrument(), dispatch=False)


def server_environment(work: Path) -> dict[str, str]:
    values = {
        "PRICE_MONITOR_DB": str(work / "demo.db"), "PRICE_MONITOR_TEST_READY": str(work / "port.txt"),
        "PRICE_MONITOR_DB_BACKEND": "sqlite", "PRICE_MONITOR_DATA": str(work / "absent.csv"),
        "PRICE_MONITOR_BITUMEN_DATA": str(work / "absent-bitumen.csv"),
        "PRICE_MONITOR_ENV": "development", "PRICE_MONITOR_HOST": "127.0.0.1",
        "PRICE_MONITOR_TEAM_MODE": "true", "PRICE_MONITOR_SECURE_COOKIES": "false",
        "PRICE_MONITOR_PUBLIC_ORIGIN": "", "PRICE_MONITOR_API_KEY": "",
        "PRICE_MONITOR_URL_SCHEME": "http", "PRICE_MONITOR_TRUSTED_PROXY": "",
        "PRICE_MONITOR_RATE_LIMIT_ENABLED": "false", "PRICE_MONITOR_DEMO": "true",
        "PRICE_MONITOR_ALERT_EMAIL_TO": "", "PRICE_MONITOR_ALERT_WEBHOOK_URL": "",
        "PRICE_MONITOR_DIGEST_EMAIL_TO": "", "PRICE_MONITOR_ALERT_THRESHOLDS": "{}",
        "FRED_API_KEY": "", "EIA_API_KEY": "", "ALPHA_VANTAGE_API_KEY": "",
    }
    return {**os.environ, **values}


def draw_card(path: Path, *, outro: bool = False) -> None:
    image = Image.new("RGB", SIZE)
    pixels = image.load()
    for y in range(SIZE[1]):
        for x in range(SIZE[0]):
            glow = max(0, 1 - math.hypot(x - 1260, y - 160) / 900)
            pixels[x, y] = (int(10 + 12 * glow), int(23 + 42 * glow), int(32 + 38 * glow))
    draw = ImageDraw.Draw(image)
    points = [(90 + i * 26, 635 - 1.5 * i - 35 * math.sin(i / 3.3)) for i in range(55)]
    draw.line(points, fill="#1a756e", width=6, joint="curve")
    for x, y in points[::7]:
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#48d2b6")
    draw.rounded_rectangle((96, 94, 260, 137), radius=19, fill="#174840")
    draw.text((116, 101), "PRODUCT DEMO", fill="#7ce3c9", font=font(20, True))
    if outro:
        draw.text((95, 245), "From data to a clearer", fill="#f4fbfb", font=font(76, True))
        draw.text((95, 342), "purchase decision.", fill="#f4fbfb", font=font(76, True))
        draw.text((100, 484), "Explore the open-source project", fill="#b3c7cb", font=font(32))
        draw.text((100, 545), "github.com/KHALIDKARROUM/prices_monitor", fill="#54dac0", font=font(33, True))
    else:
        draw.text((95, 238), "PRICE MONITOR", fill="#f4fbfb", font=font(94, True))
        draw.text((100, 376), "Market signals. Forecasts. Supplier decisions.", fill="#aad0cf", font=font(38))
        draw.text((100, 466), "A guided walkthrough with illustrative sample data", fill="#69d8bd", font=font(27))
    draw.text((100, 824), "DIESEL  /  BRENT  /  BITUMEN", fill="#8ca7ac", font=font(21, True))
    image.save(path)


def draw_caption(path: Path, heading: str, detail: str) -> None:
    image = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((307, 727, 1547, 869), radius=22, fill=(12, 28, 37, 242), outline=(66, 111, 116, 200), width=2)
    draw.rounded_rectangle((307, 727, 315, 869), radius=4, fill="#45d5b8")
    draw.text((344, 752), heading, fill="#f3fbfa", font=font(36, True))
    draw.text((344, 810), detail, fill="#b5ccd0", font=font(23))
    draw.rounded_rectangle((1345, 752, 1515, 786), radius=13, fill=(25, 85, 75, 255))
    draw.text((1361, 758), "SAMPLE DATA", fill="#88e4cf", font=font(16, True))
    image.save(path)


def record(work: Path, base: str) -> tuple[Path, list[tuple[float, float, str, str]]]:
    raw_dir = work / "raw"
    raw_dir.mkdir()
    scenes: list[tuple[float, float, str, str]] = []
    with sync_playwright() as runner:
        chrome = os.environ.get("PRICE_MONITOR_TEST_BROWSER_EXECUTABLE", "C:/Program Files/Google/Chrome/Application/chrome.exe")
        launch = {"executable_path": chrome} if Path(chrome).exists() else {"channel": "chromium"}
        browser = runner.chromium.launch(**launch)
        sign_in = browser.new_context(viewport={"width": SIZE[0], "height": SIZE[1]})
        page = sign_in.new_page()
        page.goto(base)
        page.locator("#sign-in").wait_for(state="visible")
        page.locator("#sign-in [name=username]").fill("owner")
        page.locator("#sign-in [name=password]").fill("browser-test-password-2026")
        page.locator("#sign-in-form button[type=submit]").click()
        page.locator(".app-shell").wait_for(state="visible")
        auth_state = sign_in.storage_state()
        sign_in.close()

        context = browser.new_context(storage_state=auth_state, viewport={"width": SIZE[0], "height": SIZE[1]},
                                      record_video_dir=str(raw_dir), record_video_size={"width": SIZE[0], "height": SIZE[1]})
        context.add_init_script("localStorage.setItem('priceMonitorTheme', 'dark')")
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
        page = context.new_page()
        started = time.monotonic()
        page.goto(base)
        page.locator(".app-shell").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#metric-grid .metric-card') !== null && window.Chart && Chart.getChart('price-chart')")

        def scene(heading: str, detail: str, seconds: float = 4.5) -> None:
            at = time.monotonic() - started
            page.wait_for_timeout(int(seconds * 1000))
            scenes.append((at, time.monotonic() - started, heading, detail))

        page.screenshot(path=str(DASHBOARD_PREVIEW))
        scene("A single view of key markets", "Tracked instruments, recent prices, and what needs attention.", 5)
        page.locator("[data-chart-mode=absolute]").click()
        scene("Compare prices in their own units", "Brent, diesel, and the bitumen index stay clearly separated.", 4)
        page.locator("#market-brent-instrument").select_option("brent_futures")
        page.wait_for_timeout(800)
        scene("Choose the instrument you mean", "Spot prices and futures keep separate histories and alerts.", 4)

        page.locator("nav").get_by_role("link", name="Trends", exact=True).click()
        page.wait_for_function("document.querySelector('#forecast-brent-quality')?.textContent?.trim().length > 0")
        page.wait_for_timeout(900)
        scene("Forecasts with honest validation", "Each model is checked against the last known price.", 6)

        page.locator("nav").get_by_role("link", name="Alerts", exact=True).click()
        page.wait_for_timeout(900)
        scene("Alerts follow the right series", "Rules target a specific instrument and track its own state.", 4)

        page.locator("nav").get_by_role("link", name="Suppliers", exact=True).click()
        page.wait_for_function("document.querySelectorAll('#quote-list tr').length >= 2")
        page.wait_for_timeout(800)
        scene("Turn offers into structured quotes", "Terms, validity, documents, and landed costs stay together.", 5)
        page.locator("#quote-list input[type=checkbox]").nth(0).check()
        page.locator("#quote-list input[type=checkbox]").nth(1).check()
        page.locator("#quote-compare").click()
        page.locator("#quote-comparison").wait_for(state="visible")
        page.wait_for_timeout(850)
        scene("Compare equivalent offers side by side", "Rankings include transport, insurance, duties, and more.", 6)

        page.locator("#quote-list [data-open-quote]").first.click()
        page.locator("#quote-detail").wait_for(state="visible")
        page.wait_for_timeout(850)
        scene("Keep the evidence with the quote", "Supporting files and every correction remain in the record.", 4)
        page.locator("#quote-budget").click()
        page.wait_for_timeout(850)
        page.locator("#procurement-form button[type=submit]").click()
        page.locator("#procurement-summary").wait_for(state="visible")
        scene("See the full purchase impact", "A budget rolls up merchandise and applicable extra costs.", 5)

        page.locator("nav").get_by_role("link", name="Markets", exact=True).click()
        page.wait_for_timeout(850)
        page.locator(".app-shell [data-theme-toggle]").click()
        page.wait_for_timeout(700)
        scene("Built for everyday use", "Responsive charts, accessible summaries, and light or dark mode.", 4)

        raw = page.video.path()
        context.close()
        browser.close()
    return Path(raw), scenes


def render_video(work: Path, raw: Path, scenes: list[tuple[float, float, str, str]]) -> None:
    ffmpeg = os.environ.get("PRICE_MONITOR_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Set PRICE_MONITOR_FFMPEG to an ffmpeg executable.")
    title = POSTER
    outro = work / "outro.png"
    draw_card(title)
    draw_card(outro, outro=True)
    captions = []
    for index, (_, _, heading, detail) in enumerate(scenes):
        path = work / f"caption-{index:02}.png"
        draw_caption(path, heading, detail)
        captions.append(path)

    command = [ffmpeg, "-y", "-loop", "1", "-t", "3.5", "-i", str(title), "-i", str(raw),
               "-loop", "1", "-t", "4", "-i", str(outro)]
    for caption in captions:
        command += ["-i", str(caption)]
    filters = ["[1:v]fps=30,scale=1600:900,setsar=1,setpts=PTS-STARTPTS[base]"]
    previous = "base"
    for index, (start, end, _, _) in enumerate(scenes):
        next_label = f"c{index}"
        filters.append(f"[{previous}][{index + 3}:v]overlay=0:0:eof_action=repeat:enable='between(t,{start:.2f},{end:.2f})'[{next_label}]")
        previous = next_label
    filters += ["[0:v]fps=30,scale=1600:900,setsar=1,format=yuv420p[title]",
                "[2:v]fps=30,scale=1600:900,setsar=1,format=yuv420p[outro]",
                f"[title][{previous}][outro]concat=n=3:v=1:a=0[v]"]
    command += ["-filter_complex", ";".join(filters), "-map", "[v]", "-r", "30", "-c:v", "libx264",
                "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-level:v", "4.2",
                "-video_track_timescale", "30000", "-movflags", "+faststart", "-an", str(OUTPUT)]
    encoded = subprocess.run(command, capture_output=True, text=True)
    if encoded.returncode:
        raise RuntimeError(encoded.stderr[-4000:])


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="price-monitor-video-") as temporary:
        work = Path(temporary)
        ready = work / "port.txt"
        log_path = work / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "tests" / "browser_server.py")], cwd=ROOT,
                                       env=server_environment(work), stdout=log, stderr=log,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + 30
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                if not ready.exists():
                    raise RuntimeError(log_path.read_text(encoding="utf-8"))
                seed_sample_data(work / "demo.db")
                raw, scenes = record(work, f"http://127.0.0.1:{ready.read_text()}")
                render_video(work, raw, scenes)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                gc.collect()
    print(f"Video: {OUTPUT}")
    print(f"Poster: {POSTER}")


if __name__ == "__main__":
    main()
