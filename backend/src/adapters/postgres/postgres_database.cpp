#include "babel/adapters/postgres/postgres_database.hpp"

#include <pqxx/pqxx>

namespace babel {

PostgresDatabase::PostgresDatabase(std::string connection_string)
    : connection_string_(std::move(connection_string)) {}

std::unique_ptr<pqxx::connection> PostgresDatabase::connect() const {
  return std::make_unique<pqxx::connection>(connection_string_);
}

std::string_view PostgresDatabase::connectionString() const noexcept {
  return connection_string_;
}

}  // namespace babel
