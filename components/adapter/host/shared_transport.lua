-- Exact-loader Lua API helper, compiled locally; refuses an unmatched loader.
local config=require('skate_config')
local path=config.transport_library
local function entry(name)
    local fn,why=package.loadlib(path,name)
    assert(fn,'Cannot load Skate shared memory helper: '..tostring(why))
    return fn
end
assert(entry('skate_check')()=='shared-memory-v3','Shared memory helper does not match this UE4SS release')
local rawClock=entry('skate_clock')
return {readFrame=entry('skate_read_frame'),readStatus=entry('skate_read_status'),writeHost=entry('skate_write_host'),writeCollision=entry('skate_write_collision'),
    startRelay=function()
        if not config.relay_launcher and not config.python_runtime then return 'manual' end
        assert(config.relay_launcher and config.python_runtime,'Incomplete Skate launcher configuration')
        return entry('skate_start_relay')(config.python_runtime,config.relay_launcher)
    end,
    clock=function()local value=rawClock();assert(value and #value==8,'Native timer unavailable');return (string.unpack('<d',value)) end}
