#pragma once

#include <cstddef>
#include <string>

#include "babel/application/dtos.hpp"

namespace babel {

inline constexpr std::size_t kMaxProfileGraphJsonBytes = 64U * 1024U * 1024U;

[[nodiscard]] std::string serializeProfileGraphJson(const ProfileGraphDto& graph);

}  // namespace babel
