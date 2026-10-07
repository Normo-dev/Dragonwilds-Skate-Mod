-- Use a cooked surface shader: widget materials route colour through emissive
-- and stay bright in darkness. This parent has no emissive/WPO/PDO connection.
-- Texture UVs and the imported board's authored normals are retained.
local M={}
local cache={}
local parent
local parentPath='/Game/Marketplace/MSPresets/MS_DefaultMaterial/MS_DefaultMaterial.MS_DefaultMaterial'
local profiles={deck={roughness=.72,specular=.35,metal=false},
    truck={roughness=.42,specular=.5,metal=true},
    wheel={roughness=.85,specular=.35,metal=false}}
local function valid(o) return o and o:IsValid() end
local function log(s) print('[SkateBoardMaterial] '..s..'\n') end
local function scalar(material,name,value)
    material:SetScalarParameterValue(FName(name),value)
    local actual=material:K2_GetScalarParameterValue(FName(name))
    assert(type(actual)=='number' and math.abs(actual-value)<.00001,'Board material scalar not applied: '..name)
end
local function vector(material,name,value)
    material:SetVectorParameterValue(FName(name),{R=value[1],G=value[2],B=value[3],A=value[4]})
    local actual=material:K2_GetVectorParameterValue(FName(name))
    for i,k in ipairs({'R','G','B','A'})do
        assert(type(actual[k])=='number' and math.abs(actual[k]-value[i])<.00001,'Board material vector not applied: '..name)
    end
end
local function texture_parameter(material,name,texture)
    assert(valid(texture),'Board surface texture unavailable: '..name)
    material:SetTextureParameterValue(FName(name),texture)
    local actual=material:K2_GetTextureParameterValue(FName(name))
    assert(valid(actual) and actual:GetFullName()==texture:GetFullName(),'Board material texture not applied: '..name)
end
function M.get(pawn,path,tint,kind)
    tint=tint or {1,1,1,1}
    assert(type(tint)=='table' and #tint==4,'Invalid board material tint')
    for _,value in ipairs(tint)do assert(type(value)=='number' and value>=0 and value<=1,'Invalid board tint channel')end
    kind=kind or 'deck'
    local profile=assert(profiles[kind],'Invalid board surface kind')
    local key=path..':'..table.concat(tint,',')..':'..kind
    if valid(cache[key]) then return cache[key] end
    if not valid(parent) then
        local candidate=LoadAsset(parentPath)
        assert(valid(candidate),'Cooked lit board surface material is unavailable')
        -- Confirm native class defaults rather than relying on omitted values
        -- in the cooked export. Never silently fall back to the glowing shader.
        assert(candidate.MaterialDomain==0 and candidate.BlendMode==0 and candidate.ShadingModel==1,
            'Board material must be Surface / Opaque / Default Lit')
        parent=candidate
        log('WORLD LIT parent='..parentPath..' domain=0 blend=0 shading=1')
    end
    local rendering=StaticFindObject('/Script/Engine.Default__KismetRenderingLibrary')
    local texture=rendering:ImportFileAsTexture2D(pawn,path)
    assert(valid(texture),'Cannot load extracted board texture '..path)
    local materials=StaticFindObject('/Script/Engine.Default__KismetMaterialLibrary')
    local material=materials:CreateDynamicMaterialInstance(pawn,parent,FName('None'),0)
    assert(valid(material),'Cannot create board material')
    texture_parameter(material,'Albedo',texture)
    vector(material,'Albedo Tint',tint)
    vector(material,'Albedo Controls',{1,1,1,0})
    vector(material,'Tiling/Offset',{1,1,0,0})
    vector(material,'Metallic Controls',{1,0,1,1})
    local metalName=profile.metal and 'T_Default_White_D' or 'T_Default_Black_D'
    texture_parameter(material,'Metallic',LoadAsset('/Game/Materials/DefaultTextures/'..metalName..'.'..metalName))
    scalar(material,'Min Roughness',profile.roughness)
    scalar(material,'Max Roughness',profile.roughness)
    scalar(material,'Base Specular',profile.specular)
    scalar(material,'Specular From Albedo Override',0)
    scalar(material,'Normal Strength',0)
    scalar(material,'Detail Normal Strength',0)
    scalar(material,'Detail Normal  Override',0) -- two spaces in the cooked parameter
    scalar(material,'Rotation Angle',0)
    cache[key]=material
    log('WORLD LIT APPLIED kind='..kind..' roughness='..profile.roughness..' texture='..path)
    return material
end
return M
