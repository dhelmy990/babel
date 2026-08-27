#include "babel/runtime/experiment_worker_http_client.hpp"

#include <array>
#include <cctype>
#include <stdexcept>
#include <utility>

#include <curl/curl.h>

namespace babel {
namespace {

bool validToken(std::string_view token) {
  return token.size() == 64 &&
         std::all_of(token.begin(), token.end(), [](unsigned char value) {
           return std::isdigit(value) != 0 || (value >= 'a' && value <= 'f');
         });
}

bool validEndpoint(std::string_view endpoint) {
  constexpr std::string_view prefix = "http://127.0.0.1:";
  if (!endpoint.starts_with(prefix) || endpoint.size() == prefix.size()) return false;
  return std::all_of(endpoint.begin() + static_cast<std::ptrdiff_t>(prefix.size()),
                     endpoint.end(), [](unsigned char value) {
                       return std::isdigit(value) != 0;
                     });
}

Result<long> curlPost(std::string_view url, std::string_view token) {
  static const auto initialized = curl_global_init(CURL_GLOBAL_DEFAULT);
  if (initialized != CURLE_OK) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::database_unavailable,
        .message = "online worker HTTP initialization failed",
    });
  }
  auto* handle = curl_easy_init();
  if (handle == nullptr) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::database_unavailable,
        .message = "online worker HTTP client could not be created",
    });
  }
  const std::string url_value(url);
  const std::string header = "X-Babel-Worker-Token: " + std::string(token);
  auto* headers = curl_slist_append(nullptr, header.c_str());
  std::array<char, CURL_ERROR_SIZE> errors{};
  curl_easy_setopt(handle, CURLOPT_URL, url_value.c_str());
  curl_easy_setopt(handle, CURLOPT_POST, 1L);
  curl_easy_setopt(handle, CURLOPT_POSTFIELDS, "");
  curl_easy_setopt(handle, CURLOPT_POSTFIELDSIZE, 0L);
  curl_easy_setopt(handle, CURLOPT_HTTPHEADER, headers);
  curl_easy_setopt(handle, CURLOPT_CONNECTTIMEOUT_MS, 750L);
  curl_easy_setopt(handle, CURLOPT_TIMEOUT_MS, 2000L);
  curl_easy_setopt(handle, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(handle, CURLOPT_PROTOCOLS_STR, "http");
  curl_easy_setopt(handle, CURLOPT_ERRORBUFFER, errors.data());
  const auto result = curl_easy_perform(handle);
  long status = 0;
  if (result == CURLE_OK) curl_easy_getinfo(handle, CURLINFO_RESPONSE_CODE, &status);
  curl_slist_free_all(headers);
  curl_easy_cleanup(handle);
  if (result != CURLE_OK) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::database_unavailable,
        .message = "online experiment worker is unavailable",
    });
  }
  return status;
}

}  // namespace

ExperimentWorkerHttpClient::ExperimentWorkerHttpClient(std::string endpoint,
                                                       std::string token, Post post)
    : endpoint_(std::move(endpoint)), token_(std::move(token)), post_(std::move(post)) {
  if (!validEndpoint(endpoint_)) {
    throw std::invalid_argument("online worker endpoint must be numeric IPv4 loopback HTTP");
  }
  if (!validToken(token_)) {
    throw std::invalid_argument("online worker token must contain 64 lowercase hex digits");
  }
  if (!post_) post_ = curlPost;
}

Result<void> ExperimentWorkerHttpClient::command(ExperimentRunId run_id,
                                                 std::string_view action) {
  const auto result = post_(endpoint_ + "/v1/runs/" + run_id.value + "/" +
                                std::string(action),
                            token_);
  if (!result) return tl::make_unexpected(result.error());
  if (*result < 200 || *result >= 300) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::database_unavailable,
        .message = "online experiment worker rejected the command",
    });
  }
  return {};
}

Result<void> ExperimentWorkerHttpClient::start(ExperimentRunId run_id) {
  return command(std::move(run_id), "start");
}

Result<void> ExperimentWorkerHttpClient::requestGracefulStop(ExperimentRunId run_id) {
  return command(std::move(run_id), "graceful-stop");
}

}  // namespace babel
