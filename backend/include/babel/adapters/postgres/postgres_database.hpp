#pragma once

#include <exception>
#include <memory>
#include <string>
#include <string_view>

#include "babel/application/errors.hpp"

namespace pqxx {
class connection;
}

namespace babel {

class PostgresDatabase {
 public:
  explicit PostgresDatabase(std::string connection_string);

  [[nodiscard]] std::unique_ptr<pqxx::connection> connect() const;
  [[nodiscard]] std::string_view connectionString() const noexcept;

 private:
  std::string connection_string_;
};

[[nodiscard]] ApplicationError mapPostgresError(const std::exception& exception);

}  // namespace babel
