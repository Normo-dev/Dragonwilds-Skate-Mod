using System.Security.Cryptography;
using System.Text;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Component.SplineMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.PhysicsEngine;
using Newtonsoft.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Objects;

static class StaticBake {
    public static UStaticMesh LoadMesh(AbstractFileProvider provider,string path) {
        try{return provider.LoadPackageObject<UStaticMesh>(path);}
        catch(KeyNotFoundException) {
            // IoStore plugin content is indexed under its actual Content folder,
            // whereas live Unreal object paths use the plugin's mount name.
            var packagePath=path.Split('.')[0];var split=packagePath.TrimStart('/').Split('/',2);
            if(split.Length!=2)throw;
            var suffix="/"+split[0]+"/Content/"+split[1]+".uasset";
            var file=provider.Files.Values.Single(f=>f.Path.EndsWith(suffix,StringComparison.OrdinalIgnoreCase));
            return provider.LoadPackage(file.Path).GetExports().OfType<UStaticMesh>().Single(m=>m.Name==path.Split('.').Last());
        }
    }
    public static string MeshId(string path)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(path)))[..24];
    public static void Mesh(UStaticMesh mesh,string path,string folder) {
        if(mesh.SerializationError != null)throw new InvalidDataException("Partially decoded mesh: "+mesh.SerializationError);
        var body=mesh.BodySetup.Load<UBodySetup>()??throw new Exception("Body setup unavailable");
        if(body.SerializationError != null)throw new InvalidDataException("Partially decoded body setup: "+body.SerializationError);
        var geometryFolder=Path.Combine(folder,"geometry");Directory.CreateDirectory(geometryFolder);
        var file=Path.Combine(geometryFolder,MeshId(path)+".json");
        if(File.Exists(file) && File.ReadAllText(file).Contains("\"schema\":5"))return;
        var trace=body.GetOrDefault<object?>("CollisionTraceFlag",null);
        object? complex=null;
        var agg=body.AggGeom;
        bool empty=agg is null || (agg.SphereElems.Length+agg.BoxElems.Length+agg.SphylElems.Length+agg.ConvexElems.Length+agg.TaperedCapsuleElems.Length)==0;
        bool useComplex=JsonConvert.SerializeObject(trace).Contains("UseComplexAsSimple");
        object? cooked=(empty || useComplex || (agg?.ConvexElems.Any(c=>c.IndexData.Length==0)??false))?CollisionCooked.Inspect(body,useComplex):null;
        if(useComplex && cooked is not null)complex=Newtonsoft.Json.Linq.JObject.FromObject(cooked)["triangle_mesh"];
        if(useComplex && complex is null)Console.WriteLine($"STATIC COOKED COMPLEX UNAVAILABLE {path}");
        var raw=body.GetOrDefault<FStructFallback?>("AggGeom",null);
        var aggregateFields=new Dictionary<string,int>();var unhandled=new Dictionary<string,int>();
        var supported=new HashSet<string>{"SphereElems","BoxElems","SphylElems","ConvexElems","TaperedCapsuleElems"};
        foreach(var property in raw?.Properties??[]) {
            if(property.Tag?.GenericValue is not UScriptArray array)continue;
            aggregateFields[property.Name.Text]=array.Properties.Count;
            if(array.Properties.Count>0 && !supported.Contains(property.Name.Text))unhandled[property.Name.Text]=array.Properties.Count;
        }
        var temp=file+".tmp";
        File.WriteAllText(temp,JsonConvert.SerializeObject(new {schema=5,path,agg,trace,complex,cooked,
            raw_aggregate_checked=true,aggregate_fields=aggregateFields,unhandled_geometry=unhandled}));
        File.Move(temp,file,true);
    }
    public static void ExportGeometry(IPackage package,string folder) {
        var count=0;
        foreach(var c in package.GetExports().OfType<UStaticMeshComponent>()) {
            try {
                var reference=c.GetStaticMesh();if(reference.IsNull)continue;
                var path=reference.ResolvedObject?.GetPathName()??reference.Name;
                Mesh(reference.Load<UStaticMesh>()??throw new Exception("Mesh unavailable"),path,folder);count++;
            }catch(Exception e){Console.WriteLine($"GEOMETRY OMITTED {c.GetFullName()} {e.Message}");}
        }
        Console.WriteLine($"GEOMETRY EXPORTED {package.Name}: {count} components");
    }
    public static void Export(IPackage package,string folder) {
        var geometryFolder=Path.Combine(folder,"geometry");Directory.CreateDirectory(geometryFolder);
        var packageId=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(package.Name)))[..16];
        using var manifest=new StreamWriter(Path.Combine(folder,packageId+".instances.jsonl"));
        int count=0;
        foreach(var c in package.GetExports().OfType<UStaticMeshComponent>()) {
            try {
                var instance=JsonConvert.SerializeObject(c.GetOrDefault<object?>("BodyInstance",null));
                if(instance.Contains("NoCollision"))continue;
                var reference=c.GetStaticMesh();if(reference.IsNull)continue;
                var path=reference.ResolvedObject?.GetPathName()??reference.Name;
                var id=MeshId(path);
                var file=Path.Combine(geometryFolder,id+".json");
                if(!File.Exists(file) || !File.ReadAllText(file).Contains("\"schema\":5")) {
                    Mesh(reference.Load<UStaticMesh>()??throw new Exception("Mesh unavailable"),path,folder);
                }
                var tf=c.GetAbsoluteTransform();
                if(c is UInstancedStaticMeshComponent ism && ism.PerInstanceSMData is not null) {
                    foreach(var item in ism.PerInstanceSMData)manifest.WriteLine(JsonConvert.SerializeObject(new {name=c.GetFullName(),id,transform=item.TransformData*tf}));
                    count+=ism.PerInstanceSMData.Length;
                }else {manifest.WriteLine(JsonConvert.SerializeObject(new {name=c.GetFullName(),id,transform=tf}));count++;}
            }catch(Exception e){Console.WriteLine($"STATIC OMITTED {c.GetFullName()} {e.Message}");}
        }
        Console.WriteLine($"STATIC EXPORTED {package.Name}: {count} instances");
    }

    public static void ExportAuthored(IPackage package,string folder,AuthoredPolicy policy) {
        Directory.CreateDirectory(folder);
        var geometryFolder=Path.Combine(folder,"geometry");Directory.CreateDirectory(geometryFolder);
        var packageId=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(package.Name)))[..16];
        var output=Path.Combine(folder,packageId+".instances.jsonl");
        var exports=package.GetExports().ToArray();
        var resolver=new AuthoredCollision(exports);
        var reasons=new Dictionary<string,int>();var errors=new List<object>();
        var count=0;var includedComponents=0;var considered=0;
        using(var manifest=new StreamWriter(output+".tmp")) {
            foreach(var c in exports.OfType<UStaticMeshComponent>()) {
                considered++;
                try {
                    var reference=resolver.Value(c,"StaticMesh",new CUE4Parse.UE4.Objects.UObject.FPackageIndex());
                    if(reference.IsNull){reasons["no_mesh"]=reasons.GetValueOrDefault("no_mesh")+1;continue;}
                    var decision=policy.Resolve(c,resolver);
                    reasons[decision.reason]=reasons.GetValueOrDefault(decision.reason)+1;
                    if(!decision.included)continue;
                    var path=reference.ResolvedObject?.GetPathName()??reference.Name;
                    string id;
                    if(c is USplineMeshComponent spline)id=SplineCollision.Export(spline,path,folder);
                    else {
                        id=MeshId(path);
                        Mesh(reference.Load<UStaticMesh>()??throw new Exception("Mesh unavailable"),path,folder);
                    }
                    var tf=resolver.Absolute(c);
                    void Write(object transform) {
                        manifest.WriteLine(JsonConvert.SerializeObject(new {
                            schema=2,name=c.GetFullName(),id,transform,
                            collision_policy="query_and_player_block",policy_fingerprint=policy.Fingerprint,
                            collision=decision
                        }));count++;
                    }
                    if(c is UInstancedStaticMeshComponent ism && ism.PerInstanceSMData is not null) {
                        foreach(var item in ism.PerInstanceSMData)Write(item.TransformData*tf);
                    }else Write(tf);
                    includedComponents++;
                }catch(Exception error){errors.Add(new{name=c.GetFullName(),error=error.Message});}
            }
        }
        File.Move(output+".tmp",output,true);
        var report=new {schema="S3AP1",package=package.Name,policy_fingerprint=policy.Fingerprint,
            complete=errors.Count==0,considered,included_components=includedComponents,instances=count,reasons,errors,
            transform_policy="archetype_per_field_v1",attachment_policy=AuthoredSocket.TransformPolicy};
        File.WriteAllText(Path.Combine(folder,packageId+".policy.json"),JsonConvert.SerializeObject(report));
        if(errors.Count>0)Environment.ExitCode=1;
        Console.WriteLine($"AUTHORED EXPORTED {package.Name}: {count} instances, {errors.Count} errors");
    }
}
