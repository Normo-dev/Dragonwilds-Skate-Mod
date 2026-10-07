using System.Security.Cryptography;
using System.Text;
using CUE4Parse.UE4.Assets.Exports.Component.SplineMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.PhysicsEngine;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// Spline components own their already-deformed cooked BodySetup. Never run the
// visual-mesh spline converter over an undeformed StaticMesh collision proxy.
static class SplineCollision {
    static float[] V(FVector p)=>[p.X,p.Y,p.Z];
    static float[] V2(FVector2D p)=>[p.X,p.Y];
    static string Guid(FGuid value)=>value.ToString().Replace("-","").ToUpperInvariant();
    public static object State(USplineMeshComponent component,string meshPath,UBodySetup body) {
        var p=component.SplineParams;
        return new {
            kind="spline_body_setup_v1",mesh=meshPath,body_guid=Guid(body.BodySetupGuid),
            cached_mesh_body_guid=Guid(component.GetOrDefault<FGuid>("CachedMeshBodySetupGuid")),
            start_pos=V(p.StartPos),start_tangent=V(p.StartTangent),start_scale=V2(p.StartScale),start_roll=p.StartRoll,start_offset=V2(p.StartOffset),
            end_pos=V(p.EndPos),end_tangent=V(p.EndTangent),end_scale=V2(p.EndScale),end_roll=p.EndRoll,end_offset=V2(p.EndOffset),
            forward_axis=(int)component.ForwardAxis,up_dir=V(component.SplineUpDir),
            boundary_min=component.SplineBoundaryMin,boundary_max=component.SplineBoundaryMax,
            smooth=component.bSmoothInterpRollScale
        };
    }
    public static string Export(USplineMeshComponent component,string meshPath,string folder) {
        if(component.SerializationError!=null)throw new InvalidDataException("Partially decoded spline component");
        if(!component.TryGetValue<FPackageIndex>(out var reference,"BodySetup") || reference.IsNull)
            throw new InvalidDataException("Spline has no serialized component BodySetup");
        var body=reference.Load<UBodySetup>()??throw new InvalidDataException("Spline BodySetup unavailable");
        if(body.SerializationError!=null)throw new InvalidDataException("Partially decoded spline BodySetup");
        if(body.Outer?.GetPathName()!=component.GetPathName())throw new InvalidDataException("Spline BodySetup is not owned by this component");
        var trace=body.GetOrDefault<object?>("CollisionTraceFlag",null);
        bool useComplex=JsonConvert.SerializeObject(trace).Contains("UseComplexAsSimple");
        var cooked=CollisionCooked.Inspect(body,useComplex);var cookedInfo=JObject.FromObject(cooked);
        if(cookedInfo.Value<bool?>("success")!=true || cookedInfo.Value<bool?>("shared")!=false || cookedInfo.Value<bool?>("has_payload")!=true)
            throw new InvalidDataException("Spline cooked collision unavailable: "+cookedInfo.ToString(Formatting.None));
        object? complex=useComplex?cookedInfo["triangle_mesh"]:null;
        if(useComplex && complex==null)throw new InvalidDataException("Spline cooked complex collision unavailable");
        var raw=body.GetOrDefault<FStructFallback?>("AggGeom",null);
        var fields=new Dictionary<string,int>();var unhandled=new Dictionary<string,int>();
        var supported=new HashSet<string>{"SphereElems","BoxElems","SphylElems","ConvexElems","TaperedCapsuleElems"};
        foreach(var property in raw?.Properties??[]) {
            if(property.Tag?.GenericValue is not UScriptArray array)continue;
            fields[property.Name.Text]=array.Properties.Count;
            if(array.Properties.Count>0 && !supported.Contains(property.Name.Text))unhandled[property.Name.Text]=array.Properties.Count;
        }
        var state=State(component,meshPath,body);
        var entry=new {schema=5,path=body.GetPathName(),agg=body.AggGeom,trace,complex,cooked,
            raw_aggregate_checked=true,aggregate_fields=fields,unhandled_geometry=unhandled,
            component_collision=state,component_path=component.GetPathName(),collision_source="serialized deformed spline BodySetup"};
        var bytes=Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(entry));
        var id=Convert.ToHexString(SHA256.HashData(bytes))[..24];
        var geometry=Path.Combine(folder,"geometry");Directory.CreateDirectory(geometry);
        var file=Path.Combine(geometry,id+".json");
        if(!File.Exists(file)) {File.WriteAllBytes(file+".tmp",bytes);File.Move(file+".tmp",file);}
        return id;
    }
}
