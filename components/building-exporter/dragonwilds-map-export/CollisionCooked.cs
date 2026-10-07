using System.Numerics;
using CUE4Parse.UE4.Assets.Readers;
using CUE4Parse.UE4.Objects.Chaos;
using CUE4Parse.UE4.Objects.Chaos.Convex;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.PhysicsEngine;

// Reads the game's existing Chaos shapes. In particular, cooked convexes can
// contain real extrusion faces missing from the editor VertexData/IndexData.
static class CollisionCooked {
    static Vector3 V(FVector p)=>new(p.X,p.Y,p.Z);
    static int I<T>(T x) where T:unmanaged=>Convert.ToInt32(x);

    static int[] Faces<T>(FConvex shape,TConvexHalfEdgeStructureData<T> data) where T:unmanaged {
        var result=new List<int>();
        for(int plane=0;plane<data.Planes.Length;plane++) {
            var face=data.Planes[plane];int first=I(face.FirstHalfEdgeIndex),count=I(face.NumHalfEdges);
            if(count<3 || first<0 || first+count>data.HalfEdges.Length)throw new Exception("Invalid cooked convex face");
            var vertices=data.HalfEdges.Skip(first).Take(count).Select(e=>I(e.VertexIndex)).ToArray();
            if(vertices.Any(i=>i<0 || i>=shape.Vertices.Length))throw new Exception("Invalid cooked convex vertex index");
            var n=shape.Planes[plane].MNormal;var normal=new Vector3(n[0],n[1],n[2]);
            for(int i=1;i<count-1;i++) {
                int a=vertices[0],b=vertices[i],c=vertices[i+1];
                var cross=Vector3.Cross(V(shape.Vertices[b])-V(shape.Vertices[a]),V(shape.Vertices[c])-V(shape.Vertices[a]));
                if(cross.LengthSquared()<1e-16)continue;
                if(Vector3.Dot(cross,normal)<0)(b,c)=(c,b);
                result.Add(a);result.Add(b);result.Add(c);
            }
        }
        return result.ToArray();
    }

    static int[] Faces(FConvex shape) {
        var d=shape.StructureData.Data;
        return shape.StructureData.IndexType switch {
            EIndexType.Small=>Faces(shape,d.DataS!),EIndexType.Medium=>Faces(shape,d.DataM!),
            EIndexType.Large=>Faces(shape,d.DataL!),_=>throw new Exception("Cooked convex topology unavailable")};
    }

    static double Match(FVector[] original,FConvex cooked) {
        if(original.Length==0 || cooked.Vertices.Length==0)return double.PositiveInfinity;
        var a=original.Select(V).ToArray();var b=cooked.Vertices.Select(V).ToArray();
        Vector3 amin=a.Aggregate(Vector3.Min),amax=a.Aggregate(Vector3.Max),bmin=b.Aggregate(Vector3.Min),bmax=b.Aggregate(Vector3.Max);
        // Small cook extrusion is real collision (e.g. 0.1 cm for the rubble's
        // triangle). Reject a different, much larger overlapping convex.
        double bounds=Vector3.Distance(amin,bmin)+Vector3.Distance(amax,bmax);
        if(bounds>Math.Max(2.0,Vector3.Distance(amin,amax)*.001))return double.PositiveInfinity;
        double nearest=a.Max(p=>b.Min(v=>Vector3.DistanceSquared(p,v)));
        if(nearest>.04)return double.PositiveInfinity;
        return bounds+Math.Sqrt(nearest);
    }

    static bool SameBounds(FVector[] source,FConvex? cooked) {
        if(cooked is null || !cooked.bDoCollide || cooked.Vertices.Length==0)return true;
        if(source.Length==0)return false;
        var a=source.Select(V).ToArray();var b=cooked.Vertices.Select(V).ToArray();
        var amin=a.Aggregate(Vector3.Min);var amax=a.Aggregate(Vector3.Max);
        var bmin=b.Aggregate(Vector3.Min);var bmax=b.Aggregate(Vector3.Max);
        return Vector3.Distance(amin,bmin)+Vector3.Distance(amax,bmax)<=Math.Max(2,Vector3.Distance(amin,amax)*.001);
    }

    static object TriangleMesh(FChaosArchive ar) {
        if(!ar.ReadBoolean())throw new Exception("Null cooked triangle mesh");
        int tag=ar.Read<int>();if(tag<0 || ar.Read<EImplicitObjectType>()!=EImplicitObjectType.TriangleMesh)throw new Exception("Unsupported cooked triangle object");
        new FImplicitObject().Serialize(ar);
        if(!ar.ReadBoolean())throw new Exception("Cooked triangle vertices unavailable");
        int count=ar.Read<int>();if(count<3 || count>5_000_000)throw new Exception("Invalid cooked triangle vertex count");
        var vertices=ar.ReadArray<FVector>(count);
        bool large=ar.ReadBoolean();int faces=ar.Read<int>();if(faces<1 || faces>5_000_000)throw new Exception("Invalid cooked triangle face count");
        var indices=large?ar.ReadArray<int>(checked(faces*3)):ar.ReadArray<ushort>(checked(faces*3)).Select(x=>(int)x).ToArray();
        if(indices.Any(i=>i<0 || i>=vertices.Length))throw new Exception("Invalid cooked triangle index");
        var bounds=TBox<float>.SerializeAsAABB(ar,3);
        var min=vertices.Select(V).Aggregate(Vector3.Min);var max=vertices.Select(V).Aggregate(Vector3.Max);
        var bmin=new Vector3(bounds.MMin[0],bounds.MMin[1],bounds.MMin[2]);var bmax=new Vector3(bounds.MMax[0],bounds.MMax[1],bounds.MMax[2]);
        if(Vector3.Distance(min,bmin)+Vector3.Distance(max,bmax)>.1f)throw new Exception("Cooked triangle bounds do not match decoded vertices");
        return new{vertices,indices,source="Chaos cooked triangle mesh"};
    }

    public static object Inspect(UBodySetup body,bool includeTriangleMesh=false) {
        bool shared=body.GetOrDefault<bool>("bSharedCookedData");
        try {
            var formats=body.CookedFormatData?.Formats;
            var chaos=formats?.FirstOrDefault(x=>x.Key.Text=="Chaos").Value;
            if(chaos is null || chaos.GetDataSize()==0)
                return new{checked_data=true,success=true,has_payload=false,payload_bytes=0,convex_count=0,triangle_mesh_count=0,shared};
            if(!chaos.TryCreateReader("Chaos collision",out var raw))throw new Exception("Cooked Chaos bytes unavailable");
            using var ar=new FChaosArchive(new FAssetArchive(raw,body.Owner));
            int scalarSize=ar.Read<int>();if(scalarSize!=4)throw new Exception($"Unsupported Chaos scalar size {scalarSize}");
            // FChaosDerivedDataReader stores the convex array before triangle meshes.
            var convexes=ar.ReadPtrArray<FConvex>();int triangleMeshes=ar.Read<int>();
            if(triangleMeshes<0 || triangleMeshes>1024)throw new Exception("Invalid cooked triangle mesh count");
            var elements=body.AggGeom?.ConvexElems??[];var fallbacks=new Dictionary<int,object>();var absent=new List<int>();
            var nonempty=elements.Select((element,index)=>(element,index)).Where(x=>x.element.VertexData.Length>0).ToArray();
            bool ordered=(convexes.Length==nonempty.Length || convexes.Length==nonempty.Length*2) &&
                nonempty.Select((x,index)=>SameBounds(x.element.VertexData,convexes[index]) ||
                    SameBounds(x.element.VertexData.Select(p=>x.element.Transform.TransformPosition(p)).ToArray(),convexes[index])).All(x=>x);
            for(int i=0;i<elements.Length;i++) {
                var element=elements[i];if(element.IndexData.Length>0)continue;
                if(convexes.All(c=>c is null || !c.bDoCollide || c.Vertices.Length==0)){absent.Add(i);continue;}
                if(ordered) {
                    int active=Array.FindIndex(nonempty,x=>x.index==i);
                    if(active<0 || convexes[active] is null || !convexes[active]!.bDoCollide || convexes[active]!.Vertices.Length==0){absent.Add(i);continue;}
                }
                FConvex? best=null;double score=double.PositiveInfinity;string space="element";
                foreach(var candidate in convexes) {
                    if(candidate is null || !candidate.bDoCollide)continue;
                    double candidateScore=Match(element.VertexData,candidate);
                    if(candidateScore<score){score=candidateScore;best=candidate;space="element";}
                    var transformed=element.VertexData.Select(p=>element.Transform.TransformPosition(p)).ToArray();
                    candidateScore=Match(transformed,candidate);
                    if(candidateScore<score){score=candidateScore;best=candidate;space="body";}
                }
                if(best is null)continue;
                int[] indices=Faces(best);if(indices.Length==0)continue;
                fallbacks[i]=new{vertices=best.Vertices,indices,space,volume=best.Volume,
                    plane_count=best.Planes.Length,source="Chaos cooked convex half-edges",match_error_cm=score};
            }
            object? triangleMesh=null;
            if(includeTriangleMesh) {
                if(triangleMeshes!=1)throw new Exception($"Expected one cooked triangle mesh; found {triangleMeshes}");
                triangleMesh=TriangleMesh(ar);
            }
            return new{checked_data=true,success=true,has_payload=true,payload_bytes=chaos.GetDataSize(),
                convex_count=convexes.Count(c=>c is not null && c.bDoCollide && c.Vertices.Length>0),
                stored_convex_count=convexes.Length,triangle_mesh_count=triangleMeshes,shared,fallbacks,
                absent_convex_indices=absent,validated_element_order=ordered,triangle_mesh=triangleMesh};
        }catch(Exception e){return new{checked_data=true,success=false,shared,error=e.Message};}
    }
}
