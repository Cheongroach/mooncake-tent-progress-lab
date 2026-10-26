// CPU regression only: no RPC is executed by the injected-vendor tests.
#pragma once
#include <sys/stat.h>
namespace mooncake::tent {
class CoroRpcAgent {};
}
