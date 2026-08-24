#include "babel/adapters/postgres/profile_roster_installer.hpp"

#include <pqxx/pqxx>

#include "babel/adapters/postgres/postgres_database.hpp"

namespace babel {

ProfileRosterInstaller::ProfileRosterInstaller(PostgresDatabase& database) : database_(database) {}

Result<void> ProfileRosterInstaller::install(std::span<const Creator> creators) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    for (const auto& creator : creators) {
      const auto kind = creator.kind == CreatorKind::personal ? "personal" : "generated";
      transaction.exec(R"(
          INSERT INTO creators(id, slug, display_name, profile_color, profile_kind, selector_order)
          VALUES ($1, $2, $3, $4, $5, $6)
          ON CONFLICT (id) DO UPDATE SET
            slug = EXCLUDED.slug,
            display_name = EXCLUDED.display_name,
            profile_color = EXCLUDED.profile_color,
            profile_kind = EXCLUDED.profile_kind,
            selector_order = EXCLUDED.selector_order,
            updated_at = now()
        )",
                       pqxx::params{creator.id.value, creator.slug, creator.display_name,
                                    creator.color, kind, creator.order});
    }
    transaction.commit();
  } catch (const std::exception& exception) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::database_unavailable,
        .message = exception.what(),
    });
  }
  return {};
}

}  // namespace babel
