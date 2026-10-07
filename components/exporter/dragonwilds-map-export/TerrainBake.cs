using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Component.Landscape;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Core.Math;
using Newtonsoft.Json;

static class TerrainBake {
    static int Texel(int vertex,int subsection) {
        if(vertex==0)return 0;
        var sub=(vertex-1)/subsection;
        return sub*(subsection+1)+(vertex-1)%subsection+1;
    }
    public static void Export(IPackage package,string folder) {
        Directory.CreateDirectory(folder);
        using var manifest=new StreamWriter(Path.Combine(folder,"terrain.jsonl"));
        int count=0;long vertices=0;
        foreach(var c in package.GetExports().OfType<ULandscapeComponent>()) {
            try {
                var texture=c.GetHeightmap()??throw new Exception("Missing heightmap");
                var mip=texture.GetMip(0)??throw new Exception("Missing mip zero");
                var data=mip.BulkData?.Data??throw new Exception("Missing heightmap data");
                if(texture.Format!=EPixelFormat.PF_B8G8R8A8 || data.Length!=mip.SizeX*mip.SizeY*4)
                    throw new Exception("Unsupported heightmap storage");
                int n=c.ComponentSizeQuads+1;
                int ox=(int)(mip.SizeX*c.HeightmapScaleBias.Z),oy=(int)(mip.SizeY*c.HeightmapScaleBias.W);
                var transform=c.GetAbsoluteTransform();
                byte[]? visibilityData=null;int visWidth=0,visOx=0,visOy=0,visChannel=0;
                foreach(var allocation in c.WeightmapLayerAllocations) {
                    if(!allocation.LayerInfo.Name.Contains("Visibility",StringComparison.OrdinalIgnoreCase))continue;
                    var weights=c.GetWeightmapTextures();
                    var wt=weights[allocation.WeightmapTextureIndex];var wm=wt.GetMip(0)??throw new Exception("Missing visibility mip");
                    if(wt.Format!=EPixelFormat.PF_B8G8R8A8)throw new Exception("Unsupported visibility storage");
                    visibilityData=wm.BulkData?.Data??throw new Exception("Missing visibility bytes");visWidth=wm.SizeX;
                    visOx=(int)(wm.SizeX*c.WeightmapScaleBias.Z);visOy=(int)(wm.SizeY*c.WeightmapScaleBias.W);
                    visChannel=new[]{2,1,0,3}[allocation.WeightmapTextureChannel];
                }
                var basename=count.ToString("D4");
                using var points=new BinaryWriter(File.Create(Path.Combine(folder,basename+".points")));
                using var holes=new BinaryWriter(File.Create(Path.Combine(folder,basename+".visibility")));
                var min=new[]{double.PositiveInfinity,double.PositiveInfinity,double.PositiveInfinity};
                var max=new[]{double.NegativeInfinity,double.NegativeInfinity,double.NegativeInfinity};
                for(int y=0;y<n;y++)for(int x=0;x<n;x++) {
                    var tx=Texel(x,c.SubsectionSizeQuads);var ty=Texel(y,c.SubsectionSizeQuads);
                    int offset=((ty+oy)*mip.SizeX+tx+ox)*4;
                    var height=((data[offset+2]<<8)+data[offset+1]-32768)/128f;
                    var p=transform.TransformPosition(new FVector(x,y,height));
                    points.Write(p.X);points.Write(p.Y);points.Write(p.Z);
                    var values=new double[]{p.X,p.Y,p.Z};for(int i=0;i<3;i++){min[i]=Math.Min(min[i],values[i]);max[i]=Math.Max(max[i],values[i]);}
                    holes.Write(visibilityData is null?(byte)0:visibilityData[((ty+visOy)*visWidth+tx+visOx)*4+visChannel]);
                }
                manifest.WriteLine(JsonConvert.SerializeObject(new {name=c.GetFullName(),file=basename,n,min,max,
                    mirrored=transform.GetDeterminant()<0,collisionMip=c.GetOrDefault("CollisionMipLevel",0),xyOffset=c.GetOrDefault<object?>("XYOffsetmapTexture",null)}));
                count++;vertices+=(long)n*n;
            }catch(Exception e){Console.WriteLine($"TERRAIN OMITTED {c.GetFullName()} {e.Message}");Environment.ExitCode=1;}
        }
        Console.WriteLine($"TERRAIN EXPORTED {package.Name}: {count} components, {vertices} vertices");
    }
}
