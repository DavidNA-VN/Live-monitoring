RELEASE_OWNED_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


WRITE_EVENT_EVIDENCE = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    redis.call('DEL', KEYS[2])
    return 0
end
local canonical_ttl = redis.call('PTTL', KEYS[1])
local evidence_ttl = tonumber(ARGV[1])
if canonical_ttl > 0 and canonical_ttl < evidence_ttl then
    evidence_ttl = canonical_ttl
end
redis.call(
    'HSET', KEYS[2],
    'checked', ARGV[2],
    'analyzed_variant_count', ARGV[3],
    'reason', ARGV[4]
)
local index = 5
while index <= #ARGV do
    redis.call('HSET', KEYS[2], ARGV[index], ARGV[index + 1])
    index = index + 2
end
redis.call('PEXPIRE', KEYS[2], evidence_ttl)
return 1
"""
