// Declarations for CPU-only vendor-injection tests, never hardware builds.
#pragma once
#include <cstddef>
#include <cstdint>
extern "C" {
using aclError = int32_t;
using aclrtContext = void*;
using aclrtStream = void*;
constexpr aclError ACL_ERROR_NONE = 0, ACL_SUCCESS = 0;
constexpr uint32_t ACL_STREAM_FAST_LAUNCH = 1, ACL_STREAM_FAST_SYNC = 2;
enum aclrtMemMallocPolicy { ACL_MEM_MALLOC_HUGE_FIRST };
enum aclrtMemLocationType { ACL_MEM_LOCATION_TYPE_HOST, ACL_MEM_LOCATION_TYPE_DEVICE };
enum aclrtMemcpyKind {
    ACL_MEMCPY_HOST_TO_HOST, ACL_MEMCPY_HOST_TO_DEVICE,
    ACL_MEMCPY_DEVICE_TO_HOST, ACL_MEMCPY_DEVICE_TO_DEVICE, ACL_MEMCPY_DEFAULT
};
struct aclrtPtrAttributes {
    struct { uint32_t id; aclrtMemLocationType type; } location;
};
aclError aclrtGetDevice(int*);
aclError aclrtGetDeviceCount(uint32_t*);
aclError aclrtSetDevice(int);
aclError aclrtGetCurrentContext(aclrtContext*);
aclError aclrtSetCurrentContext(aclrtContext);
aclError aclrtPointerGetAttributes(const void*, aclrtPtrAttributes*);
aclError aclrtMemcpy(void*, size_t, const void*, size_t, aclrtMemcpyKind);
aclError aclrtMemcpyAsync(void*, size_t, const void*, size_t, aclrtMemcpyKind, aclrtStream);
aclError aclrtMalloc(void**, size_t, aclrtMemMallocPolicy);
aclError aclrtFree(void*);
aclError aclrtMallocHost(void**, size_t);
aclError aclrtFreeHost(void*);
aclError aclrtCreateStreamWithConfig(aclrtStream*, uint32_t, uint32_t);
aclError aclrtDestroyStream(aclrtStream);
aclError aclrtStreamAbort(aclrtStream);
aclError aclrtGetPhyDevIdByLogicDevId(int32_t, int32_t*);
const char* aclGetRecentErrMsg();
}
