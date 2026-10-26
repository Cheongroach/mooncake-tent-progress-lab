// The actual SegmentManager is used. Only the unused RPC service is omitted.
#include "tent/runtime/control_plane.h"
#include "tent/runtime/segment_registry.h"
#include "tent/runtime/platform.h"

namespace mooncake::tent {
Platform& Platform::getLoader(std::shared_ptr<Config>) { std::abort(); }
ControlService::ControlService(const std::string&, const std::string&,
                               TransferEngineImpl* impl)
    : manager_(std::make_unique<SegmentManager>(nullptr)), impl_(impl) {}
ControlService::~ControlService() = default;
void ControlClient::subscribeSegmentUpdateAsync(const std::string&,
                                                const std::string&) {}
void ControlClient::notifySegmentUpdatedAsync(
    const std::string&, const std::string&,
    const onNotifySegmentUpdateFailure&) {}
}
