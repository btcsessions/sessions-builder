import os
import json
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TOKEN_FILE = os.path.join(DATA_DIR, "oauth_token.json")
SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]


def get_oauth_flow(client_id: str, client_secret: str, redirect_uri: str) -> Flow:
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
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
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
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)


def load_credentials() -> Credentials | None:
    """Load saved OAuth credentials."""
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        token_data = json.load(f)
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
