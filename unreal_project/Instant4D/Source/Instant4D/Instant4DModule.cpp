#include "Instant4DModule.h"
#include "Modules/ModuleManager.h"

#define LOCTEXT_NAMESPACE "FInstant4DModule"

void FInstant4DModule::StartupModule()
{
    UE_LOG(LogTemp, Log, TEXT("Instant4D module started"));
}

void FInstant4DModule::ShutdownModule()
{
    UE_LOG(LogTemp, Log, TEXT("Instant4D module shutdown"));
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_PRIMARY_GAME_MODULE(FInstant4DModule, Instant4D, "Instant4D");
