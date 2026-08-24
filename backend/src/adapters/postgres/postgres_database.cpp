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

ApplicationError mapPostgresError(const std::exception& exception) {
  if (dynamic_cast<const pqxx::broken_connection*>(&exception) != nullptr) {
    return ApplicationError{
        .code = ErrorCode::database_unavailable,
        .message = exception.what(),
    };
  }

  if (const auto* sql_error = dynamic_cast<const pqxx::sql_error*>(&exception)) {
    const auto& state = sql_error->sqlstate();
    ErrorCode code = ErrorCode::internal;
    if (state == "23505") {
      code = ErrorCode::conflict;
    } else if (state.starts_with("22") || state.starts_with("23")) {
      code = ErrorCode::invalid_argument;
    } else if (state.starts_with("08")) {
      code = ErrorCode::database_unavailable;
    }
    return ApplicationError{
        .code = code,
        .message = exception.what(),
    };
  }

  return ApplicationError{
      .code = ErrorCode::internal,
      .message = exception.what(),
  };
}

}  // namespace babel
