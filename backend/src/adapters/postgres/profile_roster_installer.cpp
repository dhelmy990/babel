#include "babel/adapters/postgres/profile_roster_installer.hpp"

#include <limits>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

#include <pqxx/pqxx>

#include "babel/adapters/postgres/postgres_database.hpp"

namespace babel {

ProfileRosterInstaller::ProfileRosterInstaller(PostgresDatabase& database) : database_(database) {}

Result<void> ProfileRosterInstaller::install(std::span<const Creator> creators) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    std::unordered_set<std::string> roster_ids;
    roster_ids.reserve(creators.size());
    for (const auto& creator : creators) {
      if (!roster_ids.insert(creator.id.value).second) {
        return invalidArgument("profile roster contains a duplicate creator ID");
      }
    }

    for (const auto& creator : creators) {
      const auto conflicts = transaction.exec(R"(
          SELECT id
          FROM creators
          WHERE id <> $1 AND (slug = $2 OR selector_order = $3)
        )",
                                              pqxx::params{creator.id.value, creator.slug,
                                                           creator.order});
      for (const auto& conflict : conflicts) {
        const auto owner_id = conflict["id"].as<std::string>();
        if (!roster_ids.contains(owner_id)) {
          return tl::make_unexpected(ApplicationError{
              .code = ErrorCode::conflict,
              .message = "an unknown creator owns desired roster metadata: " + owner_id,
          });
        }
      }
    }

    const auto maximum_order = transaction
                                   .exec("SELECT COALESCE(max(selector_order), -1) FROM creators")
                                   .one_field()
                                   .as<long long>();
    if (maximum_order >
        static_cast<long long>(std::numeric_limits<int>::max()) -
            static_cast<long long>(creators.size())) {
      return invalidArgument("profile roster staging order exceeds PostgreSQL integer range");
    }

    std::vector<std::string> temporary_slugs;
    temporary_slugs.reserve(creators.size());
    for (const auto& creator : creators) {
      const auto base_slug = "babel-stage-" + creator.id.value;
      auto temporary_slug = base_slug;
      for (std::size_t suffix = 1;
           transaction
               .exec("SELECT EXISTS(SELECT 1 FROM creators WHERE slug = $1 AND id <> $2)",
                     pqxx::params{temporary_slug, creator.id.value})
               .one_field()
               .as<bool>();
           ++suffix) {
        temporary_slug = base_slug + "-" + std::to_string(suffix);
      }
      temporary_slugs.push_back(std::move(temporary_slug));
    }

    for (std::size_t index = 0; index < creators.size(); ++index) {
      const auto& creator = creators[index];
      const auto temporary_order = maximum_order + static_cast<long long>(index) + 1;
      transaction.exec(R"(
          UPDATE creators
          SET slug = $2, selector_order = $3, updated_at = now()
          WHERE id = $1
        )",
                       pqxx::params{creator.id.value, temporary_slugs[index], temporary_order});
    }

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
    return tl::make_unexpected(mapPostgresError(exception));
  }
  return {};
}

}  // namespace babel
