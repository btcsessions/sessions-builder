from __future__ import annotations

from storage import read_json, write_json
import os
import json
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

DATA_DIR = os.environ.get("PLANNER_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TOKEN_FILE = os.path.join(DATA_DIR, "oauth_token.json")
SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]


def get_oauth_flow(client_id: str, client_secret: str, redirect_uri: str, state=None) -> Flow:
    """Create an OAuth 2.0 flow for YouTube Analytics."""
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri, state=state)
    return flow


def save_credentials(creds: Credentials):
    """Save OAuth credentials to disk."""
    os.makedirs(DATA_DIR, exist_ok=True)
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
    write_json(TOKEN_FILE, token_data)


def load_credentials() -> Credentials | None:
    """Load saved OAuth credentials."""
    if not os.path.exists(TOKEN_FILE):
        return None
    token_data = read_json(TOKEN_FILE)
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", SCOPES),
    )
    return creds


def is_authenticated() -> bool:
    """Check if we have valid OAuth credentials."""
    creds = load_credentials()
    return creds is not None and (creds.valid or creds.refresh_token is not None)


def fetch_channel_analytics(video_ids: list[str] = None) -> dict:
    """Fetch channel-level and per-video analytics data."""
    creds = load_credentials()
    if not creds:
        raise ValueError("Not authenticated. Connect YouTube Analytics in Settings.")

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        save_credentials(creds)

    yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    # Monthly queries need both dates on the 1st of a month
    month_start = (datetime.now() - timedelta(days=365)).replace(day=1).strftime("%Y-%m-%d")
    month_end = datetime.now().replace(day=1).strftime("%Y-%m-%d")

    result = {}

    # Channel-level overview (last 365 days)
    channel_resp = yt_analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost,likes,comments,shares",
    ).execute()

    if channel_resp.get("rows"):
        row = channel_resp["rows"][0]
        result["channel"] = {
            "period": f"{start_date} to {end_date}",
            "views": row[0],
            "watch_time_minutes": row[1],
            "avg_view_duration_seconds": row[2],
            "subscribers_gained": row[3],
            "subscribers_lost": row[4],
            "net_subscribers": row[3] - row[4],
            "likes": row[5],
            "comments": row[6],
            "shares": row[7],
        }

    # Channel-level impressions + CTR
    try:
        ctr_resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views,impressions,impressionsCtr",
        ).execute()
        if ctr_resp.get("rows"):
            ctr_row = ctr_resp["rows"][0]
            if "channel" not in result:
                result["channel"] = {}
            result["channel"]["impressions"] = ctr_row[1]
            result["channel"]["impressions_ctr"] = round(ctr_row[2], 2)
    except Exception as e:
        print(f"[Analytics] CTR fetch failed (non-fatal): {e}")

    # Traffic sources
    traffic_resp = yt_analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="views,estimatedMinutesWatched",
        dimensions="insightTrafficSourceType",
        sort="-views",
        maxResults=10,
    ).execute()

    result["traffic_sources"] = []
    for row in traffic_resp.get("rows", []):
        result["traffic_sources"].append({
            "source": row[0],
            "views": row[1],
            "watch_time_minutes": row[2],
        })

    # Top videos by watch time (last 365 days)
    top_resp = yt_analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",
        dimensions="video",
        sort="-estimatedMinutesWatched",
        maxResults=50,
    ).execute()

    result["top_videos"] = []
    for row in top_resp.get("rows", []):
        result["top_videos"].append({
            "video_id": row[0],
            "views": row[1],
            "watch_time_minutes": row[2],
            "avg_view_duration_seconds": row[3],
            "avg_view_percentage": round(row[4], 1),
            "subscribers_gained": row[5],
        })

    # Per-video impressions + CTR (separate query since these metrics
    # can't always be combined with watch time metrics in the same call)
    try:
        vid_ctr_resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views,impressions,impressionsCtr",
            dimensions="video",
            sort="-impressions",
            maxResults=50,
        ).execute()

        # Build lookup by video_id
        ctr_by_vid = {}
        for row in vid_ctr_resp.get("rows", []):
            ctr_by_vid[row[0]] = {
                "impressions": row[1],
                "impressions_ctr": round(row[2], 2),
            }

        # Merge into top_videos
        for tv in result["top_videos"]:
            ctr_data = ctr_by_vid.get(tv["video_id"], {})
            tv["impressions"] = ctr_data.get("impressions", 0)
            tv["impressions_ctr"] = ctr_data.get("impressions_ctr", 0)
    except Exception as e:
        print(f"[Analytics] Per-video CTR fetch failed (non-fatal): {e}")

    # Monthly trend (views + watch time by month)
    monthly_resp = yt_analytics.reports().query(
        ids="channel==MINE",
        startDate=month_start,
        endDate=month_end,
        metrics="views,estimatedMinutesWatched,subscribersGained",
        dimensions="month",
        sort="month",
    ).execute()

    result["monthly_trend"] = []
    for row in monthly_resp.get("rows", []):
        result["monthly_trend"].append({
            "month": row[0],
            "views": row[1],
            "watch_time_minutes": row[2],
            "subscribers_gained": row[3],
        })

    return result


def fetch_retention_curves(video_ids: list[str], days: int = 365) -> dict:
    """Fetch audience retention curves for specific videos.

    Returns dict keyed by video_id, each containing a list of
    {elapsed_pct, audience_pct, relative_retention} data points.
    The curve shows what fraction of viewers are still watching at
    each point in the video (sampled at ~100 points from 0% to 100%).
    """
    creds = load_credentials()
    if not creds:
        raise ValueError("Not authenticated. Connect YouTube Analytics in Settings.")

    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        save_credentials(creds)

    yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    curves = {}
    for vid in video_ids:
        try:
            resp = yt_analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="audienceWatchRatio,relativeRetentionPerformance",
                dimensions="elapsedVideoTimeRatio",
                filters=f"video=={vid}",
            ).execute()

            points = []
            for row in resp.get("rows", []):
                points.append({
                    "elapsed_pct": round(row[0] * 100, 1),
                    "audience_pct": round(row[1] * 100, 1),
                    "relative_retention": round(row[2], 2),
                })
            curves[vid] = points
        except Exception as e:
            print(f"[Analytics] Retention curve for {vid} failed: {e}")
            curves[vid] = []

    return curves
