using System.Security.Cryptography;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.AssetRegistry;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// This is owned-asset metadata, not a claim that preview bodies are live
// colliders. Runtime placement flags/profile overrides must be verified too.
static class BuildingCatalogue
{
    static JToken Token(object? value) => value==null ? JValue.CreateNull() : JToken.FromObject(value);
    static string? Reference(FPackageIndex? value) => value is null || value.IsNull ? null : value.ResolvedObject?.GetPathName();
    static JObject Entity(FStructFallback entity, int index)
    {
        var fields=new Dictionary<string,(object? value,FStructFallback source)>(StringComparer.Ordinal);
        var fragmentFields=new JArray();
        foreach(var instance in entity.GetOrDefault<FInstancedStruct[]>("Fragments",[])) {
            var fragment=instance.NonConstStruct ?? throw new InvalidDataException("Unreadable entity fragment");
            fragmentFields.Add(new JArray(fragment.Properties.Select(p=>p.Name.Text)));
            foreach(var key in new[]{"Transform","StaticMesh","BodyInstance","ComponentTags"}) {
                if(!fragment.Properties.Any(p=>p.Name.Text==key))continue;
                if(fields.ContainsKey(key))throw new InvalidDataException("Duplicate entity fragment field "+key);
                fields[key]=(fragment.GetOrDefault<object?>(key,null),fragment);
            }
        }
        object? Value(string key)=>fields.TryGetValue(key,out var pair)?pair.value:null;
        var mesh=fields.TryGetValue("StaticMesh",out var m)?Reference(m.source.GetOrDefault<FPackageIndex?>("StaticMesh",null)):null;
        var transform=fields.TryGetValue("Transform",out var t)?(FTransform?)t.source.Get<FTransform>("Transform"):null;
        var archetype=entity.GetOrDefault<FName>("ArchetypeName",new FName("None")).Text;
        var problems=new List<string>();
        if(archetype!="StaticMeshEntity")problems.Add("Unknown entity archetype "+archetype);
        if(transform is null)problems.Add("Missing exact entity transform");
        if(mesh is null)problems.Add("Missing resolved static mesh");
        if(Value("BodyInstance") is null)problems.Add("Missing authored body instance");
        return new JObject {
            ["entity_index"]=index,["archetype_name"]=archetype,["mesh"]=mesh,
            ["transform"]=Token(transform),["body"]=Token(Value("BodyInstance")),
            ["component_tags"]=Token(Value("ComponentTags")),["fragment_fields"]=fragmentFields,
            ["supported_static_mesh_metadata"]=problems.Count==0,["errors"]=new JArray(problems)
        };
    }
    public static void Export(DefaultFileProvider provider,string output,string mappings)
    {
        var data=new JObject();var derived=new JObject();var errors=new JArray();var considered=0;
        // Registry classes cover game-feature/plugin roots and asset names that
        // do not follow the base game's DA_/BUILDPIECE_ folder conventions.
        var registries=provider.Files.Values.Where(f=>f.Path.EndsWith("/AssetRegistry.bin",StringComparison.OrdinalIgnoreCase))
            .OrderBy(f=>f.Path,StringComparer.Ordinal).ToArray();
        if(registries.Length==0)throw new InvalidDataException("No mounted asset registry");
        var expected=new Dictionary<string,string>(StringComparer.Ordinal);
        foreach(var registry in registries) {
            using var reader=provider.CreateReader(registry.Path);
            foreach(var asset in new FAssetRegistryState(reader).PreallocatedAssetDataBuffers) {
                if(asset.AssetClass.Text is not ("BuildingPieceData" or "BuildingPieceDerivedData"))continue;
                if(expected.TryGetValue(asset.ObjectPath,out var prior) && prior!=asset.AssetClass.Text)
                    throw new InvalidDataException("Conflicting building registry classes");
                expected[asset.ObjectPath]=asset.AssetClass.Text;
            }
        }
        if(!expected.Values.Contains("BuildingPieceData"))throw new InvalidDataException("Registry contains no building data assets");
        var packages=new SortedSet<string>(expected.Keys.Select(p=>p.Split('.')[0]),StringComparer.Ordinal);
        var indexed=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
        foreach(var file in provider.Files.Values.Where(f=>f.Path.EndsWith(".uasset",StringComparison.OrdinalIgnoreCase))) {
            var marker=file.Path.LastIndexOf("/Content/",StringComparison.OrdinalIgnoreCase);if(marker<0)continue;
            var owner=file.Path[..marker].Split('/').Last();
            var root=file.Path.StartsWith("RSDragonwilds/Content/",StringComparison.OrdinalIgnoreCase)?"Game":owner;
            var path="/"+root+"/"+file.Path[(marker+9)..^7];
            if(indexed.TryGetValue(path,out var prior) && prior!=file.Path)throw new InvalidDataException("Ambiguous mounted asset path: "+path);
            indexed[path]=file.Path;
        }
        var visited=new HashSet<string>(StringComparer.Ordinal);
        while(packages.Count>0) {
            var package=packages.Min!;packages.Remove(package);if(!visited.Add(package))continue;
            try {
                if(!indexed.TryGetValue(package,out var actual))throw new InvalidDataException("Building package missing from mounted files");
                var exports=provider.LoadPackage(actual).GetExports().ToArray();
                foreach(var obj in exports.Where(o=>o.ExportType is "BuildingPieceData" or "BuildingPieceDerivedData")) {
                    considered++;
                    if(obj.SerializationError!=null)throw new InvalidDataException(obj.SerializationError);
                    var path=obj.GetPathName();
                    if(obj.ExportType=="BuildingPieceData") {
                        var properties=(JObject?)JObject.FromObject(obj)["Properties"]??new JObject();
                        var index=properties["BuildingPieceDataIndex"];
                        data.Add(path,new JObject {
                            ["derived_path"]=properties["DerivedData"]?["AssetPathName"]?.DeepClone(),
                            ["representation_category"]=properties["RepresentationCategory"]?.DeepClone(),
                            ["serialized_index"]=index?.DeepClone()??JValue.CreateNull(),
                            ["index_policy"]="Use observed runtime index; absent serialized index is not inferred"
                        });
                        var derivedPath=(string?)properties["DerivedData"]?["AssetPathName"];
                        if(!string.IsNullOrEmpty(derivedPath) && derivedPath!="None")packages.Add(derivedPath.Split('.')[0]);
                    } else {
                        var representation=obj.GetOrDefault<FStructFallback?>("EntityRepresentation",null);
                        var collection=representation?.GetOrDefault<FStructFallback?>("EntityCollection",null);
                        var entities=collection?.GetOrDefault<FStructFallback[]>("Entities",[])??[];
                        var rows=new JArray();var entryErrors=new JArray();
                        for(var i=0;i<entities.Length;i++) {
                            try {rows.Add(Entity(entities[i],i));}
                            catch(Exception error) {entryErrors.Add($"Entity {i}: {error.Message}");}
                        }
                        derived.Add(path,new JObject { ["entities"]=rows,["entity_count"]=entities.Length,
                            ["entity_representation_present"]=collection!=null,
                            ["usable_static_mesh_metadata"]=collection!=null && entities.Length>0 && entryErrors.Count==0
                                && rows.All(row=>(bool?)row["supported_static_mesh_metadata"]==true),
                            ["complete"]=entryErrors.Count==0,["errors"]=entryErrors });
                    }
                }
            } catch(Exception error) {errors.Add(package+": "+error.Message);}
        }
        foreach(var pair in expected) {
            if(!(pair.Value=="BuildingPieceData"?data:derived).ContainsKey(pair.Key))
                errors.Add("Registry building asset was not decoded: "+pair.Key);
        }
        foreach(var property in data.Properties()) {
            var path=(string?)property.Value["derived_path"];
            property.Value["derived_available"]=path!=null && derived.ContainsKey(path);
        }
        var result=new JObject {
            ["magic"]="S3BL1",["schema"]=1,["complete"]=errors.Count==0,
            ["mapping_sha256"]=Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(mappings))).ToLowerInvariant(),
            ["data"]=data,["derived"]=derived,["errors"]=errors,["considered"]=considered,
            ["discovery"]="mounted_asset_registry_classes_and_explicit_derived_references",
            ["registry_count"]=registries.Length,["registered_building_assets"]=expected.Count,
            ["collision_policy"]="Authored preview metadata only; runtime placement/body policy must be independently verified",
            ["coordinate_system"]="Unreal centimetres XYZ, entity transforms local to the building piece"
        };
        Directory.CreateDirectory(output);
        var pathOut=Path.Combine(output,"building-catalogue.json");
        File.WriteAllText(pathOut+".tmp",result.ToString(Formatting.None));
        File.Move(pathOut+".tmp",pathOut,true);
        Console.WriteLine($"BUILDING CATALOGUE: {data.Count} data assets, {derived.Count} derived assets, {errors.Count} errors");
        if(errors.Count!=0)throw new InvalidDataException("Building metadata export was incomplete");
    }
}
