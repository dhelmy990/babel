#include "babel/http/admin_security.hpp"

#include <array>
#include <stdexcept>
#include <string_view>

#include <openssl/crypto.h>
#include <openssl/rand.h>

namespace babel {
namespace {

bool constantTimeEqual(std::string_view left, std::string_view right) noexcept {
  if (left.size() != right.size() || left.empty()) return false;
  return CRYPTO_memcmp(left.data(), right.data(), left.size()) == 0;
}

}  // namespace

Result<std::string> generateAdminNonce() {
  std::array<unsigned char, 32> bytes{};
  if (RAND_bytes(bytes.data(), static_cast<int>(bytes.size())) != 1) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "could not generate dashboard administration nonce",
    });
  }
  constexpr char hexadecimal[] = "0123456789abcdef";
  std::string nonce;
  nonce.reserve(bytes.size() * 2U);
  for (const auto byte : bytes) {
    nonce.push_back(hexadecimal[(byte >> 4U) & 0x0fU]);
    nonce.push_back(hexadecimal[byte & 0x0fU]);
  }
  return nonce;
}

AdminSecurity::AdminSecurity() {
  auto generated = generateAdminNonce();
  if (!generated) throw std::runtime_error(generated.error().message);
  nonce_ = std::move(*generated);
}

AdminSecurity::AdminSecurity(std::string nonce) : nonce_(std::move(nonce)) {
  if (nonce_.empty()) throw std::invalid_argument("admin nonce must not be empty");
}

const std::string& AdminSecurity::nonce() const noexcept { return nonce_; }

bool AdminSecurity::authorizeMutation(const drogon::HttpRequestPtr& request) const noexcept {
  const auto host = request->getHeader("host");
  const bool valid_host = host == "127.0.0.1:8787" || host == "localhost:8787";
  if (!valid_host) return false;
  if (request->getHeader("origin") != "http://" + host) return false;
  return constantTimeEqual(request->getHeader("x-babel-admin-nonce"), nonce_);
}

}  // namespace babel
