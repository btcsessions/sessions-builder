from urllib.parse import urlparse, parse_qs
from googleapiclient.discovery import build


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

    # Step 2: Fetch statistics in batches of 50
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = youtube.videos().list(
            id=",".join(batch),
            part="statistics,snippet",
        ).execute()

        for item in resp.get("items", []):
            vid = item["id"]
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
