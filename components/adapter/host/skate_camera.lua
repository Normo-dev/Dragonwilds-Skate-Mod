-- Host-only third view. Capture the walking view before switching ViewTarget;
-- keep its distance, shoulder offset, height, pitch and FOV while the imported
-- Skate camera supplies the movement-following heading. No player input writes.
local M={}
local verified=false
local function finite(v)return type(v)=='number'and v==v and math.abs(v)<math.huge end
local function vector(v)
    assert(v and finite(v.X)and finite(v.Y)and finite(v.Z),'Invalid camera vector')
    return {X=v.X,Y=v.Y,Z=v.Z}
end
local function valid(v)return v and v:IsValid()end
local function signature(path,kind,struct)
    local object=StaticFindObject(path);assert(valid(object),'Camera API unavailable: '..path)
    local fields={};object:ForEachProperty(function(p)fields[#fields+1]=p end)
    assert(#fields==1,'Changed camera signature: '..path)
    local p=fields[1]
    assert(p:GetFName():ToString()=='ReturnValue'and p:GetClass():GetFName():ToString()==kind,'Changed camera return: '..path)
    if struct then assert(p:GetStruct():GetFullName()=='ScriptStruct '..struct,'Changed camera return struct: '..path)end
end
local function verify()
    if verified then return end
    signature('/Script/Engine.PlayerCameraManager:GetCameraLocation','StructProperty','/Script/CoreUObject.Vector')
    signature('/Script/Engine.PlayerCameraManager:GetCameraRotation','StructProperty','/Script/CoreUObject.Rotator')
    signature('/Script/Engine.PlayerCameraManager:GetFOVAngle','FloatProperty')
    verified=true
end

function M.capture(pawn,pc)
    local profile
    local status={schema='S3CAMERACAPTURE1',ready=false}
    local okay,why=pcall(function()
        verify();assert(valid(pawn)and valid(pc),'Player or controller unavailable')
        local manager=pc.PlayerCameraManager;assert(valid(manager),'Player camera manager unavailable')
        local position=vector(manager:GetCameraLocation());local origin=vector(pawn:K2_GetActorLocation())
        local rotation=manager:GetCameraRotation();local fov=manager:GetFOVAngle()
        assert(rotation and finite(rotation.Pitch)and finite(rotation.Yaw)and finite(rotation.Roll),'Invalid walking camera rotation')
        assert(finite(fov)and fov>5 and fov<170,'Invalid walking camera FOV')
        assert(math.abs(rotation.Pitch)<89.5,'Walking view is vertical')
        local x,y,z=position.X-origin.X,position.Y-origin.Y,position.Z-origin.Z
        local length=math.sqrt(x*x+y*y+z*z)
        assert(length>=25 and length<=4000,'Walking camera distance outside supported range')
        local yaw=math.rad(rotation.Yaw);local c,s=math.cos(yaw),math.sin(yaw)
        profile={schema='S3CAMERAPROFILE1',forward=x*c+y*s,right=-x*s+y*c,up=z,
            pitch=rotation.Pitch,roll=rotation.Roll,fov=fov}
        status.ready=true;status.distance_cm=length;status.pitch=profile.pitch;status.fov=fov
        status.offset={forward=profile.forward,right=profile.right,up=profile.up}
    end)
    if not okay then status.reason=tostring(why)end
    return profile,status
end

function M.present(profile,source_position,source_forward,source_up,pawn_position,source_fov)
    local position,forward,up,fov
    local status={mode='dragonwilds',ready=false}
    local okay,why=pcall(function()
        assert(profile and profile.schema=='S3CAMERAPROFILE1','Walking camera profile unavailable')
        for _,name in ipairs({'forward','right','up','pitch','roll','fov'})do
            assert(finite(profile[name]),'Invalid walking camera profile: '..name)
        end
        assert(profile.fov>5 and profile.fov<170 and math.abs(profile.pitch)<89.5,'Invalid walking camera lens')
        assert(profile.forward^2+profile.right^2+profile.up^2<=4000^2,'Invalid walking camera offset')
        local origin=vector(pawn_position);local heading=vector(source_forward)
        local length=math.sqrt(heading.X^2+heading.Y^2)
        assert(length>0.000001,'Source camera heading is vertical')
        local c,s=heading.X/length,heading.Y/length
        position={X=origin.X+profile.forward*c-profile.right*s,
            Y=origin.Y+profile.forward*s+profile.right*c,Z=origin.Z+profile.up}
        local pitch,roll=math.rad(profile.pitch),math.rad(profile.roll)
        local cp,sp,cr,sr=math.cos(pitch),math.sin(pitch),math.cos(roll),math.sin(roll)
        forward={X=cp*c,Y=cp*s,Z=sp}
        up={X=-cr*sp*c-sr*s,Y=-cr*sp*s+sr*c,Z=cr*cp}
        fov=profile.fov;status.ready=true
    end)
    if not okay then
        status.mode='high';status.reason=tostring(why)
        return source_position,source_forward,source_up,source_fov,status
    end
    return position,forward,up,fov,status
end

return M
