from __future__ import annotations
import sys
import m3u8

DEFAULT_URL = ("https://pop3-ec3-ateme.tv360.vn/tok_eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJleHAiOiIxNzg2NTQ1MDIxIiwic2lwIjoiIiwicGF0aCI6Ii9saXZlL2Vkcy8xNDEvSExTX0NsZWFuX01ITF8ycy8iLCJzZXNzaW9uX2Nkbl9pZCI6IjdlZDc5MDMyZjRjZDRmM2QiLCJzZXNzaW9uX2lkIjoiIiwiY2xpZW50X2lkIjoiIiwiZGV2aWNlX2lkIjoiIiwibWF4X3Nlc3Npb25zIjowLCJzZXNzaW9uX2R1cmF0aW9uIjowLCJ1cmwiOiJodHRwczovLzE3Mi4yNC4xNjguMTY0Iiwic2Vzc2lvbl90aW1lb3V0IjowLCJhdWQiOiIxNDYiLCJzb3VyY2VzIjpbMTk4LDQ2Miw0NjUsNDY5XX0=.RISYlddevsks8TwcPgoiXWiinti339RU1o1aUo-jX-dR4jQSZbrXWPIDacWfBAgyc4mxQdUadsCV15kN8jgUDA==/live/eds/141/HLS_Clean_MHL_2s/141-avc1_3299968=10007-mp4a_206000_eng=20001.m3u8")
HEADERS = {
    "User-Agent": "Mozilla/5.0",
}
def print_master_playlist(playlist: m3u8.M3U8) -> None:
    print("\nTYPE: MASTER PLAYLIST")
    print("Number of variants:",len(playlist.playlists))
    for index, variant in enumerate(playlist.playlists):
        info = variant.stream_info
        print(f"\nVariant {index}")
        print("raw uri:", variant.uri)
        print("absolute uri:", variant.absolute_uri)
        print("bandwidth:", info.bandwidth)
        print("average_bandwidth:", info.average_bandwidth)
        print("resolution:", info.resolution)
        print("codecs:", info.codecs)

def print_media_playlist(playlist: m3u8.M3U8) -> None:
    print("\nTYPE: MEDIA PLAYLIST")
    print("Version:",playlist.version)
    print("Target_duration:",playlist.target_duration)
    print("Media_sequence:",playlist.media_sequence)
    print("Endlist:",playlist.is_endlist)
    print("Playlist type:",playlist.playlist_type)
    print("Number of segments:", len(playlist.segments))
    print("\nFirst segments:")
    for segment in playlist.segments[:5]:
        print(
            "sequence =", segment.media_sequence,
            "| duration =",segment.duration,
            "| uri =",segment.uri,
        )
        print(" absolute uri =", segment.absolute_uri)
def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print("Loading:", url)
    try:
        playlist = m3u8.load(
            url,
            timeout=10,
            headers= HEADERS,
        )
    except Exception as exc:
        print(f"Cannot load playlist: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
    print("Base uri:", playlist.base_uri)
    print("Is variant:", playlist.is_variant)
    if playlist.is_variant:
        print_master_playlist(playlist)
    else:
        print_media_playlist(playlist)
        return
    variant = playlist.playlists[0]
    print("Selected variant:", variant.absolute_uri)
    media_playlist = m3u8.load(
        variant.absolute_uri,
        timeout=10,
        headers = HEADERS,
    )
    print_media_playlist(media_playlist)
if __name__ == "__main__":
    main()
