-- Read-only identity of the deformed BodySetup on a live spline component.
local M={}
local function number(value)
    assert(type(value)=='number' and value==value and value>-math.huge and value<math.huge,'Invalid spline parameter')
    return value
end
local function vector(value,count)
    local result={number(value.X),number(value.Y)}
    if count==3 then result[3]=number(value.Z) end
    return result
end
local function guid(value)
    local words={value.A,value.B,value.C,value.D};local parts={}
    for i,word in ipairs(words) do
        assert(type(word)=='number' and word==math.floor(word),'Invalid spline body GUID')
        if word<0 then word=word+4294967296 end
        assert(word>=0 and word<=4294967295,'Invalid spline body GUID')
        parts[i]=string.format('%08X',word)
    end
    return table.concat(parts)
end
function M.parameters(component)
    local p=component.SplineParams
    local axis=number(component.ForwardAxis)
    assert(axis==0 or axis==1 or axis==2,'Invalid spline forward axis')
    local smooth=component.bSmoothInterpRollScale
    assert(type(smooth)=='boolean','Invalid spline interpolation flag')
    return {start_pos=vector(p.StartPos,3),start_tangent=vector(p.StartTangent,3),start_scale=vector(p.StartScale,2),
        start_roll=number(p.StartRoll),start_offset=vector(p.StartOffset,2),
        end_pos=vector(p.EndPos,3),end_tangent=vector(p.EndTangent,3),end_scale=vector(p.EndScale,2),
        end_roll=number(p.EndRoll),end_offset=vector(p.EndOffset,2),forward_axis=axis,
        up_dir=vector(component.SplineUpDir,3),boundary_min=number(component.SplineBoundaryMin),
        boundary_max=number(component.SplineBoundaryMax),smooth=smooth}
end
function M.capture(component,mesh)
    local body=component.BodySetup
    assert(body and body:IsValid(),'Spline has no live BodySetup')
    local outer=body:GetOuter()
    assert(outer and outer:IsValid() and outer:GetAddress()==component:GetAddress(),'Spline BodySetup is not owned by this component')
    assert(component.bMeshDirty==false,'Spline deformed mesh is dirty')
    local function path(object)
        local value=object:GetFullName():match('^[^ ]+ (.+)$')
        assert(value and value:sub(1,1)=='/','Spline object path is unavailable')
        return value
    end
    local result=M.parameters(component)
    -- BodySetupGuid is serialized but not reflected in this installed game.
    -- The exact saved body path and its outer constrain lookup to the authored
    -- component; every reflected deformation input must still match its bytes.
    result.kind='spline_body_setup_v2';result.mesh=mesh
    result.component_path=path(component);result.body_path=path(body);result.mesh_dirty=false
    result.cached_mesh_body_guid=guid(component.CachedMeshBodySetupGuid)
    return result
end
return M
