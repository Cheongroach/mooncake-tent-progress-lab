// Link surface only. Every call aborts: tests must inject the upstream Vendor.
#pragma once
#include <cstdlib>
#include <cstdint>
#include <map>
#include <string>
#include <vector>
namespace hixl {
using AscendString = std::string;
using MemHandle = void*;
using TransferReq = void*;
constexpr uint32_t SUCCESS = 0;
enum MemType { MEM_HOST, MEM_DEVICE };
enum TransferOp { READ, WRITE };
enum class TransferStatus { WAITING, COMPLETED, TIMEOUT, FAILED };
struct MemDesc { uint64_t addr; size_t len; };
struct TransferOpDesc { uint64_t local_addr, remote_addr; size_t len; };
struct TransferArgs {};
class Hixl {
 public:
    uint32_t Initialize(const AscendString&, const std::map<AscendString, AscendString>&) { std::abort(); }
    void Finalize() { std::abort(); }
    uint32_t RegisterMem(const MemDesc&, MemType, MemHandle&) { std::abort(); }
    uint32_t DeregisterMem(MemHandle) { std::abort(); }
    uint32_t TransferAsync(const AscendString&, TransferOp, const std::vector<TransferOpDesc>&,
                           const TransferArgs&, TransferReq&) { std::abort(); }
    uint32_t GetTransferStatus(TransferReq, TransferStatus&) { std::abort(); }
    uint32_t Disconnect(const AscendString&, int32_t) { std::abort(); }
    uint32_t Connect(const AscendString&, int32_t) { std::abort(); }
};
}
