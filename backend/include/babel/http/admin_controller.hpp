#pragma once

#include <filesystem>
#include <functional>
#include <string>

#include <drogon/HttpRequest.h>
#include <drogon/HttpResponse.h>

#include "babel/http/admin_security.hpp"
#include "babel/runtime/seed_job_runner.hpp"

namespace babel {

class AdminController final {
 public:
  using Callback = std::function<void(const drogon::HttpResponsePtr&)>;
  using CurrentStatus = std::function<Result<SeedStatusDto>()>;
  using StartSeed = std::function<Result<SeedRunId>()>;

  AdminController(AdminSecurity&, std::filesystem::path asset_directory, SeedJobRunner&);
  AdminController(AdminSecurity&, std::filesystem::path asset_directory, CurrentStatus,
                  StartSeed);

  void index(const drogon::HttpRequestPtr&, Callback) const;
  void dashboardCss(const drogon::HttpRequestPtr&, Callback) const;
  void dashboardJs(const drogon::HttpRequestPtr&, Callback) const;
  void seedStatusJs(const drogon::HttpRequestPtr&, Callback) const;
  void experimentStatusJs(const drogon::HttpRequestPtr&, Callback) const;
  void experimentDashboardJs(const drogon::HttpRequestPtr&, Callback) const;
  void seedStatus(const drogon::HttpRequestPtr&, Callback) const;
  void startSeed(const drogon::HttpRequestPtr&, Callback) const;

 private:
  void asset(std::string_view filename, std::string_view content_type, bool inject_nonce,
             Callback) const;

  AdminSecurity& security_;
  std::filesystem::path asset_directory_;
  CurrentStatus current_status_;
  StartSeed start_seed_;
};

}  // namespace babel
