using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Exports.Component.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;

// Component-local authored attachment transforms. Skeletal attachments use the
// mesh's actual reference pose; a live scene snapshot supplies animated poses.
// This is not an offline animation evaluator.
static class AuthoredSocket
{
    public const string TransformPolicy = "authored_mesh_socket_or_skeletal_reference_pose_v1";

    static bool Same(string left,string right) =>
        StringComparer.OrdinalIgnoreCase.Equals(left,right); // Unreal FName equality

    static T Checked<T>(T? value,string description) where T:UObject
    {
        if(value==null) throw new InvalidDataException("Missing "+description);
        if(value.SerializationError!=null)
            throw new InvalidDataException("Partially decoded "+description+": "+value.SerializationError);
        return value;
    }

    static FTransform Valid(FTransform value,string description)
    {
        var numbers=new[]{value.Translation.X,value.Translation.Y,value.Translation.Z,
            value.Scale3D.X,value.Scale3D.Y,value.Scale3D.Z,
            value.Rotation.X,value.Rotation.Y,value.Rotation.Z,value.Rotation.W};
        if(numbers.Any(n=>!float.IsFinite(n)) || !value.IsRotationNormalized)
            throw new InvalidDataException("Invalid authored attachment transform: "+description);
        return value;
    }

    static FTransform RelativeSocket(UObject socket,AuthoredCollision resolver) => Valid(new FTransform(
        resolver.Value(socket,"RelativeRotation",FRotator.ZeroRotator),
        resolver.Value(socket,"RelativeLocation",FVector.ZeroVector),
        resolver.Value(socket,"RelativeScale",FVector.OneVector)),socket.GetPathName());

    static T? Find<T>(IEnumerable<FPackageIndex> references,string name,AuthoredCollision resolver)
        where T:UObject
    {
        foreach(var reference in references) {
            if(reference.IsNull) continue;
            var socket=Checked(reference.Load<T>(),"socket "+reference.ResolvedObject?.GetPathName());
            if(Same(resolver.Value(socket,"SocketName",new FName("None")).Text,name)) return socket;
        }
        return null;
    }

    // Matches mesh-first socket lookup. A skeleton socket's BoneName is resolved
    // in the mesh reference skeleton, not the skeleton asset's different pose.
    public static FTransform GetSocketTransform(USceneComponent parent,string socket,AuthoredCollision resolver)
    {
        Checked(parent,"attachment parent");
        if(string.IsNullOrEmpty(socket) || Same(socket,"None")) return FTransform.Identity;
        if(parent is UStaticMeshComponent) {
            var reference=resolver.Value(parent,"StaticMesh",new FPackageIndex());
            var mesh=Checked(reference.Load<UStaticMesh>(),"static attachment mesh");
            var found=Find<UStaticMeshSocket>(resolver.Value(mesh,"Sockets",mesh.Sockets??[]),socket,resolver)
                ??throw new InvalidDataException("Missing static mesh socket: "+socket+" on "+mesh.GetPathName());
            return RelativeSocket(found,resolver);
        }
        if(parent is USkinnedMeshComponent) {
            var reference=resolver.Value(parent,"SkeletalMesh",new FPackageIndex());
            if(reference.IsNull) reference=resolver.Value(parent,"SkinnedAsset",new FPackageIndex());
            var mesh=Checked(reference.Load<USkeletalMesh>(),"skeletal attachment mesh");
            var found=Find<USkeletalMeshSocket>(resolver.Value(mesh,"Sockets",mesh.Sockets),socket,resolver);
            if(found==null) {
                var skeletonReference=resolver.Value(mesh,"Skeleton",mesh.Skeleton);
                if(skeletonReference is {IsNull:false}) {
                    var skeleton=Checked(skeletonReference.Load<USkeleton>(),"attachment skeleton");
                    found=Find<USkeletalMeshSocket>(resolver.Value(skeleton,"Sockets",skeleton.Sockets),socket,resolver);
                }
            }
            var bone=found==null?socket:resolver.Value(found,"BoneName",new FName("None")).Text;
            var referenceSkeleton=mesh.ReferenceSkeleton
                ??throw new InvalidDataException("Missing skeletal mesh reference pose");
            var transform=BoneTransform(referenceSkeleton.FinalRefBoneInfo,referenceSkeleton.FinalRefBonePose,bone);
            return found==null?transform:Valid(RelativeSocket(found,resolver)*transform,"socket "+socket);
        }
        throw new InvalidDataException("Unsupported socket parent: "+parent.GetFullName());
    }

    // Kept separate so hierarchy order, scale, and malformed parent indices can
    // be verified without substituting synthetic assets for an owned mesh.
    internal static FTransform BoneTransform(FMeshBoneInfo[] bones,FTransform[] localPoses,string name)
    {
        if(bones.Length==0 || bones.Length!=localPoses.Length)
            throw new InvalidDataException("Inconsistent authored reference skeleton");
        var index=Array.FindIndex(bones,b=>Same(b.Name.Text,name));
        if(index<0) throw new InvalidDataException("Missing attachment bone: "+name);
        var result=FTransform.Identity;
        var seen=new HashSet<int>();
        while(index!=-1) {
            if(index<0 || index>=bones.Length || !seen.Add(index))
                throw new InvalidDataException("Cyclic or invalid attachment bone hierarchy");
            result=Valid(result*Valid(localPoses[index],bones[index].Name.Text),name);
            index=bones[index].ParentIndex;
        }
        return result;
    }
}
