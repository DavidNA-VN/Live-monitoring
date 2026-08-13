import m3u8

from playlist.media_parser import Segment

MASTER_URL = "http://127.0.0.1:8000/busquet/master.m3u8"


master = m3u8.load(MASTER_URL)

for index, media in enumerate (master.playlists):
    print(f"Media Playlist {index + 1}: {media.absolute_uri}\n")
