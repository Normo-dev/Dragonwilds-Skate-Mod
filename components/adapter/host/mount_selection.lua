-- Per-save mod preference. Transient mount assets never enter a native save.
local M={}
local json=require('json')
function M.options(session,clock,icon_path)
    assert(type(session)=='string'and session:match('^save%-%x+$')and #session==37,'Invalid save identity')
    local path=require('skate_config').mailbox..'mount-selection-'..session..'.json'
    return {clock=clock,icon_path=icon_path,
        load_selection=function()
            local file=io.open(path,'r');if not file then return false end
            local text=file:read('*a');file:close()
            local value=json.decode(text)
            assert(value.schema=='S3MOUNTSELECTION1'and value.session==session and type(value.selected)=='boolean','Invalid mount preference')
            return value.selected
        end,
        save_selection=function(selected)
            assert(type(selected)=='boolean','Invalid mount preference')
            local file=assert(io.open(path..'.tmp','w'))
            assert(file:write(json.encode({schema='S3MOUNTSELECTION1',session=session,selected=selected})))
            assert(file:close())
            os.remove(path);assert(os.rename(path..'.tmp',path))
        end}
end
return M
