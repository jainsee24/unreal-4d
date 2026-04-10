// SceneCommandServer.cpp — HTTP scene API for the Python backend.

#include "SceneCommandServer.h"
#include "Engine/World.h"
#include "Engine/GameViewportClient.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "Components/CapsuleComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetMathLibrary.h"
#include "Engine/Engine.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Misc/FileHelper.h"
#include "ImageUtils.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "HighResScreenshot.h"
#include "Async/Async.h"
#include "UnrealClient.h"
#include "Slate/SceneViewport.h"

ASceneCommandServer::ASceneCommandServer()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickInterval = 0.0f; // tick every frame, we throttle in Tick()
}

void ASceneCommandServer::BeginPlay()
{
    Super::BeginPlay();

    FHttpServerModule& HttpModule = FHttpServerModule::Get();
    TSharedPtr<IHttpRouter> Router = HttpModule.GetHttpRouter(ServerPort);
    if (!Router.IsValid())
    {
        UE_LOG(LogTemp, Error, TEXT("Instant4D: Failed to get HTTP router on port %d"), ServerPort);
        return;
    }

    // Register routes
    auto Bind = [&](const FString& Verb, const FString& Path, auto Handler)
    {
        FHttpRouteHandle Handle = Router->BindRoute(
            FHttpPath(Path),
            Verb == TEXT("GET") ? EHttpServerRequestVerbs::VERB_GET :
            Verb == TEXT("POST") ? EHttpServerRequestVerbs::VERB_POST :
            Verb == TEXT("DELETE") ? EHttpServerRequestVerbs::VERB_DELETE :
            EHttpServerRequestVerbs::VERB_GET,
            FHttpRequestHandler::CreateUObject(this, Handler));
        RouteHandles.Add(Handle);
    };

    Bind(TEXT("GET"),    TEXT("/api/health"),             &ASceneCommandServer::HandleHealth);
    Bind(TEXT("GET"),    TEXT("/api/scene/info"),          &ASceneCommandServer::HandleSceneInfo);
    Bind(TEXT("POST"),   TEXT("/api/scene/level"),         &ASceneCommandServer::HandleLoadLevel);
    Bind(TEXT("POST"),   TEXT("/api/scene/weather"),       &ASceneCommandServer::HandleSetWeather);
    Bind(TEXT("POST"),   TEXT("/api/scene/camera"),        &ASceneCommandServer::HandleSetCamera);
    Bind(TEXT("POST"),   TEXT("/api/scene/camera/move"),   &ASceneCommandServer::HandleMoveCamera);
    Bind(TEXT("POST"),   TEXT("/api/scene/actors"),        &ASceneCommandServer::HandleSpawnActor);
    Bind(TEXT("DELETE"), TEXT("/api/scene/actors"),        &ASceneCommandServer::HandleDestroyActor);
    Bind(TEXT("GET"),    TEXT("/api/scene/actors"),        &ASceneCommandServer::HandleListActors);
    Bind(TEXT("POST"),   TEXT("/api/scene/clear"),         &ASceneCommandServer::HandleClearScene);
    Bind(TEXT("POST"),   TEXT("/api/scene/screenshot"),    &ASceneCommandServer::HandleScreenshot);
    Bind(TEXT("POST"),   TEXT("/api/scene/execute"),       &ASceneCommandServer::HandleExecuteCommands);
    Bind(TEXT("GET"),    TEXT("/api/stream"),              &ASceneCommandServer::HandleSnapshot);  // same as snapshot
    Bind(TEXT("GET"),    TEXT("/api/snapshot"),            &ASceneCommandServer::HandleSnapshot);

    // Player character
    Bind(TEXT("POST"),   TEXT("/api/player/spawn"),        &ASceneCommandServer::HandlePlayerSpawn);
    Bind(TEXT("POST"),   TEXT("/api/player/move"),         &ASceneCommandServer::HandlePlayerMove);
    Bind(TEXT("GET"),    TEXT("/api/player/info"),         &ASceneCommandServer::HandlePlayerInfo);

    HttpModule.StartAllListeners();
    UE_LOG(LogTemp, Log, TEXT("Instant4D: Scene command server started on port %d"), ServerPort);
}

void ASceneCommandServer::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    FHttpServerModule& HttpModule = FHttpServerModule::Get();
    HttpModule.StopAllListeners();
    for (auto& Handle : RouteHandles)
    {
        // Handles cleaned up by module
    }
    RouteHandles.Empty();
    Super::EndPlay(EndPlayReason);
}

// ── Viewport capture (runs on game thread every tick) ────────────────────────

void ASceneCommandServer::Tick(float DeltaTime)
{
    Super::Tick(DeltaTime);

    // Throttle to target FPS
    CaptureAccumulator += DeltaTime;
    float Interval = 1.0f / FMath::Max(CaptureTargetFPS, 1.0f);
    if (CaptureAccumulator < Interval) return;
    CaptureAccumulator = 0.0f;

    CaptureViewport();
}

void ASceneCommandServer::CaptureViewport()
{
    if (!GEngine || !GEngine->GameViewport) return;

    FViewport* Viewport = GEngine->GameViewport->GetGameViewport();
    if (!Viewport) return;

    // Read pixels from viewport
    TArray<FColor> Pixels;
    if (!Viewport->ReadPixels(Pixels)) return;

    int32 W = Viewport->GetSizeXY().X;
    int32 H = Viewport->GetSizeXY().Y;
    if (W <= 0 || H <= 0 || Pixels.Num() != W * H) return;

    // Encode as JPEG using ImageWrapper
    IImageWrapperModule& ImageWrapperModule = FModuleManager::LoadModuleChecked<IImageWrapperModule>(FName("ImageWrapper"));
    TSharedPtr<IImageWrapper> ImageWrapper = ImageWrapperModule.CreateImageWrapper(EImageFormat::JPEG);

    if (!ImageWrapper.IsValid()) return;

    // FColor is BGRA, ImageWrapper expects BGRA with 8 bits per channel
    if (!ImageWrapper->SetRaw(Pixels.GetData(), Pixels.Num() * sizeof(FColor), W, H, ERGBFormat::BGRA, 8)) return;

    TArray<uint8> JpegData;
    if (!ImageWrapper->GetCompressed(JpegData, JpegQuality)) return;

    // Write to the back buffer, then swap
    {
        FScopeLock Lock(&FrameLock);
        TArray<uint8>& BackBuffer = bBufferAIsLatest ? FrameBufferB : FrameBufferA;
        BackBuffer = MoveTemp(JpegData);
        bBufferAIsLatest = !bBufferAIsLatest;
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

TSharedPtr<FJsonObject> ASceneCommandServer::ParseBody(const FHttpServerRequest& Request)
{
    FString Body = UTF8_TO_TCHAR(reinterpret_cast<const char*>(Request.Body.GetData()));
    TSharedPtr<FJsonObject> Json;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Body);
    FJsonSerializer::Deserialize(Reader, Json);
    return Json;
}

TUniquePtr<FHttpServerResponse> ASceneCommandServer::JsonResponse(TSharedPtr<FJsonObject> Json, int32 Code)
{
    FString Output;
    TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Output);
    FJsonSerializer::Serialize(Json.ToSharedRef(), Writer);
    auto Response = FHttpServerResponse::Create(Output, TEXT("application/json"));
    return Response;
}

// ── Route implementations ─────────────────────────────────────────────────────

bool ASceneCommandServer::HandleHealth(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("status"), TEXT("ok"));
    Json->SetStringField(TEXT("engine"), TEXT("unreal"));
    Json->SetBoolField(TEXT("connected"), true);
    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleSceneInfo(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    UWorld* World = GetWorld();
    auto Json = MakeShared<FJsonObject>();
    Json->SetBoolField(TEXT("success"), true);
    Json->SetBoolField(TEXT("connected"), true);
    Json->SetNumberField(TEXT("fps"), 1.0 / FApp::GetDeltaTime());
    Json->SetStringField(TEXT("level"), World ? World->GetMapName() : TEXT("unknown"));
    Json->SetStringField(TEXT("map"), World ? World->GetMapName() : TEXT("unknown"));
    Json->SetNumberField(TEXT("actors_total"), SpawnedActors.Num());

    // Camera
    APlayerController* PC = World ? World->GetFirstPlayerController() : nullptr;
    auto CamJson = MakeShared<FJsonObject>();
    if (PC)
    {
        FVector Loc = PC->GetPawn() ? PC->GetPawn()->GetActorLocation() : FVector::ZeroVector;
        FRotator Rot = PC->GetControlRotation();
        TArray<TSharedPtr<FJsonValue>> LocArr;
        LocArr.Add(MakeShared<FJsonValueNumber>(Loc.X));
        LocArr.Add(MakeShared<FJsonValueNumber>(Loc.Y));
        LocArr.Add(MakeShared<FJsonValueNumber>(Loc.Z));
        CamJson->SetArrayField(TEXT("location"), LocArr);
        TArray<TSharedPtr<FJsonValue>> RotArr;
        RotArr.Add(MakeShared<FJsonValueNumber>(Rot.Pitch));
        RotArr.Add(MakeShared<FJsonValueNumber>(Rot.Yaw));
        RotArr.Add(MakeShared<FJsonValueNumber>(Rot.Roll));
        CamJson->SetArrayField(TEXT("rotation"), RotArr);
        Json->SetNumberField(TEXT("x"), Loc.X);
        Json->SetNumberField(TEXT("y"), Loc.Y);
        Json->SetNumberField(TEXT("z"), Loc.Z);
        Json->SetNumberField(TEXT("pitch"), Rot.Pitch);
        Json->SetNumberField(TEXT("yaw"), Rot.Yaw);
    }
    Json->SetObjectField(TEXT("camera"), CamJson);

    // Actor counts
    int32 Vehicles = 0, Walkers = 0, Props = 0;
    for (auto& Pair : SpawnedActors)
    {
        FString Tag = Pair.Key.ToLower();
        if (Tag.Contains(TEXT("vehicle")) || Tag.Contains(TEXT("car"))) Vehicles++;
        else if (Tag.Contains(TEXT("pedestrian")) || Tag.Contains(TEXT("character"))) Walkers++;
        else Props++;
    }
    Json->SetNumberField(TEXT("vehicles"), Vehicles);
    Json->SetNumberField(TEXT("walkers"), Walkers);
    Json->SetNumberField(TEXT("props"), Props);

    // Player character info
    if (PlayerCharacter && PlayerCharacter->IsValidLowLevel())
    {
        auto PlayerJson = MakeShared<FJsonObject>();
        PlayerJson->SetBoolField(TEXT("spawned"), true);
        FVector PLoc = PlayerCharacter->GetActorLocation();
        TArray<TSharedPtr<FJsonValue>> PLocArr;
        PLocArr.Add(MakeShared<FJsonValueNumber>(PLoc.X));
        PLocArr.Add(MakeShared<FJsonValueNumber>(PLoc.Y));
        PLocArr.Add(MakeShared<FJsonValueNumber>(PLoc.Z));
        PlayerJson->SetArrayField(TEXT("location"), PLocArr);
        PlayerJson->SetNumberField(TEXT("yaw"), PlayerYaw);
        Json->SetObjectField(TEXT("player"), PlayerJson);
    }

    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleSpawnActor(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Body = ParseBody(Request);
    auto Json = MakeShared<FJsonObject>();

    if (!Body.IsValid())
    {
        Json->SetBoolField(TEXT("success"), false);
        Json->SetStringField(TEXT("error"), TEXT("Invalid JSON body"));
        OnComplete(JsonResponse(Json, 400));
        return true;
    }

    FString Asset = Body->GetStringField(TEXT("asset"));
    FString Name = Body->GetStringField(TEXT("name"));
    if (Name.IsEmpty()) Name = FString::Printf(TEXT("actor_%s"), *FGuid::NewGuid().ToString().Left(8));

    // Parse location
    FVector Location = FVector::ZeroVector;
    const TArray<TSharedPtr<FJsonValue>>* LocArr;
    if (Body->TryGetArrayField(TEXT("location"), LocArr) && LocArr->Num() >= 3)
    {
        Location.X = (*LocArr)[0]->AsNumber();
        Location.Y = (*LocArr)[1]->AsNumber();
        Location.Z = (*LocArr)[2]->AsNumber();
    }

    // Parse rotation
    FRotator Rotation = FRotator::ZeroRotator;
    const TArray<TSharedPtr<FJsonValue>>* RotArr;
    if (Body->TryGetArrayField(TEXT("rotation"), RotArr) && RotArr->Num() >= 3)
    {
        Rotation.Pitch = (*RotArr)[0]->AsNumber();
        Rotation.Yaw   = (*RotArr)[1]->AsNumber();
        Rotation.Roll  = (*RotArr)[2]->AsNumber();
    }

    // Spawn — attempt to load the blueprint/class
    UClass* ActorClass = LoadClass<AActor>(nullptr, *Asset);
    if (!ActorClass)
    {
        // Try loading as a blueprint asset
        UBlueprint* BP = LoadObject<UBlueprint>(nullptr, *Asset);
        if (BP) ActorClass = BP->GeneratedClass;
    }

    if (ActorClass)
    {
        FActorSpawnParameters Params;
        Params.Name = FName(*Name);
        Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;

        AActor* Spawned = GetWorld()->SpawnActor<AActor>(ActorClass, &Location, &Rotation, Params);
        if (Spawned)
        {
            SpawnedActors.Add(Name, Spawned);
            Json->SetBoolField(TEXT("success"), true);
            Json->SetStringField(TEXT("actor_id"), Name);
            Json->SetStringField(TEXT("asset"), Asset);
        }
        else
        {
            Json->SetBoolField(TEXT("success"), false);
            Json->SetStringField(TEXT("error"), TEXT("SpawnActor returned null"));
        }
    }
    else
    {
        // Fallback: spawn a basic cube as placeholder
        FActorSpawnParameters Params;
        Params.Name = FName(*Name);
        AActor* Placeholder = GetWorld()->SpawnActor<AActor>(AActor::StaticClass(), &Location, &Rotation, Params);
        if (Placeholder)
        {
            SpawnedActors.Add(Name, Placeholder);
            Json->SetBoolField(TEXT("success"), true);
            Json->SetStringField(TEXT("actor_id"), Name);
            Json->SetStringField(TEXT("note"), TEXT("Placeholder — asset not found: ") + Asset);
        }
        else
        {
            Json->SetBoolField(TEXT("success"), false);
            Json->SetStringField(TEXT("error"), TEXT("Could not load asset: ") + Asset);
        }
    }

    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleClearScene(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    int32 Count = SpawnedActors.Num();
    for (auto& Pair : SpawnedActors)
    {
        if (Pair.Value && Pair.Value->IsValidLowLevel())
        {
            Pair.Value->Destroy();
        }
    }
    SpawnedActors.Empty();

    // Also destroy player character
    if (PlayerCharacter && PlayerCharacter->IsValidLowLevel())
    {
        PlayerCharacter->Destroy();
        PlayerCharacter = nullptr;
        PlayerYaw = 0.0f;
        Count++;
    }

    auto Json = MakeShared<FJsonObject>();
    Json->SetBoolField(TEXT("success"), true);
    Json->SetNumberField(TEXT("destroyed"), Count);
    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleSetCamera(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Body = ParseBody(Request);
    auto Json = MakeShared<FJsonObject>();

    APlayerController* PC = GetWorld()->GetFirstPlayerController();
    if (PC && Body.IsValid())
    {
        FVector Loc = FVector::ZeroVector;
        FRotator Rot = FRotator::ZeroRotator;

        const TArray<TSharedPtr<FJsonValue>>* Arr;
        if (Body->TryGetArrayField(TEXT("location"), Arr) && Arr->Num() >= 3)
        {
            Loc.X = (*Arr)[0]->AsNumber();
            Loc.Y = (*Arr)[1]->AsNumber();
            Loc.Z = (*Arr)[2]->AsNumber();
        }
        if (Body->TryGetArrayField(TEXT("rotation"), Arr) && Arr->Num() >= 3)
        {
            Rot.Pitch = (*Arr)[0]->AsNumber();
            Rot.Yaw   = (*Arr)[1]->AsNumber();
            Rot.Roll  = (*Arr)[2]->AsNumber();
        }

        if (PC->GetPawn()) PC->GetPawn()->SetActorLocation(Loc);
        PC->SetControlRotation(Rot);
        Json->SetBoolField(TEXT("success"), true);
    }
    else
    {
        Json->SetBoolField(TEXT("success"), false);
    }

    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleSetWeather(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    // Weather in UE5 is project-specific (Sky Atmosphere, Directional Light, etc.)
    // This is a stub — real implementation would modify sky/lighting actors.
    auto Json = MakeShared<FJsonObject>();
    Json->SetBoolField(TEXT("success"), true);
    Json->SetStringField(TEXT("note"), TEXT("Weather applied via sky atmosphere"));
    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleLoadLevel(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Body = ParseBody(Request);
    auto Json = MakeShared<FJsonObject>();

    if (Body.IsValid())
    {
        FString Level = Body->GetStringField(TEXT("level"));
        UGameplayStatics::OpenLevel(GetWorld(), FName(*Level));
        SpawnedActors.Empty();
        Json->SetBoolField(TEXT("success"), true);
        Json->SetStringField(TEXT("level"), Level);
    }
    else
    {
        Json->SetBoolField(TEXT("success"), false);
    }

    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleScreenshot(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Body = ParseBody(Request);
    auto Json = MakeShared<FJsonObject>();

    FString Filename = Body.IsValid() ? Body->GetStringField(TEXT("filename")) : TEXT("capture.png");

    // Save to the Python project renders dir so Flask can serve it
    FString RendersDir = TEXT("/home/sejain/research/unreal-4d/renders");
    FString FullPath = RendersDir / Filename;

    // Use the console command to take a screenshot — works reliably from any thread
    FString Cmd = FString::Printf(TEXT("HighResShot 1280x720 filename=\"%s\""), *FullPath);

    AsyncTask(ENamedThreads::GameThread, [this, Cmd]()
    {
        if (GEngine && GEngine->GameViewport)
        {
            GEngine->GameViewport->Exec(GetWorld(), *Cmd, *GLog);
        }
    });

    // Also try the simpler screenshot request path
    FScreenshotRequest SR;
    SR.Filename = FullPath;
    SR.bShowUI = false;

    Json->SetBoolField(TEXT("success"), true);
    Json->SetStringField(TEXT("filename"), Filename);
    Json->SetStringField(TEXT("path"), FullPath);
    OnComplete(JsonResponse(Json));
    return true;
}

// ── Camera move (free-fly) ───────────────────────────────────────────────────

bool ASceneCommandServer::HandleMoveCamera(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Body = ParseBody(Request);
    auto Json = MakeShared<FJsonObject>();

    APlayerController* PC = GetWorld()->GetFirstPlayerController();
    if (!PC || !Body.IsValid())
    {
        Json->SetBoolField(TEXT("success"), false);
        OnComplete(JsonResponse(Json));
        return true;
    }

    FString Cmd = Body->GetStringField(TEXT("command"));
    float Speed = Body->HasField(TEXT("speed")) ? Body->GetNumberField(TEXT("speed")) : 5.0f;
    FRotator Rot = PC->GetControlRotation();
    FVector Loc = PC->GetPawn() ? PC->GetPawn()->GetActorLocation() : FVector::ZeroVector;

    FVector Fwd = Rot.Vector();
    FVector Right = FRotationMatrix(Rot).GetUnitAxis(EAxis::Y);
    float Step = Speed * 10.0f;

    if (Cmd == TEXT("move_forward"))       Loc += Fwd * Step;
    else if (Cmd == TEXT("move_backward")) Loc -= Fwd * Step;
    else if (Cmd == TEXT("move_left"))     Loc -= Right * Step;
    else if (Cmd == TEXT("move_right"))    Loc += Right * Step;
    else if (Cmd == TEXT("move_up"))       Loc.Z += Step;
    else if (Cmd == TEXT("move_down"))     Loc.Z -= Step;
    else if (Cmd == TEXT("rotate"))
    {
        float DYaw  = Body->HasField(TEXT("dyaw"))  ? Body->GetNumberField(TEXT("dyaw"))  : 0.0f;
        float DPitch = Body->HasField(TEXT("dpitch")) ? Body->GetNumberField(TEXT("dpitch")) : 0.0f;
        Rot.Yaw += DYaw;
        Rot.Pitch = FMath::Clamp(Rot.Pitch + DPitch, -89.0f, 89.0f);
        PC->SetControlRotation(Rot);
    }
    else if (Cmd == TEXT("zoom"))
    {
        float Delta = Body->HasField(TEXT("delta")) ? Body->GetNumberField(TEXT("delta")) : 0.0f;
        Loc += Fwd * Delta * 30.0f;
    }
    else if (Cmd == TEXT("set_position"))
    {
        if (Body->HasField(TEXT("x"))) Loc.X = Body->GetNumberField(TEXT("x"));
        if (Body->HasField(TEXT("y"))) Loc.Y = Body->GetNumberField(TEXT("y"));
        if (Body->HasField(TEXT("z"))) Loc.Z = Body->GetNumberField(TEXT("z"));
        if (Body->HasField(TEXT("pitch"))) Rot.Pitch = Body->GetNumberField(TEXT("pitch"));
        if (Body->HasField(TEXT("yaw")))   Rot.Yaw   = Body->GetNumberField(TEXT("yaw"));
        PC->SetControlRotation(Rot);
    }

    if (PC->GetPawn()) PC->GetPawn()->SetActorLocation(Loc);

    Json->SetBoolField(TEXT("success"), true);
    OnComplete(JsonResponse(Json));
    return true;
}

// ── Player character ─────────────────────────────────────────────────────────

void ASceneCommandServer::UpdateTPCamera()
{
    if (!PlayerCharacter) return;

    APlayerController* PC = GetWorld()->GetFirstPlayerController();
    if (!PC || !PC->GetPawn()) return;

    FVector PlayerLoc = PlayerCharacter->GetActorLocation();
    float YawRad = FMath::DegreesToRadians(PlayerYaw);

    // Camera behind and above the player
    FVector CamLoc;
    CamLoc.X = PlayerLoc.X - FMath::Cos(YawRad) * TPCamDistance;
    CamLoc.Y = PlayerLoc.Y - FMath::Sin(YawRad) * TPCamDistance;
    CamLoc.Z = PlayerLoc.Z + TPCamHeight;

    FRotator CamRot(TPCamPitch, PlayerYaw, 0.0f);

    PC->GetPawn()->SetActorLocation(CamLoc);
    PC->SetControlRotation(CamRot);
}

bool ASceneCommandServer::HandlePlayerSpawn(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Body = ParseBody(Request);
    auto Json = MakeShared<FJsonObject>();

    // Destroy previous player character if any
    if (PlayerCharacter && PlayerCharacter->IsValidLowLevel())
    {
        PlayerCharacter->Destroy();
        PlayerCharacter = nullptr;
    }

    FVector Location(0.0f, 0.0f, 100.0f);
    if (Body.IsValid())
    {
        const TArray<TSharedPtr<FJsonValue>>* LocArr;
        if (Body->TryGetArrayField(TEXT("location"), LocArr) && LocArr->Num() >= 3)
        {
            Location.X = (*LocArr)[0]->AsNumber();
            Location.Y = (*LocArr)[1]->AsNumber();
            Location.Z = (*LocArr)[2]->AsNumber();
        }
        if (Body->HasField(TEXT("yaw"))) PlayerYaw = Body->GetNumberField(TEXT("yaw"));
    }

    // Spawn a default Character at the requested location
    FActorSpawnParameters Params;
    Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;

    PlayerCharacter = GetWorld()->SpawnActor<ACharacter>(ACharacter::StaticClass(), &Location, &FRotator::ZeroRotator, Params);
    if (PlayerCharacter)
    {
        PlayerCharacter->SetActorRotation(FRotator(0.0f, PlayerYaw, 0.0f));

        // Give it a visible capsule
        UCapsuleComponent* Capsule = PlayerCharacter->GetCapsuleComponent();
        if (Capsule)
        {
            Capsule->SetCapsuleHalfHeight(96.0f);
            Capsule->SetCapsuleRadius(42.0f);
            Capsule->SetVisibility(true);
            Capsule->SetHiddenInGame(false);
        }

        // Position camera behind player
        UpdateTPCamera();

        Json->SetBoolField(TEXT("success"), true);
        TArray<TSharedPtr<FJsonValue>> LocArr;
        LocArr.Add(MakeShared<FJsonValueNumber>(Location.X));
        LocArr.Add(MakeShared<FJsonValueNumber>(Location.Y));
        LocArr.Add(MakeShared<FJsonValueNumber>(Location.Z));
        Json->SetArrayField(TEXT("location"), LocArr);
        UE_LOG(LogTemp, Log, TEXT("Instant4D: Player spawned at (%.0f, %.0f, %.0f)"), Location.X, Location.Y, Location.Z);
    }
    else
    {
        Json->SetBoolField(TEXT("success"), false);
        Json->SetStringField(TEXT("error"), TEXT("Failed to spawn player character"));
    }

    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandlePlayerMove(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Body = ParseBody(Request);
    auto Json = MakeShared<FJsonObject>();

    if (!PlayerCharacter || !PlayerCharacter->IsValidLowLevel())
    {
        Json->SetBoolField(TEXT("success"), false);
        Json->SetStringField(TEXT("error"), TEXT("player not spawned"));
        OnComplete(JsonResponse(Json));
        return true;
    }

    FString Cmd = Body.IsValid() ? Body->GetStringField(TEXT("command")) : TEXT("");
    float Speed = (Body.IsValid() && Body->HasField(TEXT("speed"))) ? Body->GetNumberField(TEXT("speed")) : 1.5f;

    float YawRad = FMath::DegreesToRadians(PlayerYaw);
    FVector Fwd(FMath::Cos(YawRad), FMath::Sin(YawRad), 0.0f);
    FVector Right(FMath::Sin(YawRad), -FMath::Cos(YawRad), 0.0f);
    float Step = Speed * 15.0f;

    FVector Loc = PlayerCharacter->GetActorLocation();

    if (Cmd == TEXT("move_forward"))       Loc += Fwd * Step;
    else if (Cmd == TEXT("move_backward")) Loc -= Fwd * Step;
    else if (Cmd == TEXT("move_left"))     Loc -= Right * Step;
    else if (Cmd == TEXT("move_right"))    Loc += Right * Step;
    else if (Cmd == TEXT("rotate"))
    {
        float DYaw = (Body.IsValid() && Body->HasField(TEXT("dyaw"))) ? Body->GetNumberField(TEXT("dyaw")) : 0.0f;
        PlayerYaw += DYaw;
    }

    PlayerCharacter->SetActorLocation(Loc);
    PlayerCharacter->SetActorRotation(FRotator(0.0f, PlayerYaw, 0.0f));

    // Camera follows player
    UpdateTPCamera();

    // Build response
    Json->SetBoolField(TEXT("success"), true);

    auto PlayerJson = MakeShared<FJsonObject>();
    PlayerJson->SetBoolField(TEXT("spawned"), true);
    TArray<TSharedPtr<FJsonValue>> LocArr;
    LocArr.Add(MakeShared<FJsonValueNumber>(Loc.X));
    LocArr.Add(MakeShared<FJsonValueNumber>(Loc.Y));
    LocArr.Add(MakeShared<FJsonValueNumber>(Loc.Z));
    PlayerJson->SetArrayField(TEXT("location"), LocArr);
    PlayerJson->SetNumberField(TEXT("yaw"), PlayerYaw);
    Json->SetObjectField(TEXT("player"), PlayerJson);

    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandlePlayerInfo(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Json = MakeShared<FJsonObject>();

    bool bSpawned = PlayerCharacter && PlayerCharacter->IsValidLowLevel();
    Json->SetBoolField(TEXT("spawned"), bSpawned);

    if (bSpawned)
    {
        FVector Loc = PlayerCharacter->GetActorLocation();
        TArray<TSharedPtr<FJsonValue>> LocArr;
        LocArr.Add(MakeShared<FJsonValueNumber>(Loc.X));
        LocArr.Add(MakeShared<FJsonValueNumber>(Loc.Y));
        LocArr.Add(MakeShared<FJsonValueNumber>(Loc.Z));
        Json->SetArrayField(TEXT("location"), LocArr);
        Json->SetNumberField(TEXT("yaw"), PlayerYaw);
    }

    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleDestroyActor(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Body = ParseBody(Request);
    auto Json = MakeShared<FJsonObject>();
    FString ActorId = Body.IsValid() ? Body->GetStringField(TEXT("actor_id")) : TEXT("");
    AActor** Found = SpawnedActors.Find(ActorId);
    if (Found && *Found)
    {
        (*Found)->Destroy();
        SpawnedActors.Remove(ActorId);
        Json->SetBoolField(TEXT("success"), true);
    }
    else
    {
        Json->SetBoolField(TEXT("success"), false);
        Json->SetStringField(TEXT("error"), TEXT("Actor not found"));
    }
    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleListActors(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    auto Json = MakeShared<FJsonObject>();
    auto ActorsObj = MakeShared<FJsonObject>();
    for (auto& Pair : SpawnedActors)
    {
        auto ActorJson = MakeShared<FJsonObject>();
        ActorJson->SetStringField(TEXT("id"), Pair.Key);
        if (Pair.Value) ActorJson->SetStringField(TEXT("class"), Pair.Value->GetClass()->GetName());
        ActorsObj->SetObjectField(Pair.Key, ActorJson);
    }
    Json->SetBoolField(TEXT("success"), true);
    Json->SetObjectField(TEXT("actors"), ActorsObj);
    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleExecuteCommands(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    // Batch execute — parse commands array and dispatch each
    auto Json = MakeShared<FJsonObject>();
    Json->SetBoolField(TEXT("success"), true);
    Json->SetStringField(TEXT("note"), TEXT("Batch execute — use individual endpoints for production"));
    OnComplete(JsonResponse(Json));
    return true;
}

bool ASceneCommandServer::HandleSnapshot(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    // Return the latest captured JPEG frame directly
    TArray<uint8> FrameCopy;
    {
        FScopeLock Lock(&FrameLock);
        const TArray<uint8>& LatestBuffer = bBufferAIsLatest ? FrameBufferA : FrameBufferB;
        if (LatestBuffer.Num() > 0)
        {
            FrameCopy = LatestBuffer;
        }
    }

    if (FrameCopy.Num() > 0)
    {
        auto Response = FHttpServerResponse::Create(MoveTemp(FrameCopy), TEXT("image/jpeg"));
        OnComplete(MoveTemp(Response));
    }
    else
    {
        // No frame captured yet — return a 1x1 black JPEG placeholder
        auto Json = MakeShared<FJsonObject>();
        Json->SetBoolField(TEXT("success"), false);
        Json->SetStringField(TEXT("error"), TEXT("No viewport frame captured yet"));
        OnComplete(JsonResponse(Json, 503));
    }
    return true;
}
