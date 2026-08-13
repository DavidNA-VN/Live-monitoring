import m3u8
RAW_MASTER_PLAYLIST = """
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-INDEPENDENT-SEGMENTS

#EXT-X-STREAM-INF:BANDWIDTH=800000,CODECS="avc1.42e01e,mp4a.40.2",RESOLUTION=640x360
360p/playlist.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=2500000,CODECS="avc1.4d401f,mp4a.40.2",RESOLUTION=1280x720
720p/playlist.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=5000000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1920x1080
1080p/playlist.m3u8
"""
playlist = m3u8.loads(
    RAW_MASTER_PLAYLIST,
    uri="https://media.example.com/live/master.m3u8",)
print("is_variant:",playlist.is_variant)
print("is_endlist:",playlist.is_endlist)
print("number_of_playlists:",len(playlist.playlists))
for index, variant in enumerate(playlist.playlists):
    info = variant.stream_info
    print(f"\nVariant{index}")
    print("raw uri:", variant.uri)
    print("absolute uri:", variant.absolute_uri)
    print("bandwidth:", info.bandwidth)
    print("resolution:", info.resolution)
    print("codecs:", info.codecs)