"""
CupGet - Video Downloader API
A production-ready FastAPI backend powered by yt-dlp.
"""

import logging
import re
from contextlib import asynccontextmanager
from typing import Any

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl, field_validator

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cupget")


# ──────────────────────────────────────────────
# App lifecycle
# ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 CupGet API starting up …")
    yield
    logger.info("🛑 CupGet API shutting down …")


# ──────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────
app = FastAPI(
    title="CupGet Video Downloader API",
    description="Extract direct video download links from any supported platform.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ──────────────────────────────────────────────
# CORS — allow Flutter (or any client) to connect
# ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten in production if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────
class ExtractRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^https?://", v, re.IGNORECASE):
            raise ValueError("URL must start with http:// or https://")
        return v


class VideoFormat(BaseModel):
    format_id: str
    quality_label: str
    ext: str
    resolution: str | None
    fps: int | None
    vcodec: str | None
    acodec: str | None
    filesize: int | None
    url: str
    has_video: bool
    has_audio: bool


class ExtractResponse(BaseModel):
    title: str
    thumbnail: str | None
    duration: int | None        # seconds
    uploader: str | None
    platform: str | None
    formats: list[VideoFormat]


# ──────────────────────────────────────────────
# yt-dlp helpers
# ──────────────────────────────────────────────

# A modern, realistic browser User-Agent
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Shared ydl options — optimised for extraction only (no actual download)
_YDL_OPTS: dict[str, Any] = {
    # ── extraction behaviour ──────────────────
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,          # single video, not whole playlist
    "extract_flat": False,

    # ── bypass / anti-bot tricks ─────────────
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "geo_bypass": True,
    "geo_bypass_country": "US",

    # ── HTTP headers sent with every request ─
    "http_headers": {
        "User-Agent": _USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    },

    # ── retries & timeouts ───────────────────
    "retries": 3,
    "fragment_retries": 3,
    "socket_timeout": 30,

    # ── platform-specific cookies (optional) ─
    # "cookiefile": "/app/cookies.txt",   # uncomment and mount a cookies file

    # ── format selection (fetch ALL formats) ─
    "format": "bestvideo+bestaudio/best",
    "merge_output_format": "mp4",

    # ── output template (not used during info extraction) ─
    "outtmpl": "/tmp/%(id)s.%(ext)s",
}


def _build_quality_label(fmt: dict) -> str:
    """Return a human-readable quality label for a format dict."""
    height = fmt.get("height")
    if height:
        fps = fmt.get("fps")
        label = f"{height}p"
        if fps and fps > 30:
            label += f"{int(fps)}"
        return label

    # audio-only formats
    abr = fmt.get("abr")
    if abr:
        return f"Audio {int(abr)}kbps"

    note = fmt.get("format_note", "")
    if note:
        return note

    return fmt.get("format_id", "unknown")


def _parse_formats(raw_formats: list[dict]) -> list[VideoFormat]:
    """Convert yt-dlp format dicts into our VideoFormat schema."""
    result: list[VideoFormat] = []

    for fmt in raw_formats:
        url = fmt.get("url", "")
        if not url or url.startswith("javascript"):
            continue

        vcodec = fmt.get("vcodec", "none")
        acodec = fmt.get("acodec", "none")
        has_video = vcodec not in (None, "none", "")
        has_audio = acodec not in (None, "none", "")

        height = fmt.get("height")
        width = fmt.get("width")
        resolution = f"{width}x{height}" if (width and height) else None

        fps_raw = fmt.get("fps")
        fps = int(fps_raw) if fps_raw else None

        filesize = fmt.get("filesize") or fmt.get("filesize_approx")

        result.append(
            VideoFormat(
                format_id=str(fmt.get("format_id", "")),
                quality_label=_build_quality_label(fmt),
                ext=fmt.get("ext", "mp4"),
                resolution=resolution,
                fps=fps,
                vcodec=vcodec if vcodec not in (None, "none") else None,
                acodec=acodec if acodec not in (None, "none") else None,
                filesize=int(filesize) if filesize else None,
                url=url,
                has_video=has_video,
                has_audio=has_audio,
            )
        )

    # Sort: combined (video+audio) first, then by height desc, then audio-only
    def sort_key(f: VideoFormat):
        combined = 0 if (f.has_video and f.has_audio) else (1 if f.has_video else 2)
        height = int(f.resolution.split("x")[1]) if f.resolution else 0
        return (combined, -height)

    result.sort(key=sort_key)
    return result


def _detect_platform(url: str) -> str | None:
    patterns = {
        "YouTube": r"(youtube\.com|youtu\.be)",
        "TikTok": r"tiktok\.com",
        "Instagram": r"instagram\.com",
        "Twitter / X": r"(twitter\.com|x\.com)",
        "Facebook": r"facebook\.com",
        "Vimeo": r"vimeo\.com",
        "Dailymotion": r"dailymotion\.com",
        "Reddit": r"reddit\.com",
        "Twitch": r"twitch\.tv",
        "Snapchat": r"snapchat\.com",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, url, re.IGNORECASE):
            return name
    return None


# ──────────────────────────────────────────────
# Global exception handler
# ──────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s", request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred.", "error": str(exc)},
    )


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "service": "CupGet API", "version": "1.0.0"}


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}


@app.api_route("/extract", methods=["GET", "POST"], tags=["Extractor"])
async def extract(request: Request, url: str = None):
    """
    Extract video metadata and direct download links from a URL.

    - GET  /extract?url=https://...
    - POST /extract  {"url": "https://..."}

    Supports: YouTube, TikTok, Instagram, Twitter/X, Facebook, Vimeo,
    Dailymotion, Reddit, Twitch, Snapchat, and 1000+ more sites.
    """
    # ── استخراج الـ URL من GET أو POST ──────────
    target_url = url  # من query string في حالة GET

    if not target_url and request.method == "POST":
        try:
            body = await request.json()
            target_url = body.get("url")
        except Exception:
            pass

    if not target_url:
        raise HTTPException(
            status_code=400,
            detail="يرجى تزويد رابط الفيديو. مثال: ?url=https://youtube.com/... أو JSON body: {\"url\": \"...\"}",
        )

    target_url = target_url.strip()
    if not re.match(r"^https?://", target_url, re.IGNORECASE):
        raise HTTPException(
            status_code=422,
            detail="الرابط غير صالح. يجب أن يبدأ بـ http:// أو https://",
        )

    url = target_url
    platform = _detect_platform(url)
    logger.info("Extracting | platform=%s | url=%s", platform or "unknown", url)

    try:
        with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)

    except yt_dlp.utils.DownloadError as exc:
        err_msg = str(exc).lower()
        logger.warning("DownloadError for %s: %s", url, exc)

        if any(k in err_msg for k in ("private", "login", "sign in", "authentication")):
            raise HTTPException(
                status_code=403,
                detail="This video is private or requires authentication.",
            )
        if any(k in err_msg for k in ("not available", "removed", "deleted", "unavailable")):
            raise HTTPException(
                status_code=404,
                detail="This video is no longer available or has been removed.",
            )
        if "unsupported url" in err_msg:
            raise HTTPException(
                status_code=422,
                detail="This URL is not supported. Please try a direct video link.",
            )
        if "geo" in err_msg or "not available in your country" in err_msg:
            raise HTTPException(
                status_code=451,
                detail="This video is geo-restricted and not available in the server's region.",
            )
        raise HTTPException(status_code=400, detail=f"Could not extract video: {exc}")

    except yt_dlp.utils.ExtractorError as exc:
        logger.warning("ExtractorError for %s: %s", url, exc)
        raise HTTPException(
            status_code=422,
            detail=f"Extractor failed for this URL: {exc}",
        )

    except Exception as exc:
        logger.exception("Unexpected error extracting %s", url)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    if not info:
        raise HTTPException(status_code=404, detail="No information could be extracted from this URL.")

    raw_formats: list[dict] = info.get("formats", [])
    if not raw_formats:
        raise HTTPException(status_code=404, detail="No downloadable formats were found for this video.")

    formats = _parse_formats(raw_formats)

    response = ExtractResponse(
        title=info.get("title", "Untitled"),
        thumbnail=info.get("thumbnail"),
        duration=info.get("duration"),
        uploader=info.get("uploader") or info.get("channel"),
        platform=platform or info.get("extractor_key"),
        formats=formats,
    )

    logger.info(
        "Extracted OK | title='%s' | formats=%d | platform=%s",
        response.title,
        len(formats),
        response.platform,
    )
    return response
