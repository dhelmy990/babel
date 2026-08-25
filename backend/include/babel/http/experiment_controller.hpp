#pragma once

#include <functional>
#include <string>

#include <drogon/HttpRequest.h>
#include <drogon/HttpResponse.h>

#include "babel/application/experiment_service.hpp"
#include "babel/http/admin_security.hpp"

namespace babel {

class ExperimentController final {
 public:
  using Callback = std::function<void(const drogon::HttpResponsePtr&)>;

  ExperimentController(AdminSecurity&, ExperimentService&);

  void models(const drogon::HttpRequestPtr&, Callback) const;
  void latest(const drogon::HttpRequestPtr&, Callback) const;
  void run(const drogon::HttpRequestPtr&, std::string run_id, Callback) const;
  void activity(const drogon::HttpRequestPtr&, std::string run_id, Callback) const;
  void start(const drogon::HttpRequestPtr&, Callback) const;
  void gracefulStop(const drogon::HttpRequestPtr&, std::string run_id, Callback) const;

 private:
  AdminSecurity& security_;
  ExperimentService& service_;
};

}  // namespace babel
