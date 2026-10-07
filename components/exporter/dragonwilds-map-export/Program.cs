using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Versions;
using CUE4Parse.MappingsProvider.Usmap;
using Newtonsoft.Json;
using Serilog;
using CUE4Parse.UE4.Assets;

if(args.Length==1 && args[0]=="selftest-authored") {AuthoredSelfTest.Run();return;}
var work = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,"../../../../../"));
// CUE4Parse's default managed OodleSharp decoder keeps local exports offline.
// Do not initialize OodleHelper: it may download a proprietary native decoder.
Log.Logger = new LoggerConfiguration().WriteTo.Console().CreateLogger();
CUE4Parse.CUE4ParseLog.UseLogger(Log.Logger);
var provider = new DefaultFileProvider(args[0],SearchOption.TopDirectoryOnly,
    new VersionContainer(EGame.GAME_UE5_6),StringComparer.OrdinalIgnoreCase);
provider.Initialize();
provider.Mount();
provider.PostMount();
Console.WriteLine($"Mounted files: {provider.Files.Count}");
Directory.CreateDirectory(args[1]);
File.WriteAllLines(Path.Combine(args[1],"files.txt"),provider.Files.Keys.Order());
if(args.Length>2 && args[2]=="config") {
    foreach(var file in provider.Files.Values.Where(f=>f.Path.EndsWith(".ini",StringComparison.OrdinalIgnoreCase)
        && (f.Path.Contains("/Config/Default",StringComparison.OrdinalIgnoreCase)
            || f.Path.Equals("Engine/Config/BaseEngine.ini",StringComparison.OrdinalIgnoreCase))))
        File.WriteAllBytes(Path.Combine(args[1],Path.GetFileName(file.Path)),file.Read());
    return;
}
if(args.Length>2 && args[2]=="index") {
    using var output=new StreamWriter(Path.Combine(args[1],"map-index.jsonl"));
    foreach(var file in provider.Files.Values.Where(f=>f.Path.Contains("/Maps/World/")&&f.Path.EndsWith(".umap",StringComparison.OrdinalIgnoreCase))) {
        try {
            var package=provider.LoadPackage(file.Path);
            var names=package.NameMap.Select(n=>n.Name).ToArray();
            var landscape=names.Where(n=>n.Contains("Landscape",StringComparison.OrdinalIgnoreCase)).ToArray();
            output.WriteLine(JsonConvert.SerializeObject(new { path=file.Path,landscape,exportCount=package.ExportMapLength }));
        } catch(Exception e){output.WriteLine(JsonConvert.SerializeObject(new { path=file.Path,error=e.Message }));}
    }
    return;
}
if (args.Length>2) {
    provider.MappingsContainer = new FileUsmapTypeMappingsProvider(args[2]);
    var authoredList=args.Length>5 && args[4]=="static-authored-list";
    var authoredPolicy=args.Length>5 && (args[4]=="static-authored" || authoredList)?new AuthoredPolicy(args[5]):null;
    if(args.Length>4 && args[4]=="mesh-list") {
        foreach(var path in JsonConvert.DeserializeObject<string[]>(File.ReadAllText(args[3]))??[]) {
            try {StaticBake.Mesh(StaticBake.LoadMesh(provider,path),path,Path.Combine(args[1],"static"));}
            catch(Exception e){Console.WriteLine($"MESH FAILED {path}: {e.Message}");Environment.ExitCode=1;}
        }
        return;
    }
    var selectedFiles=provider.Files.Values.Where(f=>(f.Path.EndsWith(".umap",StringComparison.OrdinalIgnoreCase) ||
        (args.Length>4 && (args[4]=="asset" || args[4]=="geometry") && f.Path.EndsWith(".uasset",StringComparison.OrdinalIgnoreCase))) &&
        (authoredList || args.Length<4 || f.Path.Contains(args[3],StringComparison.OrdinalIgnoreCase))).ToArray();
    if(authoredList) {
        var requested=(JsonConvert.DeserializeObject<string[]>(File.ReadAllText(args[3]))??[]).ToHashSet(StringComparer.Ordinal);
        var inventory=(JsonConvert.DeserializeObject<string[]>(File.ReadAllText(Path.Combine(args[1],"authored-packages.json")))??[]).ToHashSet(StringComparer.Ordinal);
        if(requested.Count==0 || !requested.IsSubsetOf(inventory))throw new InvalidDataException("Authored retry list must be a nonempty subset of the full existing package inventory");
        selectedFiles=selectedFiles.Where(f=>requested.Contains(provider.LoadPackage(f.Path).Name)).ToArray();
        var found=selectedFiles.Select(f=>provider.LoadPackage(f.Path).Name).ToHashSet(StringComparer.Ordinal);
        if(!requested.SetEquals(found))throw new InvalidDataException("Authored retry package unavailable in provider");
    }
    if(authoredPolicy!=null && !authoredList) {
        var expected=selectedFiles.Select(f=>provider.LoadPackage(f.Path).Name).Order().ToArray();
        File.WriteAllText(Path.Combine(args[1],"authored-packages.json"),JsonConvert.SerializeObject(expected));
    }
    foreach(var file in selectedFiles) {
        Console.WriteLine(file.Path);
        try {
            var package=provider.LoadPackage(file.Path);
            if(authoredPolicy!=null) {StaticBake.ExportAuthored(package,Path.Combine(args[1],"static"),authoredPolicy);continue;}
            if(args.Length>4 && args[4]=="authored-audit") {AuthoredCollision.Audit(package,Path.Combine(args[1],"authored"));continue;}
            if(args.Length>4 && args[4]=="terrain") {
                TerrainBake.Export(package,Path.Combine(args[1],Path.GetFileNameWithoutExtension(file.Path)+"-terrain"));continue;
            }
            if(args.Length>4 && args[4]=="static") {StaticBake.Export(package,Path.Combine(args[1],"static"));continue;}
            if(args.Length>4 && args[4]=="geometry") {StaticBake.ExportGeometry(package,Path.Combine(args[1],"static"));continue;}
            var objects=package.GetExports();
            File.WriteAllText(Path.Combine(args[1],Path.GetFileNameWithoutExtension(file.Path)+".json"),JsonConvert.SerializeObject(objects,Formatting.Indented));
        } catch (Exception e) { Console.WriteLine(e.ToString()); Environment.ExitCode=1; }
    }
}
