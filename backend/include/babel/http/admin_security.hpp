#pragma once

#include <string>

#include <drogon/HttpRequest.h>

#include "babel/application/errors.hpp"

namespace babel {

class AdminSecurity final {
 public:
  AdminSecurity();
  explicit AdminSecurity(std::string nonce);

  [[nodiscard]] const std::string& nonce() const noexcept;
  [[nodiscard]] bool authorizeMutation(const drogon::HttpRequestPtr&) const noexcept;

 private:
  std::string nonce_;
};

Result<std::string> generateAdminNonce();

}  // namespace babel
