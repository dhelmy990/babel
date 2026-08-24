#include "babel/adapters/postgres/postgres_repositories.hpp"

#include <algorithm>
#include <cctype>
#include <string>

#include <boost/uuid/random_generator.hpp>
#include <boost/uuid/uuid_io.hpp>
#include <pqxx/pqxx>

#include "babel/adapters/postgres/postgres_database.hpp"

namespace babel {
namespace {

Creator creatorFromRow(const pqxx::row& row) {
  return Creator{
      .id = CreatorId::parse(row["id"].as<std::string>()).value(),
      .slug = row["slug"].as<std::string>(),
      .display_name = row["display_name"].as<std::string>(),
      .color = row["profile_color"].as<std::string>(),
      .kind = row["profile_kind"].as<std::string>() == "personal" ? CreatorKind::personal
                                                                  : CreatorKind::generated,
      .order = row["selector_order"].as<int>(),
  };
}

ProfileSummaryDto summaryFromCreator(const Creator& creator) {
  return ProfileSummaryDto{
      .id = creator.id,
      .display_name = creator.display_name,
      .color = creator.color,
      .order = creator.order,
  };
}

Babel babelFromRow(const pqxx::row& row) {
  return Babel{
      .id = BabelId::parse(row["id"].as<std::string>()).value(),
      .owner_id = CreatorId::parse(row["owner_id"].as<std::string>()).value(),
      .title = row["title"].as<std::string>(),
      .content_html = row["content_html"].as<std::string>(),
      .color = row["color"].as<std::string>(),
      .content_revision = row["content_revision"].as<std::uint64_t>(),
      .content_hash = row["content_hash"].as<std::string>(),
  };
}

std::string seedRunStateName(SeedRunState state) {
  switch (state) {
    case SeedRunState::queued:
      return "queued";
    case SeedRunState::running:
      return "running";
    case SeedRunState::completed:
      return "completed";
    case SeedRunState::completed_with_errors:
      return "completed_with_errors";
    case SeedRunState::failed:
      return "failed";
    case SeedRunState::interrupted:
      return "interrupted";
  }
  return "failed";
}

SeedRunState seedRunStateFromName(std::string_view state) {
  if (state == "queued") return SeedRunState::queued;
  if (state == "running") return SeedRunState::running;
  if (state == "completed") return SeedRunState::completed;
  if (state == "completed_with_errors") return SeedRunState::completed_with_errors;
  if (state == "interrupted") return SeedRunState::interrupted;
  return SeedRunState::failed;
}

std::string seedItemStateName(SeedItemState state) {
  switch (state) {
    case SeedItemState::pending:
      return "pending";
    case SeedItemState::resolving:
      return "resolving";
    case SeedItemState::importing:
      return "importing";
    case SeedItemState::imported:
      return "imported";
    case SeedItemState::skipped:
      return "skipped";
    case SeedItemState::failed:
      return "failed";
  }
  return "failed";
}

std::string errorCodeName(ErrorCode code) {
  switch (code) {
    case ErrorCode::invalid_argument:
      return "invalid_argument";
    case ErrorCode::not_found:
      return "not_found";
    case ErrorCode::conflict:
      return "conflict";
    case ErrorCode::database_unavailable:
      return "database_unavailable";
    case ErrorCode::wikipedia_unavailable:
      return "wikipedia_unavailable";
    case ErrorCode::wikipedia_not_found:
      return "wikipedia_not_found";
    case ErrorCode::sanitizer_rejected:
      return "sanitizer_rejected";
    case ErrorCode::invalid_legacy_file:
      return "invalid_legacy_file";
    case ErrorCode::internal:
      return "internal";
  }
  return "internal";
}

SeedStatusDto seedStatusFromRow(const pqxx::row& row) {
  const auto run_id = SeedRunId::parse(row["id"].as<std::string>()).value();
  return SeedStatusDto{
      .kind = SeedStatusKind::persisted,
      .run_id = run_id,
      .run_state = seedRunStateFromName(row["state"].as<std::string>()),
      .total = row["total"].as<std::size_t>(),
      .imported = row["imported"].as<std::size_t>(),
      .skipped = row["skipped"].as<std::size_t>(),
      .failed = row["failed"].as<std::size_t>(),
  };
}

constexpr auto kSeedStatusQuery = R"(
  SELECT r.id, r.state, r.total,
         count(i.*) FILTER (WHERE i.state = 'imported') AS imported,
         count(i.*) FILTER (WHERE i.state = 'skipped') AS skipped,
         count(i.*) FILTER (WHERE i.state = 'failed') AS failed
  FROM seed_runs r
  LEFT JOIN seed_run_items i ON i.seed_run_id = r.id
  WHERE r.id = $1
  GROUP BY r.id, r.state, r.total
)";

constexpr std::string_view kPersonalCreatorId = "4b98a489-2eef-5cf8-90db-e21c5f7e067c";

bool validSha256(std::string_view digest) {
  return digest.size() == 64 &&
         std::all_of(digest.begin(), digest.end(), [](unsigned char character) {
           return std::isdigit(character) || (character >= 'a' && character <= 'f');
         });
}

constexpr auto kCreatorColumns =
    "id, slug, display_name, profile_color, profile_kind, selector_order";

}  // namespace

PostgresCreatorRepository::PostgresCreatorRepository(PostgresDatabase& database)
    : database_(database) {}

Result<bool> PostgresCreatorRepository::exists(CreatorId id) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    return transaction
        .exec("SELECT EXISTS(SELECT 1 FROM creators WHERE id = $1)", pqxx::params{id.value})
        .one_field()
        .as<bool>();
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<Creator> PostgresCreatorRepository::get(CreatorId id) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(
        std::string{"SELECT "} + kCreatorColumns + " FROM creators WHERE id = $1",
        pqxx::params{id.value});
    if (rows.empty()) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::not_found,
          .message = "creator not found: " + id.value,
      });
    }
    return creatorFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<std::vector<Creator>> PostgresCreatorRepository::listOrdered() {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(std::string{"SELECT "} + kCreatorColumns +
                                       " FROM creators ORDER BY selector_order");
    std::vector<Creator> creators;
    creators.reserve(rows.size());
    for (const auto& row : rows) {
      creators.push_back(creatorFromRow(row));
    }
    return creators;
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

PostgresGraphRepository::PostgresGraphRepository(PostgresDatabase& database)
    : database_(database) {}

Result<ProfileGraphDto> PostgresGraphRepository::loadGraph(CreatorId creator_id) {
  try {
    auto connection = database_.connect();
    pqxx::transaction<pqxx::isolation_level::repeatable_read, pqxx::write_policy::read_only>
        transaction(*connection);
    const auto creator_rows = transaction.exec(
        std::string{"SELECT "} + kCreatorColumns + " FROM creators WHERE id = $1",
        pqxx::params{creator_id.value});
    if (creator_rows.empty()) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::not_found,
          .message = "creator not found: " + creator_id.value,
      });
    }

    const auto creator = creatorFromRow(creator_rows.one_row());
    ProfileGraphDto graph{
        .profile = summaryFromCreator(creator),
        .babels = {},
        .edges = {},
    };
    const auto babel_rows = transaction.exec(R"(
        SELECT id, title, content_html, color, content_revision
        FROM babels
        WHERE owner_id = $1
        ORDER BY created_at, id
      )",
                                               pqxx::params{creator_id.value});
    graph.babels.reserve(babel_rows.size());
    for (const auto& row : babel_rows) {
      graph.babels.push_back(BabelDto{
          .id = BabelId::parse(row["id"].as<std::string>()).value(),
          .title = row["title"].as<std::string>(),
          .content_html = row["content_html"].as<std::string>(),
          .color = row["color"].as<std::string>(),
          .content_revision = row["content_revision"].as<std::uint64_t>(),
      });
    }

    const auto edge_rows = transaction.exec(R"(
        SELECT id, source_babel_id, target_babel_id
        FROM edges
        WHERE owner_id = $1
        ORDER BY created_at, id
      )",
                                              pqxx::params{creator_id.value});
    graph.edges.reserve(edge_rows.size());
    for (const auto& row : edge_rows) {
      graph.edges.push_back(EdgeDto{
          .id = EdgeId::parse(row["id"].as<std::string>()).value(),
          .source_id = BabelId::parse(row["source_babel_id"].as<std::string>()).value(),
          .target_id = BabelId::parse(row["target_babel_id"].as<std::string>()).value(),
      });
    }
    return graph;
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

PostgresWikipediaBabelRepository::PostgresWikipediaBabelRepository(PostgresDatabase& database)
    : database_(database) {}

Result<std::optional<Babel>> PostgresWikipediaBabelRepository::findByPage(
    CreatorId owner_id, WikipediaPageId page_id) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(R"(
        SELECT b.id, b.owner_id, b.title, b.content_html, b.color,
               b.content_revision, b.content_hash
        FROM babels b
        JOIN babel_sources s ON s.babel_id = b.id
        WHERE s.owner_id = $1 AND s.provider = 'wikipedia' AND s.external_page_id = $2
      )",
                                       pqxx::params{owner_id.value, page_id.value});
    if (rows.empty()) {
      return std::optional<Babel>{};
    }
    return std::optional<Babel>{babelFromRow(rows.one_row())};
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<void> PostgresWikipediaBabelRepository::insertWikipediaBabel(
    const Babel& babel, const BabelSource& source) {
  if (source.babel_id != babel.id || source.owner_id != babel.owner_id) {
    return invalidArgument("Wikipedia source identity must match its Babel");
  }
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    transaction.exec(R"(
        INSERT INTO babels(
          id, owner_id, title, content_html, color, content_revision, content_hash
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
      )",
                     pqxx::params{babel.id.value, babel.owner_id.value, babel.title,
                                  babel.content_html, babel.color, babel.content_revision,
                                  babel.content_hash});

    pqxx::params source_params{source.babel_id.value,
                               source.owner_id.value,
                               source.provider,
                               source.external_page_id.value,
                               source.canonical_url};
    source_params.append(source.source_revision_id);
    source_params.append(source.seed_assignment_id
                             ? std::optional<std::string>{source.seed_assignment_id->value}
                             : std::nullopt);
    source_params.append(source.declared_title);
    transaction.exec(R"(
        INSERT INTO babel_sources(
          babel_id, owner_id, provider, external_page_id, canonical_url,
          source_revision_id, seed_assignment_id, declared_title
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      )",
                     source_params);
    transaction.commit();
  } catch (const pqxx::unique_violation& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
  return {};
}

Result<void> PostgresWikipediaBabelRepository::attachSeedAssignment(
    BabelId babel_id, SeedAssignmentId assignment_id, std::string_view declared_title) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto result = transaction.exec(R"(
        UPDATE babel_sources
        SET seed_assignment_id = $2, declared_title = $3
        WHERE babel_id = $1
          AND seed_assignment_id IS NULL
      )",
                                         pqxx::params{babel_id.value, assignment_id.value,
                                                      declared_title});
    if (result.affected_rows() == 0) {
      const auto existing = transaction.exec(
          "SELECT seed_assignment_id, declared_title FROM babel_sources WHERE babel_id = $1",
          pqxx::params{babel_id.value});
      if (existing.empty()) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::not_found,
            .message = "Wikipedia source not found for Babel: " + babel_id.value,
        });
      }

      const auto row = existing.one_row();
      if (!row["seed_assignment_id"].is_null() &&
          row["seed_assignment_id"].as<std::string>() == assignment_id.value &&
          row["declared_title"].as<std::string>() == declared_title) {
        transaction.commit();
        return {};
      }
      if (!row["seed_assignment_id"].is_null()) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::conflict,
            .message = "Wikipedia source already has different seed provenance",
        });
      }
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::conflict,
          .message = "Wikipedia source seed provenance changed concurrently",
      });
    }
    transaction.commit();
  } catch (const pqxx::unique_violation& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
  return {};
}

PostgresSeedRunRepository::PostgresSeedRunRepository(PostgresDatabase& database)
    : database_(database) {}

Result<SeedRunId> PostgresSeedRunRepository::createRun(
    std::string_view manifest_version, std::span<const SeedAssignment> assignments) {
  if (manifest_version.empty()) {
    return invalidArgument("seed manifest version must not be empty");
  }
  if (assignments.empty()) {
    return invalidArgument("seed run must contain at least one assignment");
  }

  const auto generated = boost::uuids::to_string(boost::uuids::random_generator{}());
  const auto run_id = SeedRunId::parse(generated).value();
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    transaction.exec(R"(
        INSERT INTO seed_runs(id, manifest_version, state, total)
        VALUES ($1, $2, 'queued', $3)
      )",
                     pqxx::params{run_id.value, manifest_version, assignments.size()});
    for (const auto& assignment : assignments) {
      transaction.exec(R"(
          INSERT INTO seed_run_items(
            seed_run_id, seed_assignment_id, creator_id, declared_title,
            state, attempt_count
          ) VALUES ($1, $2, $3, $4, 'pending', 0)
        )",
                       pqxx::params{run_id.value, assignment.id.value,
                                    assignment.creator_id.value, assignment.declared_title});
    }
    transaction.commit();
  } catch (const pqxx::unique_violation& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  } catch (const pqxx::check_violation& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
  return run_id;
}

Result<bool> PostgresSeedRunRepository::assignmentExists(SeedAssignmentId assignment_id) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    return transaction
        .exec("SELECT EXISTS(SELECT 1 FROM babel_sources WHERE seed_assignment_id = $1)",
              pqxx::params{assignment_id.value})
        .one_field()
        .as<bool>();
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<void> PostgresSeedRunRepository::recordItemState(
    SeedRunId run_id, SeedAssignmentId assignment_id, const SeedItemUpdate& update) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    pqxx::params params{run_id.value, assignment_id.value, seedItemStateName(update.state),
                        update.attempt_count};
    params.append(update.resolved_page_id
                      ? std::optional<std::int64_t>{update.resolved_page_id->value}
                      : std::nullopt);
    params.append(update.babel_id ? std::optional<std::string>{update.babel_id->value}
                                  : std::nullopt);
    params.append(update.error ? std::optional<std::string>{errorCodeName(update.error->code)}
                               : std::nullopt);
    params.append(update.error ? std::optional<std::string>{update.error->message} : std::nullopt);
    const auto result = transaction.exec(R"(
        UPDATE seed_run_items
        SET state = $3,
            attempt_count = $4,
            resolved_page_id = $5,
            babel_id = $6,
            error_code = $7,
            error_detail = $8,
            started_at = CASE
              WHEN $3 IN ('resolving', 'importing') THEN COALESCE(started_at, now())
              ELSE started_at
            END,
            finished_at = CASE
              WHEN $3 IN ('imported', 'skipped', 'failed') THEN now()
              ELSE NULL
            END
        WHERE seed_run_id = $1 AND seed_assignment_id = $2
      )",
                                         params);
    if (result.affected_rows() == 0) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::not_found,
          .message = "seed run item not found",
      });
    }
    transaction.commit();
  } catch (const pqxx::check_violation& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  } catch (const pqxx::foreign_key_violation& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
  return {};
}

Result<void> PostgresSeedRunRepository::setRunState(SeedRunId run_id, SeedRunState state) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto state_name = seedRunStateName(state);
    const auto result = transaction.exec(R"(
        UPDATE seed_runs
        SET state = $2,
            started_at = CASE
              WHEN $2 = 'running' THEN COALESCE(started_at, now())
              ELSE started_at
            END,
            finished_at = CASE
              WHEN $2 IN ('completed', 'completed_with_errors', 'failed', 'interrupted') THEN now()
              ELSE NULL
            END
        WHERE id = $1
      )",
                                         pqxx::params{run_id.value, state_name});
    if (result.affected_rows() == 0) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::not_found,
          .message = "seed run not found: " + run_id.value,
      });
    }
    transaction.commit();
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
  return {};
}

Result<SeedStatusDto> PostgresSeedRunRepository::status(SeedRunId run_id) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(kSeedStatusQuery, pqxx::params{run_id.value});
    if (rows.empty()) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::not_found,
          .message = "seed run not found: " + run_id.value,
      });
    }
    return seedStatusFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<SeedStatusDto> PostgresSeedRunRepository::latestStatus() {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto latest = transaction.exec(R"(
        SELECT id FROM seed_runs ORDER BY created_at DESC, id DESC LIMIT 1
      )");
    if (latest.empty()) {
      return SeedStatusDto{};
    }
    const auto run_id = SeedRunId::parse(latest.one_field().as<std::string>()).value();
    const auto rows = transaction.exec(kSeedStatusQuery, pqxx::params{run_id.value});
    return seedStatusFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<void> PostgresSeedRunRepository::markRunningAsInterrupted() {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    transaction.exec(R"(
        UPDATE seed_runs
        SET state = 'interrupted', finished_at = now()
        WHERE state = 'running'
      )");
    transaction.commit();
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
  return {};
}

PostgresLegacyMigrationRepository::PostgresLegacyMigrationRepository(PostgresDatabase& database)
    : database_(database) {}

Result<bool> PostgresLegacyMigrationRepository::digestExists(std::string_view sha256) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    return transaction
        .exec("SELECT EXISTS(SELECT 1 FROM legacy_migrations WHERE source_sha256 = $1)",
              pqxx::params{sha256})
        .one_field()
        .as<bool>();
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<bool> PostgresLegacyMigrationRepository::importPersonalGraph(
    std::string_view sha256, std::span<const Babel> babels, std::span<const Edge> edges) {
  if (!validSha256(sha256)) {
    return invalidArgument("legacy source digest must be 64 lowercase hexadecimal characters");
  }
  const auto wrong_babel_owner = std::find_if(babels.begin(), babels.end(), [](const Babel& babel) {
    return babel.owner_id.value != kPersonalCreatorId;
  });
  const auto wrong_edge_owner = std::find_if(edges.begin(), edges.end(), [](const Edge& edge) {
    return edge.owner_id.value != kPersonalCreatorId;
  });
  if (wrong_babel_owner != babels.end() || wrong_edge_owner != edges.end()) {
    return invalidArgument("legacy graphs may only populate the Personal creator");
  }

  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto claimed = transaction.exec(R"(
        INSERT INTO legacy_migrations(
          source_sha256, creator_id, babel_count, edge_count
        ) VALUES ($1, $2, $3, $4)
        ON CONFLICT (source_sha256) DO NOTHING
        RETURNING source_sha256
      )",
                                          pqxx::params{sha256, kPersonalCreatorId, babels.size(),
                                                       edges.size()});
    if (claimed.empty()) {
      transaction.commit();
      return false;
    }
    for (const auto& babel : babels) {
      transaction.exec(R"(
          INSERT INTO babels(
            id, owner_id, title, content_html, color, content_revision, content_hash
          ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        )",
                       pqxx::params{babel.id.value, babel.owner_id.value, babel.title,
                                    babel.content_html, babel.color, babel.content_revision,
                                    babel.content_hash});
    }
    for (const auto& edge : edges) {
      transaction.exec(R"(
          INSERT INTO edges(id, owner_id, source_babel_id, target_babel_id)
          VALUES ($1, $2, $3, $4)
        )",
                       pqxx::params{edge.id.value, edge.owner_id.value, edge.source_id.value,
                                    edge.target_id.value});
    }
    transaction.commit();
    return true;
  } catch (const pqxx::check_violation& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  } catch (const pqxx::foreign_key_violation& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

}  // namespace babel
