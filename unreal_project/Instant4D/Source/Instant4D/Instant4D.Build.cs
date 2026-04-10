using UnrealBuildTool;

public class Instant4D : ModuleRules
{
    public Instant4D(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "InputCore",
            "HTTP",
            "HTTPServer",
            "Json",
            "JsonUtilities",
            "ImageWrapper",
            "RenderCore",
            "Renderer",
        });

        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "Slate",
            "SlateCore",
        });
    }
}
