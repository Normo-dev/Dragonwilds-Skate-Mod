using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// Cooked level exports store overrides, not complete component state. In
// particular, one BodyInstance member must not hide all its archetype members.
sealed class AuthoredCollision
{
    readonly Dictionary<UObject,UObject[]> chains = new();
    readonly Dictionary<UObject,JObject> bodies = new();
    readonly Dictionary<USceneComponent,FTransform> transforms = new();
    readonly HashSet<USceneComponent> transforming = new();
    USceneComponent[] siblings = [];

    public AuthoredCollision(IEnumerable<UObject>? exports=null) {
        if (exports != null) siblings=exports.OfType<USceneComponent>().ToArray();
    }

    public UObject[] Chain(UObject value)
    {
        if (chains.TryGetValue(value,out var cached)) return cached;
        var result = new List<UObject>(); var seen = new HashSet<UObject>();
        UObject? current = value;
        while (current != null)
        {
            if(current.SerializationError != null)
                throw new InvalidDataException("Partially decoded collision object: "+current.GetFullName()+": "+current.SerializationError);
            if (!seen.Add(current) || result.Count >= 64)
                throw new InvalidDataException("Cyclic/deep component archetype chain");
            result.Add(current);
            if (current.Template == null) {
                // Blueprint class defaults may omit an explicit template index.
                // Their base class default still supplies inherited properties.
                var klass=current.Class?.Load<UClass>();
                if (klass is null || klass is UScriptClass) break;
                if (current.Flags.HasFlag(EObjectFlags.RF_ClassDefaultObject))
                    klass=klass.SuperStruct?.Load<UClass>();
                if (klass is null || klass is UScriptClass || klass.ClassDefaultObject is null
                    || klass.ClassDefaultObject.IsNull) break;
                var cdo=klass.ClassDefaultObject.Load<UObject>();
                if (cdo == current) break;
                current=cdo??throw new InvalidDataException("Missing blueprint class default");
                continue;
            }
            var reference = current.Template;
            current = reference.Load<UObject>();
            if (current == null && !reference.GetPathName().StartsWith("/Script/",StringComparison.Ordinal))
                throw new InvalidDataException("Missing cooked archetype: " + reference.GetPathName());
        }
        return chains[value] = result.ToArray();
    }

    public string NativeClass(UObject value)
    {
        var reference=Chain(value).Last().Class;
        var seen=new HashSet<string>();
        while(reference != null) {
            var path=reference.GetPathName();
            if (!seen.Add(path)) throw new InvalidDataException("Cyclic native class lookup");
            if (path.StartsWith("/Script/",StringComparison.Ordinal)) return path;
            reference=reference.Load<UClass>()?.SuperStruct?.ResolvedObject;
        }
        throw new InvalidDataException("Native component class unavailable");
    }

    public T Value<T>(UObject value,string name,T fallback)
    {
        foreach (var item in Chain(value))
            if (item.TryGetValue<T>(out var result,name)) return result;
        return fallback;
    }

    public JObject Body(UObject value)
    {
        if (bodies.TryGetValue(value,out var cached)) return cached;
        var result = new JObject();
        foreach (var item in Chain(value).Reverse())
            if (item.GetOrDefault<FStructFallback?>("BodyInstance",null) is { } body)
                result.Merge(JObject.FromObject(body),new JsonMergeSettings {
                    MergeArrayHandling=MergeArrayHandling.Replace,
                    MergeNullValueHandling=MergeNullValueHandling.Merge
                });
        return bodies[value] = result;
    }

    public FTransform Relative(USceneComponent value) => new(
        Value(value,"RelativeRotation",FRotator.ZeroRotator),
        Value(value,"RelativeLocation",FVector.ZeroVector),
        Value(value,"RelativeScale3D",FVector.OneVector));

    public FTransform Absolute(USceneComponent component)
    {
        if(transforms.TryGetValue(component,out var cached)) return cached;
        if(!transforming.Add(component)) throw new InvalidDataException("Cyclic attachment hierarchy");
        try {
            var relative=Relative(component);var result=relative;
            var parentRef=Value<FPackageIndex?>(component,"AttachParent",null);
            if(parentRef is {IsNull:false}) {
                var parent=parentRef.Load<USceneComponent>()??throw new InvalidDataException("Missing attach parent");
                // An inherited attachment names a blueprint template. Resolve
                // its corresponding component on this actual placed actor.
                var owner=component.Outer?.GetPathName();
                if(parent.Flags.HasFlag(EObjectFlags.RF_ArchetypeObject) && parent.Outer?.GetPathName()!=owner) {
                    var path=parent.GetPathName();
                    var candidates=siblings.Where(s=>s.Outer?.GetPathName()==owner &&
                        Chain(s).Any(t=>t.GetPathName()==path)).ToArray();
                    if(candidates.Length!=1) throw new InvalidDataException("Cannot resolve inherited attach parent: "+path);
                    parent=candidates[0];
                }
                var socket=Value(component,"AttachSocketName",new FName("None")).Text;
                // A socketless attachment already has its component-local pose.
                // Avoid an identity multiply: mirrored FTransform composition
                // decomposes a matrix with approximate quaternion normalization,
                // so a redundant step changes the authored orientation.
                var local=string.IsNullOrEmpty(socket) || StringComparer.OrdinalIgnoreCase.Equals(socket,"None")
                    ? relative : relative*AuthoredSocket.GetSocketTransform(parent,socket,this);
                result=local*Absolute(parent);
                if(Value(component,"bAbsoluteLocation",false)) result.Translation=relative.Translation;
                if(Value(component,"bAbsoluteRotation",false)) result.Rotation=relative.Rotation;
                if(Value(component,"bAbsoluteScale",false)) result.Scale3D=relative.Scale3D;
            }
            return transforms[component]=result;
        } finally {transforming.Remove(component);}
    }

    public object Evidence(UStaticMeshComponent component)
    {
        var owner = component.Outer?.Load<UObject>();
        return new {
            body = Body(component),
            owner_collision = owner == null ? (bool?)null : Value<bool?>(owner,"bActorEnableCollision",null),
            native_class = NativeClass(component),
            templates = Chain(component).Skip(1).Select(t=>t.GetPathName()).ToArray(),
            owner_templates = owner == null ? [] : Chain(owner).Skip(1).Select(t=>t.GetPathName()).ToArray(),
            relative = Relative(component),
            old_relative = component.GetRelativeTransform(),
            attach_parent = Value<FPackageIndex?>(component,"AttachParent",null)?.ResolvedObject?.GetPathName(),
            attach_socket = Value(component,"AttachSocketName",new FName("None")).Text,
            absolute_location = Value(component,"bAbsoluteLocation",false),
            absolute_rotation = Value(component,"bAbsoluteRotation",false),
            absolute_scale = Value(component,"bAbsoluteScale",false)
        };
    }

    public static void Audit(IPackage package,string folder)
    {
        Directory.CreateDirectory(folder);
        var exports=package.GetExports().ToArray();
        var resolver = new AuthoredCollision(exports);
        var id = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
            System.Text.Encoding.UTF8.GetBytes(package.Name)))[..16];
        using var output = new StreamWriter(Path.Combine(folder,id+".authored.jsonl"));
        var count=0; var errors=0;
        foreach (var component in exports.OfType<UStaticMeshComponent>())
        {
            try {
                var mesh = component.GetStaticMesh();
                output.WriteLine(JsonConvert.SerializeObject(new {
                    name=component.GetFullName(), mesh=mesh.ResolvedObject?.GetPathName(),
                    evidence=resolver.Evidence(component)
                }));count++;
            } catch (Exception error) {
                output.WriteLine(JsonConvert.SerializeObject(new {name=component.GetFullName(),error=error.Message}));errors++;
            }
        }
        Console.WriteLine($"AUTHORED AUDIT {package.Name}: {count} components, {errors} errors");
    }
}
