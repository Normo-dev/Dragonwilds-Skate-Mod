using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Objects.Properties;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;

static class AuthoredSelfTest
{
    static void Check(bool condition,string message) {
        if(!condition)throw new Exception("Authored regression: "+message);
        Console.WriteLine("PASS "+message);
    }
    public static void Run() {
        var template=new USceneComponent{Name="FixtureTemplate"};
        var instance=new USceneComponent{Name="FixtureInstance",Template=new ResolvedLoadedObject(template)};
        PropertyUtil.Set(template,"RelativeRotation",new FRotator(0,90,0));
        PropertyUtil.Set(template,"RelativeScale3D",new FVector(2,3,4));
        PropertyUtil.Set(instance,"RelativeLocation",new FVector(10,20,30));
        var resolver=new AuthoredCollision();
        var relative=resolver.Relative(instance);
        Check(Math.Abs(relative.Rotation.Z-new FRotator(0,90,0).Quaternion().Z)<0.00001,
            "position override preserves archetype rotation");
        Check(relative.Translation.X==10 && relative.Scale3D.Y==3,
            "position and independently inherited scale are retained");

        var inheritedBody=new FStructFallback();
        PropertyUtil.Set(inheritedBody,"CollisionEnabled",new NameProperty(new FName("ECollisionEnabled::NoCollision")));
        PropertyUtil.Set(inheritedBody,"CollisionProfileName",new NameProperty(new FName("NoCollision")));
        PropertyUtil.Set(template,"BodyInstance",inheritedBody);
        var overrideBody=new FStructFallback();
        PropertyUtil.Set(overrideBody,"MaxAngularVelocity",new FloatProperty(3600));
        PropertyUtil.Set(instance,"BodyInstance",overrideBody);
        var merged=new AuthoredCollision().Body(instance);
        Check((string?)merged["CollisionProfileName"]=="NoCollision" && (float?)merged["MaxAngularVelocity"]==3600,
            "one BodyInstance member preserves inherited collision disable");
        PropertyUtil.Set(template,"StaticMesh",new FPackageIndex((IPackage)null!,42));
        PropertyUtil.Set(instance,"StaticMesh",new FPackageIndex());
        Check(new AuthoredCollision().Value(instance,"StaticMesh",new FPackageIndex()).IsNull,
            "explicit empty mesh does not restore an inherited mesh");
        template.Template=new ResolvedLoadedObject(instance);
        var rejected=false;
        try {new AuthoredCollision().Chain(instance);}catch(InvalidDataException){rejected=true;}
        Check(rejected,"cyclic archetypes are rejected");
    }
}
