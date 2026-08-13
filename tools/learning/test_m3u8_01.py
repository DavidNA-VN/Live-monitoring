import m3u8
RAW_PLAYLIST = "https://pop3-ec3-ateme.tv360.vn/tok_eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJleHAiOiIxNzg2NTQ1MDIxIiwic2lwIjoiIiwicGF0aCI6Ii9saXZlL2Vkcy8xNDEvSExTX0NsZWFuX01ITF8ycy8iLCJzZXNzaW9uX2Nkbl9pZCI6IjdlZDc5MDMyZjRjZDRmM2QiLCJzZXNzaW9uX2lkIjoiIiwiY2xpZW50X2lkIjoiIiwiZGV2aWNlX2lkIjoiIiwibWF4X3Nlc3Npb25zIjowLCJzZXNzaW9uX2R1cmF0aW9uIjowLCJ1cmwiOiJodHRwczovLzE3Mi4yNC4xNjguMTY0Iiwic2Vzc2lvbl90aW1lb3V0IjowLCJhdWQiOiIxNDYiLCJzb3VyY2VzIjpbMTk4LDQ2Miw0NjUsNDY5XX0=.RISYlddevsks8TwcPgoiXWiinti339RU1o1aUo-jX-dR4jQSZbrXWPIDacWfBAgyc4mxQdUadsCV15kN8jgUDA==/live/eds/141/HLS_Clean_MHL_2s/141-avc1_3299968=10007-mp4a_206000_eng=20001.m3u8"
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:100
#EXTINF:5.8,
segments/video_100.ts
#EXTINF:6.0 ,
segments/video_101.ts
#EXTINF:5.9,
segments/video_102.ts
#EXT-X-ENDLIST"""

playlist = m3u8.loads(RAW_PLAYLIST)
print("is_variant:", playlist.is_variant)
print("is_endlist:", playlist.is_endlist)
print("version:",playlist.version)
print("target duration:", playlist.target_duration)
print("media_sequence:",playlist.media_sequence)
print("number_of_segments:", len(playlist.segments))
print("\nSegments:")
for segment in playlist.segments:
    print(
        "sequence =", segment.media_sequence,
        "| duration =",segment.duration,
        "| uri =",segment.uri,

    )