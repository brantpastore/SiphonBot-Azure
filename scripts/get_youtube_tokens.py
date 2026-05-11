#!/usr/bin/env python3
"""
Generate fresh YouTube PO token and visitor data for yt-dlp.

Usage:
    python3 get_youtube_tokens.py [--video-id VIDEO_ID]

Output (stdout):
    YTDLP_YOUTUBE_VISITOR_DATA=...
    YTDLP_YOUTUBE_PO_TOKEN=...

These can be set as Container App environment variables:
    az containerapp update -n siphonbot-app -g siphon_bot \\
        --set-env-vars \\
        YTDLP_YOUTUBE_VISITOR_DATA='<value>' \\
        YTDLP_YOUTUBE_PO_TOKEN='<value>'
"""

import argparse
import json
import re
import subprocess
import sys


def get_youtube_tokens(video_id: str = "jNQXAC9IVRw") -> dict | None:
    """Extract visitor_data and PO token via yt-dlp + bgutil.

    Args:
        video_id: YouTube video ID to use for token generation.
                  Defaults to 'Me at the zoo', the first public YouTube video.

    Returns:
        dict with 'visitor_data' and 'po_token' keys, or None on failure.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        result = subprocess.run(
            [
                "python3", "-m", "yt_dlp",
                "--dump-json",
                "--skip-download",
                "-f", "best",
                "--extractor-args", "youtube=pot_trace:true",
                "-v",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("[ERROR] yt-dlp timed out during token generation", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"[ERROR] Failed to run yt-dlp: {exc}", file=sys.stderr)
        return None

    stdout = result.stdout
    stderr = result.stderr

    # Attempt to parse JSON info dict from stdout (debug lines may precede it)
    info: dict | None = None
    for line in stdout.splitlines():
        if line.startswith("{"):
            try:
                info = json.loads(line)
                break
            except json.JSONDecodeError:
                pass

    visitor_data: str | None = None
    po_token: str | None = None

    # 1. Try to find visitor_data in the parsed info dict
    if info:
        visitor_data = info.get("visitor_data") or info.get("visitorData")

    # 2. Scan debug output for visitor data patterns
    if not visitor_data:
        match = re.search(
            r'visitor[_-]?data["\']?\s*[:=]\s*["\']?([A-Za-z0-9_%\-=]+)',
            stderr,
            re.IGNORECASE,
        )
        if match:
            visitor_data = match.group(1)

    # 3. Scan debug output for PO token patterns
    po_match = re.search(
        r'(?:po[_-]?token|SAPISIDHASH)["\']?\s*[:=]\s*["\']?([A-Za-z0-9_\-=]+)',
        stderr,
        re.IGNORECASE,
    )
    if po_match:
        po_token = po_match.group(1)

    # Determine success: a parsed info dict with a format_id field signals real extraction
    extraction_ok = bool(info and info.get("format_id"))
    bot_blocked = "LOGIN_REQUIRED" in stderr or "Sign in to confirm" in stderr

    if extraction_ok and not bot_blocked:
        print(f"[INFO] yt-dlp extraction succeeded for video {video_id}", file=sys.stderr)
    else:
        print(
            f"[WARN] yt-dlp extraction {'blocked by bot-check' if bot_blocked else 'did not return usable info'}; "
            "using fallback visitor_data. bgutil will generate a PO token at runtime.",
            file=sys.stderr,
        )
        if stderr:
            print(f"[DEBUG] stderr (first 500 chars):\n{stderr[:500]}", file=sys.stderr)

    # Fall back to a known-good visitor_data if nothing was extracted.
    # bgutil generates PO tokens dynamically, so leaving po_token empty is correct.
    if not visitor_data:
        visitor_data = "CgtXd0NFVEhoQjFRYw%3D%3D"

    return {
        "visitor_data": visitor_data,
        "po_token": po_token or "",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate YouTube tokens for yt-dlp")
    parser.add_argument(
        "--video-id",
        default="jNQXAC9IVRw",
        help="YouTube video ID to use for extraction (default: jNQXAC9IVRw)",
    )
    args = parser.parse_args()

    print(f"[INFO] Generating YouTube tokens using video: {args.video_id}", file=sys.stderr)
    tokens = get_youtube_tokens(args.video_id)

    if tokens:
        print(f"YTDLP_YOUTUBE_VISITOR_DATA={tokens['visitor_data']}")
        print(f"YTDLP_YOUTUBE_PO_TOKEN={tokens['po_token']}")
        print(file=sys.stderr)
        print("[INFO] Set these as Container App environment variables:", file=sys.stderr)
        print("  az containerapp update -n siphonbot-app -g siphon_bot \\", file=sys.stderr)
        print(f"    --set-env-vars YTDLP_YOUTUBE_VISITOR_DATA='{tokens['visitor_data']}' \\", file=sys.stderr)
        print(f"    YTDLP_YOUTUBE_PO_TOKEN='{tokens['po_token']}'", file=sys.stderr)
    else:
        print("[ERROR] Failed to generate tokens", file=sys.stderr)
        sys.exit(1)
