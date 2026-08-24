#pragma once

#include <chrono>
#include <optional>
#include <string>
#include <utility>

#include <tl/expected.hpp>

namespace babel {

enum class ErrorCode {
  invalid_argument,
  not_found,
  conflict,
  database_unavailable,
  wikipedia_unavailable,
  wikipedia_not_found,
  sanitizer_rejected,
  invalid_legacy_file,
  internal,
};

struct ApplicationError {
  ErrorCode code;
  std::string message;
  std::optional<std::chrono::milliseconds> retry_after{};

  friend bool operator==(const ApplicationError&, const ApplicationError&) = default;
};

template <typename T>
using Result = tl::expected<T, ApplicationError>;

inline tl::unexpected<ApplicationError> invalidArgument(std::string message) {
  return tl::make_unexpected(
      ApplicationError{.code = ErrorCode::invalid_argument, .message = std::move(message)});
}

}  // namespace babel
