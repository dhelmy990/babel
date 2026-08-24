#pragma once

#include <array>
#include <cctype>
#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <openssl/evp.h>

#include "babel/application/errors.hpp"

namespace babel {

namespace detail {

inline Result<std::string> normalizedUuid(std::string_view input) {
  constexpr std::array<std::size_t, 4> dash_positions{8, 13, 18, 23};
  if (input.size() != 36) {
    return invalidArgument("UUID must use canonical 8-4-4-4-12 form");
  }

  std::string normalized;
  normalized.reserve(input.size());
  for (std::size_t index = 0; index < input.size(); ++index) {
    const bool is_dash = dash_positions[0] == index || dash_positions[1] == index ||
                         dash_positions[2] == index || dash_positions[3] == index;
    if (is_dash) {
      if (input[index] != '-') {
        return invalidArgument("UUID must use canonical 8-4-4-4-12 form");
      }
      normalized.push_back('-');
      continue;
    }

    const auto character = static_cast<unsigned char>(input[index]);
    if (!std::isxdigit(character)) {
      return invalidArgument("UUID contains a non-hexadecimal character");
    }
    normalized.push_back(static_cast<char>(std::tolower(character)));
  }

  return normalized;
}

inline Result<std::string> uuidV5(std::string_view name) {
  constexpr std::array<unsigned char, 16> namespace_id{
      0x6d, 0xb4, 0x3f, 0x2d, 0xa1, 0xdc, 0x5d, 0x73,
      0x9a, 0xeb, 0x9b, 0x9d, 0x6d, 0x79, 0xd7, 0x2b,
  };

  std::vector<unsigned char> bytes(namespace_id.begin(), namespace_id.end());
  bytes.insert(bytes.end(), name.begin(), name.end());

  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int digest_size = 0;
  if (EVP_Digest(bytes.data(), bytes.size(), digest.data(), &digest_size, EVP_sha1(), nullptr) !=
          1 ||
      digest_size < 16) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "OpenSSL could not create a UUID v5 digest",
    });
  }

  digest[6] = static_cast<unsigned char>((digest[6] & 0x0fU) | 0x50U);
  digest[8] = static_cast<unsigned char>((digest[8] & 0x3fU) | 0x80U);

  constexpr char hex[] = "0123456789abcdef";
  std::string value;
  value.reserve(36);
  for (std::size_t index = 0; index < 16; ++index) {
    if (index == 4 || index == 6 || index == 8 || index == 10) {
      value.push_back('-');
    }
    value.push_back(hex[(digest[index] >> 4U) & 0x0fU]);
    value.push_back(hex[digest[index] & 0x0fU]);
  }
  return value;
}

template <typename Derived>
class UuidId {
 public:
  [[nodiscard]] static Result<Derived> parse(std::string_view input) {
    auto normalized = normalizedUuid(input);
    if (!normalized) {
      return tl::make_unexpected(normalized.error());
    }
    return Derived{std::move(normalized.value())};
  }

  [[nodiscard]] static Result<Derived> v5(std::string_view name) {
    auto generated = uuidV5(name);
    if (!generated) {
      return tl::make_unexpected(generated.error());
    }
    return Derived{std::move(generated.value())};
  }

  [[nodiscard]] std::string_view value() const noexcept { return value_; }

  friend bool operator==(const UuidId&, const UuidId&) = default;

 protected:
  explicit UuidId(std::string value) : value_(std::move(value)) {}

 private:
  std::string value_;
};

}  // namespace detail

class CreatorId final : public detail::UuidId<CreatorId> {
 public:
  using UuidId::parse;
  using UuidId::v5;

 private:
  friend class detail::UuidId<CreatorId>;
  explicit CreatorId(std::string value) : UuidId(std::move(value)) {}
};

class BabelId final : public detail::UuidId<BabelId> {
 public:
  using UuidId::parse;
  using UuidId::v5;

 private:
  friend class detail::UuidId<BabelId>;
  explicit BabelId(std::string value) : UuidId(std::move(value)) {}
};

class EdgeId final : public detail::UuidId<EdgeId> {
 public:
  using UuidId::parse;
  using UuidId::v5;

 private:
  friend class detail::UuidId<EdgeId>;
  explicit EdgeId(std::string value) : UuidId(std::move(value)) {}
};

class SeedRunId final : public detail::UuidId<SeedRunId> {
 public:
  using UuidId::parse;
  using UuidId::v5;

 private:
  friend class detail::UuidId<SeedRunId>;
  explicit SeedRunId(std::string value) : UuidId(std::move(value)) {}
};

class SeedAssignmentId final : public detail::UuidId<SeedAssignmentId> {
 public:
  using UuidId::parse;
  using UuidId::v5;

 private:
  friend class detail::UuidId<SeedAssignmentId>;
  explicit SeedAssignmentId(std::string value) : UuidId(std::move(value)) {}
};

class WikipediaPageId {
 public:
  [[nodiscard]] static Result<WikipediaPageId> fromInt(std::int64_t value) {
    if (value <= 0) {
      return invalidArgument("Wikipedia page ID must be positive");
    }
    return WikipediaPageId{value};
  }

  [[nodiscard]] std::int64_t value() const noexcept { return value_; }

  friend bool operator==(const WikipediaPageId&, const WikipediaPageId&) = default;

 private:
  explicit WikipediaPageId(std::int64_t value) : value_(value) {}

  std::int64_t value_;
};

}  // namespace babel

namespace std {

template <>
struct hash<babel::CreatorId> {
  std::size_t operator()(const babel::CreatorId& id) const noexcept {
    return hash<std::string_view>{}(id.value());
  }
};

template <>
struct hash<babel::BabelId> {
  std::size_t operator()(const babel::BabelId& id) const noexcept {
    return hash<std::string_view>{}(id.value());
  }
};

template <>
struct hash<babel::EdgeId> {
  std::size_t operator()(const babel::EdgeId& id) const noexcept {
    return hash<std::string_view>{}(id.value());
  }
};

template <>
struct hash<babel::SeedRunId> {
  std::size_t operator()(const babel::SeedRunId& id) const noexcept {
    return hash<std::string_view>{}(id.value());
  }
};

template <>
struct hash<babel::SeedAssignmentId> {
  std::size_t operator()(const babel::SeedAssignmentId& id) const noexcept {
    return hash<std::string_view>{}(id.value());
  }
};

}  // namespace std
