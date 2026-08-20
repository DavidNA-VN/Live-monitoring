RELEASE_OWNED_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

RENEW_OWNED_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

COMPLETE_SEGMENT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call(
    'HSET', KEYS[2],
    'status', ARGV[2],
    'updated_at', ARGV[3],
    'last_error', ARGV[4]
)
redis.call('EXPIRE', KEYS[2], ARGV[5])
redis.call('DEL', KEYS[1])
return 1
"""

PUBLISH_RUNTIME_HEALTH = """
local previous = redis.call('GET', KEYS[1])
if previous == ARGV[1] then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
    return 0
end
local had_previous = previous ~= false
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
if not had_previous and ARGV[3] == 'HEALTHY' then
    return 0
end
local output_state = ARGV[3]
local output_reason = ARGV[5]
if ARGV[3] == 'HEALTHY' then
    output_state = 'RECOVERED'
    output_reason = 'runtime_recovered'
end
redis.call(
    'XADD', KEYS[2], 'MAXLEN', '~', ARGV[10], '*',
    'schema_version', ARGV[6],
    'alert_id', ARGV[7],
    'event_id', ARGV[8],
    'category', 'runtime',
    'check', 'runtime',
    'type', 'RUNTIME_HEALTH',
    'state', output_state,
    'stream_id', ARGV[4],
    'occurred_at', ARGV[9],
    'emitted_at', ARGV[9],
    'reason', output_reason,
    'reasons', ARGV[5],
    'payload', '{}'
)
redis.call('HINCRBY', KEYS[3], 'alert_total', 1)
redis.call('HINCRBY', KEYS[3], 'alert_runtime_total', 1)
redis.call('EXPIRE', KEYS[3], ARGV[2])
return 1
"""
