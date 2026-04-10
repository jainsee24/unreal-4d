// SceneCommandServer.h — HTTP server that receives scene commands from the Python backend.
// Spawns actors, sets weather, moves camera, manages player character, streams viewport.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "GameFramework/Character.h"
#include "HttpServerModule.h"
#include "IHttpRouter.h"
#include "HttpPath.h"
#include "SceneCommandServer.generated.h"

UCLASS()
class INSTANT4D_API ASceneCommandServer : public AActor
{
    GENERATED_BODY()

public:
    ASceneCommandServer();

    virtual void BeginPlay() override;
    virtual void Tick(float DeltaTime) override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Instant4D")
    int32 ServerPort = 8000;

    // Viewport capture settings
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Instant4D|Stream")
    int32 CaptureWidth = 1280;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Instant4D|Stream")
    int32 CaptureHeight = 720;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Instant4D|Stream")
    int32 JpegQuality = 75;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Instant4D|Stream")
    float CaptureTargetFPS = 30.0f;

    // Third-person camera offsets
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Instant4D|Player")
    float TPCamDistance = 400.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Instant4D|Player")
    float TPCamHeight = 200.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Instant4D|Player")
    float TPCamPitch = -20.0f;

private:
    // HTTP route handlers — scene
    bool HandleHealth(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleSceneInfo(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleLoadLevel(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleSetWeather(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleSetCamera(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleMoveCamera(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleSpawnActor(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleDestroyActor(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleListActors(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleClearScene(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleScreenshot(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleExecuteCommands(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandleSnapshot(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);  // also serves /api/stream

    // HTTP route handlers — player
    bool HandlePlayerSpawn(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandlePlayerMove(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    bool HandlePlayerInfo(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);

    // Helpers
    TSharedPtr<FJsonObject> ParseBody(const FHttpServerRequest& Request);
    TUniquePtr<FHttpServerResponse> JsonResponse(TSharedPtr<FJsonObject> Json, int32 Code = 200);
    void UpdateTPCamera();
    void CaptureViewport();

    // Route handles for cleanup
    TArray<FHttpRouteHandle> RouteHandles;

    // Spawned actors tracking
    TMap<FString, AActor*> SpawnedActors;

    // Player character state
    UPROPERTY()
    ACharacter* PlayerCharacter = nullptr;
    float PlayerYaw = 0.0f;

    // Viewport frame capture — double buffer for thread safety
    TArray<uint8> FrameBufferA;
    TArray<uint8> FrameBufferB;
    FThreadSafeBool bBufferAIsLatest = false;
    FCriticalSection FrameLock;
    float CaptureAccumulator = 0.0f;
};
