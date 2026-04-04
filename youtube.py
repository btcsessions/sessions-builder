from urllib.parse import urlparse, parse_qs
from googleapiclient.discovery import build
import re


def parse_playlist_id(url_or_id: str) -> str:
    """Extract playlist ID from a YouTube URL or bare ID."""
    url_or_id = url_or_id.strip()
    if url_or_id.startswith(("PL", "UU", "LL", "FL", "OL")):
        return url_or_id
    parsed = urlparse(url_or_id)
    qs = parse_qs(parsed.query)
    if "list" in qs:
        return qs["list"][0]
    raise ValueError(f"Could not parse playlist ID from: {url_or_id}")


def parse_channel_input(url_or_id: str) -> dict:
    """Parse a channel URL, handle, or ID into a lookup dict.

    Returns {"type": "id"|"handle"|"username", "value": "..."}.
    """
    url_or_id = url_or_id.strip().rstrip("/")

    # Direct channel ID
    if url_or_id.startswith("UC") and len(url_or_id) == 24:
        return {"type": "id", "value": url_or_id}

    # Handle (@username)
    if url_or_id.startswith("@"):
        return {"type": "handle", "value": url_or_id}

    # URL patterns
    parsed = urlparse(url_or_id)
    path = parsed.path

    # /channel/UCxxxxxx
    m = re.match(r"/channel/(UC[\w-]+)", path)
    if m:
        return {"type": "id", "value": m.group(1)}

    # /@handle
    m = re.match(r"/(@[\w.-]+)", path)
    if m:
        return {"type": "handle", "value": m.group(1)}

    # /c/customname or /user/username
    m = re.match(r"/(c|user)/([\w.-]+)", path)
    if m:
        return {"type": "username", "value": m.group(2)}

    raise ValueError(f"Could not parse channel from: {url_or_id}")


def resolve_channel_id(channel_input: str, api_key: str) -> tuple[str, str, str]:
    """Resolve a channel URL/handle/ID to (channel_id, channel_title, avatar_url)."""
    youtube = build("youtube", "v3", developerKey=api_key)
    parsed = parse_channel_input(channel_input)

    if parsed["type"] == "id":
        resp = youtube.channels().list(id=parsed["value"], part="snippet").execute()
    elif parsed["type"] == "handle":
        resp = youtube.channels().list(forHandle=parsed["value"].lstrip("@"), part="snippet").execute()
    else:
        resp = youtube.channels().list(forUsername=parsed["value"], part="snippet").execute()

    items = resp.get("items", [])
    if not items:
        raise ValueError(f"Channel not found: {channel_input}")

    snippet = items[0]["snippet"]
    thumbs = snippet.get("thumbnails", {})
    avatar_url = (thumbs.get("default") or thumbs.get("medium") or {}).get("url", "")

    return items[0]["id"], snippet["title"], avatar_url


def fetch_channel_videos(channel_input: str, api_key: str, max_videos: int = 30) -> dict:
    """Fetch recent public videos from a channel with stats.

    Returns {"channel_id": ..., "channel_title": ..., "videos": [...]}.
    """
    channel_id, channel_title, avatar_url = resolve_channel_id(channel_input, api_key)

    # The uploads playlist is the channel ID with "UC" replaced by "UU"
    uploads_playlist = "UU" + channel_id[2:]

    youtube = build("youtube", "v3", developerKey=api_key)

    video_ids = []
    video_meta = {}
    next_page = None
    fetched = 0

    while fetched < max_videos:
        batch_size = min(50, max_videos - fetched)
        resp = youtube.playlistItems().list(
            playlistId=uploads_playlist,
            part="snippet",
            maxResults=batch_size,
            pageToken=next_page,
        ).execute()

        for item in resp.get("items", []):
            snippet = item["snippet"]
            vid = snippet["resourceId"]["videoId"]
            video_ids.append(vid)
            video_meta[vid] = {
                "video_id": vid,
                "title": snippet["title"],
                "thumbnail_url": snippet["thumbnails"].get("medium", snippet["thumbnails"].get("default", {})).get("url", ""),
            }
            fetched += 1

        next_page = resp.get("nextPageToken")
        if not next_page:
            break

    # Fetch stats and filter out Shorts (< 60 seconds)
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = youtube.videos().list(
            id=",".join(batch),
            part="statistics,snippet,contentDetails",
        ).execute()

        for item in resp.get("items", []):
            vid = item["id"]

            # Skip Shorts (videos under 60 seconds)
            duration_str = item.get("contentDetails", {}).get("duration", "PT0S")
            m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
            duration_seconds = 0
            if m:
                duration_seconds = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
            if duration_seconds < 420:
                continue

            stats = item["statistics"]
            views = int(stats.get("viewCount", 0))
            likes = int(stats.get("likeCount", 0))
            comments = int(stats.get("commentCount", 0))
            engagement = (likes + comments) / views if views > 0 else 0

            meta = video_meta.get(vid, {})
            videos.append({
                "video_id": vid,
                "title": meta.get("title", item["snippet"]["title"]),
                "published_at": item["snippet"]["publishedAt"],
                "thumbnail_url": meta.get("thumbnail_url", item["snippet"]["thumbnails"].get("medium", item["snippet"]["thumbnails"].get("default", {})).get("url", "")),
                "view_count": views,
                "like_count": likes,
                "comment_count": comments,
                "engagement_rate": round(engagement * 100, 2),
            })

    videos.sort(key=lambda v: v["view_count"], reverse=True)
    return {
        "channel_id": channel_id,
        "channel_title": channel_title,
        "avatar_url": avatar_url,
        "videos": videos,
    }


def fetch_playlist_videos(playlist_id: str, api_key: str) -> list[dict]:
    """Fetch all videos from a playlist with their statistics."""
    youtube = build("youtube", "v3", developerKey=api_key)

    # Step 1: Get all video IDs from the playlist
    video_ids = []
    video_meta = {}
    next_page = None

    while True:
        resp = youtube.playlistItems().list(
            playlistId=playlist_id,
            part="snippet",
            maxResults=50,
            pageToken=next_page,
        ).execute()

        for item in resp.get("items", []):
            snippet = item["snippet"]
            vid = snippet["resourceId"]["videoId"]
            video_ids.append(vid)
            video_meta[vid] = {
                "video_id": vid,
                "title": snippet["title"],
                "thumbnail_url": snippet["thumbnails"].get("medium", snippet["thumbnails"].get("default", {})).get("url", ""),
                "position": snippet["position"],
            }

        next_page = resp.get("nextPageToken")
        if not next_page:
            break

    # Step 2: Fetch statistics in batches of 50, filter out Shorts
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = youtube.videos().list(
            id=",".join(batch),
            part="statistics,snippet,contentDetails",
        ).execute()

        for item in resp.get("items", []):
            vid = item["id"]

            # Skip Shorts (videos under 60 seconds)
            duration_str = item.get("contentDetails", {}).get("duration", "PT0S")
            dm = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
            duration_seconds = 0
            if dm:
                duration_seconds = int(dm.group(1) or 0) * 3600 + int(dm.group(2) or 0) * 60 + int(dm.group(3) or 0)
            if duration_seconds < 420:
                continue

            stats = item["statistics"]
            meta = video_meta.get(vid, {})
            views = int(stats.get("viewCount", 0))
            likes = int(stats.get("likeCount", 0))
            comments = int(stats.get("commentCount", 0))

            engagement = (likes + comments) / views if views > 0 else 0

            videos.append({
                "video_id": vid,
                "title": meta.get("title", item["snippet"]["title"]),
                "published_at": item["snippet"]["publishedAt"],
                "thumbnail_url": meta.get("thumbnail_url", ""),
                "view_count": views,
                "like_count": likes,
                "comment_count": comments,
                "engagement_rate": round(engagement * 100, 2),
            })

    videos.sort(key=lambda v: v["view_count"], reverse=True)
    return videos
