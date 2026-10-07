using System.Security.Cryptography;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using Newtonsoft.Json.Linq;

sealed record AuthoredDecision(bool included,string reason,int enabled,int object_type,
    int response_to_player,int player_response,string profile,string native_class);

// Policy values come from the installed game's collision defaults and player
// capsule. Unknown values invalidate the export rather than becoming obstacles.
sealed class AuthoredPolicy
{
    readonly JObject data;
    public string Fingerprint {get;}
    public int QueryChannel {get;}
    public AuthoredPolicy(string file) {
        var bytes=File.ReadAllBytes(file);
        data=JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
        if((string?)data["schema"]!="S3AP1")throw new InvalidDataException("Expected installed collision policy S3AP1");
        QueryChannel=Number(data["query_channel"],0,31,"query channel");
        Fingerprint=Convert.ToHexString(SHA256.HashData(bytes));
        Responses(data["query_responses"]);
        Responses(data["default_responses"]);
    }
    static int Number(JToken? value,int min,int max,string field) {
        if(value?.Type!=JTokenType.Integer)throw new InvalidDataException("Missing/invalid "+field);
        var result=value.Value<int>();
        if(result<min || result>max)throw new InvalidDataException("Out of range "+field);
        return result;
    }
    static int[] Responses(JToken? value) {
        if(value is not JArray array || array.Count!=32)throw new InvalidDataException("Expected 32 collision responses");
        return array.Select(v=>Number(v,0,2,"collision response")).ToArray();
    }
    static string Short(JToken value)=>value.ToString().Split("::").Last();
    int Channel(JToken value) {
        if(value.Type==JTokenType.Integer)return Number(value,0,31,"object type");
        var key=Short(value);
        if(key.StartsWith("ECC_",StringComparison.Ordinal))key=key[4..];
        return Number((data["channel_names"] as JObject)?.GetValue(key,StringComparison.OrdinalIgnoreCase),0,31,"channel "+key);
    }
    static int Enabled(JToken value) {
        if(value.Type==JTokenType.Integer)return Number(value,0,5,"collision enabled");
        return Short(value) switch {
            "NoCollision"=>0,"QueryOnly"=>1,"PhysicsOnly"=>2,"QueryAndPhysics"=>3,
            "ProbeOnly"=>4,"QueryAndProbe"=>5,
            _=>throw new InvalidDataException("Unknown collision enabled "+value)
        };
    }
    static int Response(JToken? value) {
        // FResponseChannel's omitted member is Block. Confirmed by the owned
        // profile config's omitted values and the live reflected struct values.
        if(value==null)return 2;
        if(value.Type==JTokenType.Integer)return Number(value,0,2,"response");
        return Short(value) switch {"ECR_Ignore"=>0,"ECR_Overlap"=>1,"ECR_Block"=>2,
            _=>throw new InvalidDataException("Unknown collision response "+value)};
    }
    public AuthoredDecision Resolve(UStaticMeshComponent component,AuthoredCollision resolver) {
        var body=resolver.Body(component);
        var owner=component.Outer?.Load<UObject>()??throw new InvalidDataException("Missing component owner");
        var enabledOwner=resolver.Value<bool?>(owner,"bActorEnableCollision",null)
            ??data["actor_default_collision"]?.Value<bool>()
            ??throw new InvalidDataException("Actor collision default unavailable");
        if(!enabledOwner)return new(false,"actor_disabled",0,-1,0,0,"","");
        if(body["CollisionEnabled"] is {} early && Enabled(early) is 0 or 2 or 4)
            return new(false,"no_query_collision",Enabled(early),-1,0,0,"","");
        var nativeClass=resolver.NativeClass(component);
        var native=data["components"]?[nativeClass] as JObject
            ??throw new InvalidDataException("Uncaptured native collision default: "+nativeClass);
        var profile=(string?)body["CollisionProfileName"]??(string?)native["profile"]
            ??throw new InvalidDataException("Missing collision profile");
        var preset=data["profiles"]?[profile] as JObject;
        if(preset==null && profile!="None" && profile!="Custom" && profile!="")
            throw new InvalidDataException("Unknown collision profile: "+profile);
        var enabled=Enabled(body["CollisionEnabled"]??preset?["enabled"]??native["enabled"]
            ??throw new InvalidDataException("Missing collision enabled"));
        if(enabled is 0 or 2 or 4)return new(false,"no_query_collision",enabled,-1,0,0,profile,nativeClass);
        var objectToken=body["ObjectType"]??preset?["object_type"];
        if(objectToken?.Type==JTokenType.Null)objectToken=null;
        var objectType=Channel(objectToken??native["object_type"]??throw new InvalidDataException("Missing object type"));
        var responses=Responses(preset?["responses"]??native["responses"]);
        if(body["CollisionResponses"]?["ResponseArray"] is JArray array) {
            // Serialized response arrays describe exceptions to the configured
            // channel defaults; replacing the array also replaces old exceptions.
            responses=Responses(data["default_responses"]);
            foreach(var item in array)responses[Channel(item["Channel"]??throw new InvalidDataException("Missing response channel"))]=Response(item["Response"]);
        }
        var response=responses[QueryChannel];
        var player=Responses(data["query_responses"])[objectType];
        var included=response==2 && player==2;
        return new(included,included?"query_and_player_block":"pair_does_not_block",enabled,objectType,response,player,profile,nativeClass);
    }
}
