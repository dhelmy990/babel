find_package(Catch2 3 CONFIG REQUIRED)
find_package(boost_uuid CONFIG REQUIRED)
find_package(CURL CONFIG REQUIRED)
find_package(Drogon CONFIG REQUIRED)
find_package(libpqxx CONFIG REQUIRED)
find_package(LibXml2 REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(tl-expected CONFIG REQUIRED)

function(babel_enable_warnings target)
  get_target_property(target_type ${target} TYPE)
  if(target_type STREQUAL "INTERFACE_LIBRARY")
    target_compile_options(${target} INTERFACE -Wall -Wextra -Wpedantic)
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
  endif()
endfunction()

add_library(babel_domain INTERFACE)
target_include_directories(babel_domain INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_link_libraries(babel_domain INTERFACE OpenSSL::Crypto tl::expected)
babel_enable_warnings(babel_domain)

add_library(babel_application INTERFACE)
target_link_libraries(babel_application INTERFACE babel_domain)
babel_enable_warnings(babel_application)

file(GLOB_RECURSE BABEL_BACKEND_SOURCES CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp")
list(FILTER BABEL_BACKEND_SOURCES EXCLUDE REGEX "/main\\.cpp$")
file(GLOB_RECURSE BABEL_TEST_SOURCES CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_SOURCE_DIR}/tests/*.cpp")

if(BABEL_BACKEND_SOURCES)
  add_library(babel_backend STATIC ${BABEL_BACKEND_SOURCES})
  target_link_libraries(babel_backend PUBLIC
    babel_application
    Boost::uuid
    CURL::libcurl
    Drogon::Drogon
    libpqxx::pqxx
    LibXml2::LibXml2
    nlohmann_json::nlohmann_json)
else()
  add_library(babel_backend INTERFACE)
  target_link_libraries(babel_backend INTERFACE
    babel_application
    Boost::uuid
    CURL::libcurl
    Drogon::Drogon
    libpqxx::pqxx
    LibXml2::LibXml2
    nlohmann_json::nlohmann_json)
endif()
babel_enable_warnings(babel_backend)

add_executable(babel_backend_cli "${CMAKE_CURRENT_SOURCE_DIR}/src/main.cpp")
set_target_properties(babel_backend_cli PROPERTIES OUTPUT_NAME babel_backend)
target_link_libraries(babel_backend_cli PRIVATE babel_backend)
babel_enable_warnings(babel_backend_cli)

set(BABEL_MIGRATION_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/migrations" CACHE PATH
    "Runtime directory containing numbered PostgreSQL migrations")
set(BABEL_ADMIN_ASSET_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/admin" CACHE PATH
    "Runtime directory containing the loopback dashboard assets")
target_compile_definitions(babel_backend PRIVATE
  BABEL_MIGRATION_DIRECTORY="${BABEL_MIGRATION_DIRECTORY}"
  BABEL_ADMIN_ASSET_DIRECTORY="${BABEL_ADMIN_ASSET_DIRECTORY}")

if(BUILD_TESTING)
  add_custom_target(babel_tests ALL)
  include(Catch)
  foreach(BABEL_TEST_SOURCE IN LISTS BABEL_TEST_SOURCES)
    get_filename_component(BABEL_TEST_NAME "${BABEL_TEST_SOURCE}" NAME_WE)
    string(REGEX REPLACE "_test$" "" BABEL_TEST_PREFIX "${BABEL_TEST_NAME}")
    string(MAKE_C_IDENTIFIER "${BABEL_TEST_NAME}" BABEL_TEST_TARGET_SUFFIX)
    set(BABEL_TEST_TARGET "babel_test_${BABEL_TEST_TARGET_SUFFIX}")

    add_executable(${BABEL_TEST_TARGET} "${BABEL_TEST_SOURCE}")
    target_link_libraries(${BABEL_TEST_TARGET} PRIVATE babel_backend Catch2::Catch2WithMain)
    babel_enable_warnings(${BABEL_TEST_TARGET})
    add_dependencies(babel_tests ${BABEL_TEST_TARGET})
    catch_discover_tests(${BABEL_TEST_TARGET} TEST_PREFIX "${BABEL_TEST_PREFIX}.")
  endforeach()
endif()
